"""
Voice-call API — real-time, hands-free spoken conversation.

This is a *cascaded* voice pipeline that reuses the full agent brain:

    browser mic + VAD  ──audio──▶  WS /voice/{slug}/stream
        1. Groq Whisper           → user text        ──transcript──▶ browser
        2. pipeline.agent_chat(...)  (RAG + emotion + escalation + learning log)
        3. answer split into sentences → TTS each      ──audio chunks──▶ browser
        4. done {escalated, emotion}

One WebSocket = one "call". Conversation history lives for the connection; every
turn is also logged via client_store.log_interaction (session_id = the call id)
so voice conversations feed the SAME insights / knowledge-gap loop as chat.

Barge-in: while the reply is being spoken the browser keeps listening; if the
user talks, it stops local playback and sends {"type":"cancel"} so the server
stops emitting further sentences.
"""

import asyncio
import base64
import json
import re
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from config import get_settings
from logger import get_logger
from database import SessionLocal
from services import client_store
from services.tts_service import synthesize

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/voice", tags=["voice"])


# ---- Speech-to-text ---------------------------------------------------------
#
# Two engines. Gemini's multimodal audio is markedly better at Sinhala (and at
# Sinhala/English code-switching) than Whisper, and it accepts the browser's
# audio/webm;opus directly — so it's the default. Groq Whisper stays as a fast
# fallback that kicks in automatically if Gemini errors or has no key.

_STT_INSTRUCTION = (
    "Transcribe the speech in this audio verbatim. The speaker may use Sinhala, "
    "English, or a mix of both (code-switching is common). Write Sinhala words in "
    "Sinhala script and English words in the Latin alphabet, exactly as spoken. "
    "Output ONLY the words spoken — no translation, no commentary, no quotation "
    "marks. If there is no intelligible speech, output nothing."
)


async def _gemini_transcribe(audio_data: bytes, content_type: str,
                             prompt: Optional[str] = None,
                             lang: Optional[str] = None) -> Optional[str]:
    """Transcribe via Gemini's audio understanding. Returns the text, or None on
    any failure/absence-of-key so the caller can fall back to Whisper.

    `lang` ("si"/"en"/None) is the caller-selected language; when known it's used
    to steer the model instead of relying on auto-detection.
    """
    if not settings.google_api_key:
        return None
    mime = (content_type or "audio/webm").split(";")[0].strip()  # drop ";codecs=opus"
    if lang == "si":
        instruction = (
            "Transcribe the Sinhala speech in this audio verbatim. The speaker is "
            "speaking Sinhala and may mix in a few English words. Write Sinhala in "
            "Sinhala script and English words in the Latin alphabet, exactly as "
            "spoken. Output ONLY the words spoken — no translation, no commentary, "
            "no quotation marks. If there is no intelligible speech, output nothing."
        )
    elif lang == "ta":
        instruction = (
            "Transcribe the Tamil speech in this audio verbatim. The speaker is "
            "speaking Tamil and may mix in a few English words. Write Tamil in "
            "Tamil script and English words in the Latin alphabet, exactly as "
            "spoken. Output ONLY the words spoken — no translation, no commentary, "
            "no quotation marks. If there is no intelligible speech, output nothing."
        )
    elif lang == "en":
        instruction = (
            "Transcribe the English speech in this audio verbatim. Output ONLY the "
            "words spoken — no commentary, no quotation marks. If there is no "
            "intelligible speech, output nothing."
        )
    else:
        instruction = _STT_INSTRUCTION
    if prompt:
        instruction += f"\nLikely vocabulary/context: {prompt}"
    payload = {
        "contents": [{"parts": [
            {"text": instruction},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio_data).decode()}},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{settings.gemini_utility_model}:generateContent?key={settings.google_api_key}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            logger.warning(f"Gemini STT error {resp.status_code}: {resp.text[:200]}")
            return None
        parts = resp.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        logger.warning(f"Gemini STT failed: {e}")
        return None


async def _whisper_transcribe(audio_data: bytes, filename: str,
                              content_type: str, prompt: Optional[str] = None,
                              language: Optional[str] = None) -> str:
    """Transcribe raw audio bytes using Groq's Whisper API.

    `prompt` is an optional vocabulary hint (brand/domain terms) that biases
    Whisper toward the right spellings — improves accuracy on proper nouns.
    `language` overrides the configured default (e.g. the caller-selected "en"/"si").
    """
    data = {"model": settings.stt_model,
            "temperature": "0", "response_format": "json"}
    lang = language or settings.stt_language
    # Only pin a language when known; otherwise let Whisper auto-detect so both
    # Sinhala and English callers transcribe correctly.
    if lang:
        data["language"] = lang
    # The vocab hint is English domain terms; it biases Whisper toward English,
    # so skip it when we're transcribing a non-English language.
    if prompt and lang not in ("si", "ta"):
        data["prompt"] = prompt
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            files={"file": (filename, audio_data, content_type)},
            data=data,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        )
        if resp.status_code != 200:
            logger.error(f"Whisper API error: {resp.text}")
            raise HTTPException(status_code=500, detail="Speech transcription failed")
        return resp.json().get("text", "").strip()


