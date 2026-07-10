"""
Text-to-Speech service (swappable, free-first).

The voice-call pipeline speaks the agent's answer through this service. It is
deliberately provider-swappable so we can start on a free tier and drop in a
premium voice (MAI-Voice-2 / ElevenLabs) later with no caller changes.

Providers:
  - "gemini"  : Gemini TTS via google_api_key (natural voice, free tier)   [default]
  - "openai"  : OpenAI tts-1 via openai_api_key (only when a key is set)
  - "browser" : returns no audio -> the client speaks the text itself (zero cost)

`synthesize` NEVER raises for provider/quota problems: on any failure it returns
(None, None), which the WebSocket treats as "tell the browser to speak the text."
So a live demo keeps working even if the cloud voice is unavailable.
"""

import asyncio
import base64
import re
import struct
from typing import Optional, Tuple

import httpx

from config import get_settings
from logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Sinhala (U+0D80–U+0DFF) and Tamil (U+0B80–U+0BFF) Unicode blocks — used to
# route Sinhala/Tamil answers to the more expressive Gemini voice while English
# stays on the edge voice.
_SINHALA_RE = re.compile(r"[඀-෿]")
_TAMIL_RE = re.compile(r"[஀-௿]")

GEMINI_TTS_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def _pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1,
                bits: int = 16) -> bytes:
    """Wrap raw little-endian PCM (what Gemini returns) in a WAV container so a
    browser <audio> element can play it directly."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_len = len(pcm)
    header = b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                    byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", data_len)
    return header + pcm


def _parse_rate(mime: str, default: int = 24000) -> int:
    """Pull the sample rate out of a mime like 'audio/L16;rate=24000'."""
    for part in (mime or "").split(";"):
        part = part.strip()
        if part.startswith("rate="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                pass
    return default


async def _gemini_tts(text: str, voice: str) -> Tuple[bytes, str]:
    """Synthesize with Gemini TTS. Returns (wav_bytes, 'audio/wav')."""
    url = GEMINI_TTS_URL.format(model=settings.gemini_tts_model)
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            params={"key": settings.google_api_key},
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        part = data["candidates"][0]["content"]["parts"][0]
        inline = part["inlineData"]
        pcm = base64.b64decode(inline["data"])
        rate = _parse_rate(inline.get("mimeType", ""))
        return _pcm_to_wav(pcm, sample_rate=rate), "audio/wav"


async def _edge_tts(text: str, voice: str) -> Tuple[bytes, str]:
    """Synthesize with Microsoft neural voices via edge-tts. Returns (mp3, 'audio/mpeg').

    Free, no API key. Uses Microsoft's Edge read-aloud service — ideal for demos.
    """
    import edge_tts  # lazy so a missing dep degrades to browser TTS instead of crashing

    communicate = edge_tts.Communicate(text, voice or settings.edge_voice)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    if not buf:
        raise RuntimeError("edge-tts returned no audio")
    return bytes(buf), "audio/mpeg"


async def _openai_tts(text: str, voice: str) -> Tuple[bytes, str]:
    """Synthesize with OpenAI tts-1. Returns (mp3_bytes, 'audio/mpeg')."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={"model": "tts-1", "input": text,
                  "voice": voice or "alloy", "response_format": "mp3"},
        )
        resp.raise_for_status()
        return resp.content, "audio/mpeg"


async def synthesize(text: str, voice: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Convert text to speech using the configured provider.

    Returns (audio_bytes, mime_type). Returns (None, None) when there is no
    server-side audio (provider="browser", or a graceful fallback after an
    error) — the caller should then have the client speak the text.
    """
    text = (text or "").strip()
    if not text:
        return None, None

    # Hybrid Sinhala path: speak Sinhala answers with the more expressive Gemini
    # TTS regardless of the base provider, then degrade gracefully — Gemini first,
    # the edge Sinhala voice if Gemini's free quota/errors bite, browser last. This
    # is never worse than the plain edge Sinhala voice, and better when quota allows.
    if _SINHALA_RE.search(text) and settings.tts_sinhala_provider == "gemini" \
            and settings.google_api_key:
        try:
            return await _gemini_tts(text, settings.tts_voice_sinhala)
        except Exception as e:
            logger.warning(f"Gemini Sinhala TTS failed ({e}); using edge Sinhala voice.")
            try:
                return await _edge_tts(text, settings.edge_voice_sinhala)
            except Exception as e2:
                logger.warning(f"edge Sinhala TTS also failed ({e2}); browser fallback.")
                return None, None

    # Same hybrid path for Tamil: expressive Gemini TTS, then the edge Tamil voice
    # (Saranya) if Gemini's free quota bites, browser last. Never worse than edge.
    if _TAMIL_RE.search(text) and settings.tts_tamil_provider == "gemini" \
            and settings.google_api_key:
        try:
            return await _gemini_tts(text, settings.tts_voice_tamil)
        except Exception as e:
            logger.warning(f"Gemini Tamil TTS failed ({e}); using edge Tamil voice.")
            try:
                return await _edge_tts(text, settings.edge_voice_tamil)
            except Exception as e2:
                logger.warning(f"edge Tamil TTS also failed ({e2}); browser fallback.")
                return None, None

    provider = settings.tts_provider

    try:
        if provider == "edge":
            # Retry transient edge-tts hiccups BEFORE falling back to the browser
            # voice — otherwise a single failed chunk switches the voice mid-answer.
            last_err = None
            for attempt in range(3):
                try:
                    return await _edge_tts(text, voice or settings.edge_voice)
                except Exception as e:
                    last_err = e
                    await asyncio.sleep(0.25 * (attempt + 1))
            logger.warning(f"edge-tts failed after retries ({last_err}); browser fallback")
            return None, None

        if provider == "gemini":
            if not settings.google_api_key:
                logger.warning("TTS: gemini selected but no google_api_key; falling back to browser TTS")
                return None, None
            return await _gemini_tts(text, voice or settings.tts_voice)

        if provider == "openai":
            if not settings.openai_api_key:
                logger.warning("TTS: openai selected but no openai_api_key; falling back to browser TTS")
                return None, None
            return await _openai_tts(text, voice)

        # provider == "browser" (or anything unknown): no server audio.
        return None, None

    except Exception as e:
        # Quota/network/parse errors must not break the call — degrade to browser TTS.
        logger.warning(f"TTS synthesis failed ({provider}): {e}. Falling back to browser TTS.")
        return None, None