async def transcribe_bytes(audio_data: bytes, filename: str = "audio.webm",
                           content_type: str = "audio/webm",
                           prompt: Optional[str] = None,
                           lang: Optional[str] = None) -> str:
    """Transcribe raw audio bytes, picking the best engine for the (caller-selected)
    language `lang` ("si"/"ta"/"en"/None):

      * "en"      -> Groq Whisper pinned to English: fast and accurate, no need for Gemini.
      * "si"/"ta" -> Gemini (far better Sinhala/Tamil), auto-falling back to Whisper(lang).
      * None      -> Gemini auto-detect, falling back to Whisper auto-detect.
    """
    # English: Whisper is quick and accurate — skip Gemini entirely.
    if lang == "en":
        return await _whisper_transcribe(audio_data, filename, content_type, prompt, language="en")

    # Sinhala/Tamil or unknown: prefer Gemini for accuracy, fall back to Whisper.
    if settings.stt_provider == "gemini":
        text = await _gemini_transcribe(audio_data, content_type, prompt, lang=lang)
        if text is not None:
            return text
        logger.info("Gemini STT unavailable — falling back to Groq Whisper.")
        fallback_lang = lang if lang in ("si", "ta") else settings.stt_language
        return await _whisper_transcribe(audio_data, filename, content_type, prompt,
                                         language=fallback_lang)

    return await _whisper_transcribe(
        audio_data, filename, content_type, prompt,
        language=(lang if lang in ("si", "ta") else settings.stt_language),
    )


async def transcribe_audio(audio_file: UploadFile) -> str:
    data = await audio_file.read()
    return await transcribe_bytes(
        data, audio_file.filename or "audio.webm",
        audio_file.content_type or "audio/webm",
    )


@router.post("/transcribe")
async def transcribe_endpoint(audio: UploadFile = File(...)):
    """Transcribe an audio file to text (debug/helper)."""
    return {"text": await transcribe_audio(audio)}


# ---- Helpers ----------------------------------------------------------------

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """Turn the agent's markdown answer into clean speakable text — otherwise the
    TTS reads '*', '#', backticks and link syntax out loud."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)     # code fences
    t = re.sub(r"`([^`]*)`", r"\1", t)                     # inline code
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)       # links/images -> label
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)  # headings
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)  # bullet markers
    t = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", t)    # bold/italic
    t = re.sub(r"[*_#>`]", "", t)                          # stray markdown chars
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _split_sentences(text: str, max_len: int = 240) -> list[str]:
    """Chunk an answer into sentence-sized pieces so TTS can stream: the first
    piece starts playing while later pieces are still synthesizing."""
    text = (text or "").strip()
    if not text:
        return []
    pieces = [p.strip() for p in _SENTENCE_RE.findall(text) if p.strip()]
    # Merge tiny fragments and hard-wrap anything too long for one TTS call.
    out: list[str] = []
    for p in pieces:
        if out and len(out[-1]) < 40:
            out[-1] = (out[-1] + " " + p).strip()
        elif len(p) > max_len:
            out.extend(p[i:i + max_len] for i in range(0, len(p), max_len))
        else:
            out.append(p)
    return out or [text]


def _chunk_for_tts(text: str, first_max: int = 180, rest_target: int = 400) -> list[str]:
    """Chunk the answer for STREAMING TTS: a small FIRST chunk (first sentence)
    so audio starts playing almost immediately, then the remainder grouped into
    large ~`rest_target` pieces. Same voice for every chunk (edge-tts), so there's
    no mid-answer voice switch — this just cuts time-to-first-audio.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= first_max:
        return [text]
    sents = _split_sentences(text)
    if not sents:
        return [text]
    chunks: list[str] = [sents[0]]   # first sentence -> fast start
    cur = ""
    for s in sents[1:]:
        if cur and len(cur) + len(s) + 1 > rest_target:
            chunks.append(cur.strip())
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur.strip())
    return chunks


# Voices the caller may select (edge-tts). Curated natural/expressive set.
ALLOWED_VOICES = {
    "en-US-AvaNeural", "en-US-AvaMultilingualNeural",
    "en-US-EmmaNeural", "en-US-EmmaMultilingualNeural",
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-MichelleNeural",
    # Sinhala neural voices (free via edge-tts)
    "si-LK-ThiliniNeural", "si-LK-SameeraNeural",
    # Tamil neural voices (free via edge-tts) — Sri Lankan + Indian
    "ta-LK-SaranyaNeural", "ta-LK-KumarNeural",
    "ta-IN-PallaviNeural", "ta-IN-ValluvarNeural",
}

# Sinhala (U+0D80–U+0DFF) and Tamil (U+0B80–U+0BFF) Unicode blocks — used to
# detect a Sinhala/Tamil answer and speak it with the matching voice.
_SINHALA_RE = re.compile(r"[඀-෿]")
_TAMIL_RE = re.compile(r"[஀-௿]")


def _pick_voice(text: str, requested_voice: Optional[str]) -> Optional[str]:
    """Choose the TTS voice for `text`. edge-tts voices are language-specific, so
    a Sinhala/Tamil answer must use a matching voice regardless of what was
    requested; an English answer keeps the requested/default English voice.
    gemini/openai voices handle language from the text itself, so they're left
    untouched.
    """
    if settings.tts_provider == "edge":
        if _SINHALA_RE.search(text or ""):
            return settings.edge_voice_sinhala
        if _TAMIL_RE.search(text or ""):
            return settings.edge_voice_tamil
    return requested_voice


def _get_pipeline(slug: str):
    # Imported lazily to avoid a circular import with api.clients at module load.
    from api.clients import get_pipeline_manager
    return get_pipeline_manager().get_pipeline(slug)


_DOMAIN_TERMS = {
    "telecom": "plans, data, billing, activation, coverage, SIM, roaming, top-up, upgrade",
    "university": "courses, admission, enrollment, fees, scholarships, semester, credits, campus",
    "generic": "account, billing, order, refund, support, subscription",
}


def _stt_prompt(client) -> str:
    """A short vocabulary hint for Whisper — brand name + likely domain terms."""
    name = getattr(client, "bot_name", None) or getattr(client, "name", None) or "the assistant"
    terms = _DOMAIN_TERMS.get(getattr(client, "domain", "") or "generic", _DOMAIN_TERMS["generic"])
    return f"Customer support call with {name}. Topics: {terms}."


def _log_turn(slug: str, session_id: str, user_text: str, result: dict) -> None:
    """Persist a voice turn into the same Interaction table the web chat uses."""
    db = SessionLocal()
    try:
        client_store.log_interaction(
            db,
            client_slug=slug,
            session_id=session_id,
            user_message=user_text,
            answer=result.get("answer", ""),
            used_retrieval=result.get("used_retrieval", False),
            no_kb_match=result.get("no_kb_match", False),
            emotion=result.get("emotion") or {},
            escalated=result.get("escalated", False),
        )
    except Exception as e:
        logger.warning(f"Failed to log voice interaction for {slug}: {e}")
    finally:
        db.close()


# ---- Real-time voice-call WebSocket -----------------------------------------

@router.websocket("/{slug}/stream")
async def voice_stream(websocket: WebSocket, slug: str):
    """Full-duplex voice call for one client assistant."""
    await websocket.accept()

    # Validate the client + pipeline before starting the call.
    db = SessionLocal()
    try:
        client = client_store.get_client(db, slug)
    finally:
        db.close()
    if client is None:
        await websocket.send_json({"type": "error", "message": "Assistant not found"})
        await websocket.close()
        return

    pipeline = _get_pipeline(slug)
    if pipeline is None or getattr(pipeline.llm_service, "llm", None) is None:
        await websocket.send_json({"type": "error", "message": "Assistant unavailable"})
        await websocket.close()
        return

    session_id = "voice-" + uuid.uuid4().hex[:12]
    history: list[dict] = []

    # Caller may choose a voice (?voice=en-US-AvaNeural); validated against allowlist.
    req_voice = websocket.query_params.get("voice")
    voice = req_voice if req_voice in ALLOWED_VOICES else None
    # Caller-selected spoken language (?lang=si|ta|en). Lets us pick the best STT
    # engine per language deterministically instead of auto-detecting from audio.
    req_lang = websocket.query_params.get("lang")
    lang = req_lang if req_lang in ("si", "ta", "en") else None
    stt_prompt = _stt_prompt(client)   # bias Whisper toward this client's vocabulary

    greeting = client.greeting or "Hi! How can I help you today?"
    await websocket.send_json({
        "type": "ready",
        "greeting": greeting,
        "bot_name": client.bot_name or client.name,
    })
    # Speak the greeting through the same TTS voice as answers (consistency).
    await _speak(websocket, _strip_markdown(greeting), voice=voice)
    await websocket.send_json({"type": "done"})

    audio_queue: asyncio.Queue = asyncio.Queue()
    cancel_event = asyncio.Event()
    closed = asyncio.Event()

    async def receiver():
        """Read the socket continuously: audio blobs -> queue; control JSON -> flags."""
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await audio_queue.put(msg["bytes"])
                elif msg.get("text") is not None:
                    try:
                        data = json.loads(msg["text"])
                    except Exception:
                        continue
                    t = data.get("type")
                    if t == "cancel":        # barge-in: stop speaking now
                        cancel_event.set()
                    elif t == "end":          # user hung up
                        break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"voice receiver ended: {e}")
        finally:
            closed.set()

    recv_task = asyncio.create_task(receiver())

    try:
        while not closed.is_set():
            try:
                audio = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            cancel_event.clear()
            await _handle_utterance(
                websocket, pipeline, slug, session_id, history, audio, cancel_event,
                voice, stt_prompt, lang,
            )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Voice call error for {slug}: {e}")
    finally:
        recv_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


async def _speak(websocket, text: str, cancel_event: Optional[asyncio.Event] = None,
                 voice: Optional[str] = None):
    """Synthesize `text` and stream it to the client. Few large chunks (usually
    one) => one consistent voice, no rate-limit fallback mid-answer."""
    # Pick the voice from the answer's language (Sinhala answer -> Sinhala voice).
    speak_voice = _pick_voice(text, voice)
    for seq, chunk in enumerate(_chunk_for_tts(text)):
        if cancel_event is not None and cancel_event.is_set():
            break
        audio_bytes, mime = await synthesize(chunk, speak_voice)
        if cancel_event is not None and cancel_event.is_set():
            break
        if audio_bytes:
            await websocket.send_json({"type": "audio_meta", "seq": seq, "mime": mime})
            await websocket.send_bytes(audio_bytes)
        else:
            # No server audio (browser provider or graceful fallback) — client speaks it.
            await websocket.send_json({"type": "speak_text", "seq": seq, "text": chunk})


async def _handle_utterance(websocket, pipeline, slug, session_id, history,
                            audio: bytes, cancel_event: asyncio.Event,
                            voice: Optional[str] = None, stt_prompt: Optional[str] = None,
                            lang: Optional[str] = None):
    """Process one spoken utterance: STT -> agent -> streamed TTS."""
    # 1. Transcribe (in the caller-selected language, if any)
    try:
        user_text = await transcribe_bytes(audio, prompt=stt_prompt, lang=lang)
    except Exception as e:
        logger.warning(f"Voice STT failed: {e}")
        return
    if not user_text:
        return
    # Drop pure-noise transcripts (empty once punctuation is stripped) — Whisper
    # emits stray "." / "…" for silence blips.
    if not re.sub(r"[^\w]", "", user_text):
        return
    await websocket.send_json({"type": "transcript", "role": "user", "text": user_text})
    if cancel_event.is_set():
        return

    # 2. The brain (agent_chat is sync -> run off the event loop)
    result = await run_in_threadpool(
        pipeline.agent_chat, user_text, list(history), 4, session_id=session_id
    )
    answer = result.get("answer", "") or "Sorry, I didn't catch that."
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})

    # 3. Persist the turn (same learning-loop memory as chat) — log the raw answer.
    _log_turn(slug, session_id, user_text, result)

    # Speak/display plain text (no markdown symbols read aloud).
    spoken = _strip_markdown(answer)
    emotion = (result.get("emotion") or {}).get("emotion")
    await websocket.send_json({
        "type": "answer",
        "text": spoken,
        "escalated": bool(result.get("escalated", False)),
        "emotion": emotion,
    })

    # 4. Speak it (consistent voice via _speak).
    await _speak(websocket, spoken, cancel_event, voice)

    await websocket.send_json({"type": "done"})
