import { useEffect, useRef, useState, useCallback } from 'react';
import { WS_BASE } from '../services/api';
import './VoiceCall.css';

/**
 * VoiceCall — hands-free, continuous spoken conversation with the agent.
 *
 * Flow: continuous mic → self-contained energy VAD detects each utterance →
 * send it over the WebSocket → the server transcribes (Whisper), runs the same
 * agent brain, and streams the spoken reply back sentence by sentence. The user
 * can talk over the reply to interrupt it (barge-in). No buttons per turn.
 *
 * The VAD is intentionally dependency-free (Web Audio RMS + MediaRecorder) so it
 * loads instantly and can't break a demo by failing to fetch a model at runtime.
 */
// Curated natural/expressive Microsoft neural voices (must match the server allowlist).
const VOICES = [
    { id: 'en-US-AvaNeural', label: 'Ava — warm & expressive' },
    { id: 'en-US-EmmaNeural', label: 'Emma — friendly' },
    { id: 'en-US-AriaNeural', label: 'Aria — clear' },
    { id: 'en-US-JennyNeural', label: 'Jenny — neutral' },
    { id: 'en-US-MichelleNeural', label: 'Michelle — calm' },
];

const VoiceCall = ({ slug, accentColor = '#4f46e5', onClose }) => {
    const [status, setStatus] = useState('connecting'); // connecting|listening|thinking|speaking|error
    const [lines, setLines] = useState([]);             // [{role, text}]
    const [error, setError] = useState('');
    const [escalated, setEscalated] = useState(false);
    const [micLevel, setMicLevel] = useState(0);        // live input level (0–100), for feedback
    const [micMuted, setMicMuted] = useState(false);    // OS/hardware mic mute warning
    const [voice, setVoice] = useState('en-US-AvaNeural');
    const [lang, setLang] = useState('en');             // spoken language: 'en' | 'si' | 'ta'

    // Long-lived refs (not state — they must not trigger re-renders).
    const wsRef = useRef(null);
    const streamRef = useRef(null);
    const audioCtxRef = useRef(null);
    const analyserRef = useRef(null);
    const rafRef = useRef(null);
    const recorderRef = useRef(null);
    const chunksRef = useRef([]);
    const speakingUtterRef = useRef(false);   // are WE (the user) currently speaking?
    const pendingMimeRef = useRef('audio/wav');

    // Playback queue for the assistant's streamed audio.
    const queueRef = useRef([]);
    const playingRef = useRef(false);
    const turnDoneRef = useRef(false);
    const currentAudioRef = useRef(null);
    const statusRef = useRef('connecting');
    const suppressRef = useRef(true);         // half-duplex: ignore mic unless actively listening

    const setStat = (s) => {
        statusRef.current = s;
        // Only capture the user while 'listening' — never while the assistant is
        // speaking (prevents the speaker→mic feedback loop) or thinking.
        suppressRef.current = (s !== 'listening');
        setStatus(s);
    };

    // Pick a natural female English voice for the browser-TTS fallback so it
    // doesn't clash with the (female) Gemini voice used for the main audio.
    const pickVoice = () => {
        const voices = window.speechSynthesis?.getVoices?.() || [];
        if (!voices.length) return null;
        const en = voices.filter((v) => /^en(-|_|$)/i.test(v.lang));
        const pref = ['google uk english female', 'samantha', 'microsoft zira',
            'microsoft aria', 'google us english', 'female'];
        for (const name of pref) {
            const m = en.find((v) => v.name.toLowerCase().includes(name));
            if (m) return m;
        }
        return en[0] || voices[0];
    };

    const pushLine = (role, text) =>
        setLines((prev) => [...prev, { role, text }].slice(-8));

    // ---- Assistant playback -------------------------------------------------
    const resumeListening = useCallback(() => {
        if (statusRef.current !== 'error') setStat('listening');
    }, []);

    const playNext = useCallback(() => {
        if (queueRef.current.length === 0) {
            playingRef.current = false;
            if (turnDoneRef.current) resumeListening();
            return;
        }
        playingRef.current = true;
        setStat('speaking');
        const item = queueRef.current.shift();

        if (item.type === 'audio') {
            const url = URL.createObjectURL(item.blob);
            const audio = new Audio(url);
            currentAudioRef.current = audio;
            audio.onended = () => { URL.revokeObjectURL(url); currentAudioRef.current = null; playNext(); };
            audio.onerror = () => { URL.revokeObjectURL(url); currentAudioRef.current = null; playNext(); };
            audio.play().catch(() => playNext());
        } else { // browser-TTS fallback
            if (!('speechSynthesis' in window)) { playNext(); return; }
            const u = new SpeechSynthesisUtterance(item.text);
            const v = pickVoice();
            if (v) u.voice = v;
            u.onend = () => playNext();
            u.onerror = () => playNext();
            window.speechSynthesis.speak(u);
        }
    }, [resumeListening]);

    const enqueue = useCallback((item) => {
        queueRef.current.push(item);
        if (!playingRef.current) playNext();
    }, [playNext]);

    const stopPlayback = useCallback(() => {
        queueRef.current = [];
        playingRef.current = false;
        if (currentAudioRef.current) {
            try { currentAudioRef.current.pause(); } catch { /* ignore */ }
            currentAudioRef.current = null;
        }
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    }, []);

    // ---- Barge-in -----------------------------------------------------------
    const onUserSpeechStart = useCallback(() => {
        if (statusRef.current === 'speaking') {
            // Interrupt the assistant.
            stopPlayback();
            turnDoneRef.current = true;
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: 'cancel' }));
            }
        }
        setStat('listening');
    }, [stopPlayback]);

    const onUserSpeechEnd = useCallback((blob) => {
        if (blob && blob.size > 1200 && wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(blob);           // one utterance → server
            setStat('thinking');
        }
    }, []);

    // ---- Setup on mount -----------------------------------------------------
    useEffect(() => {
        let cancelled = false;

        const start = async () => {
            // 1. WebSocket
            const ws = new WebSocket(`${WS_BASE}/voice/${slug}/stream?voice=${encodeURIComponent(voice)}&lang=${encodeURIComponent(lang)}`);
            ws.binaryType = 'arraybuffer';
            wsRef.current = ws;

            ws.onmessage = (ev) => {
                if (typeof ev.data !== 'string') {
                    // binary = an assistant audio chunk (paired with the last audio_meta)
                    const blob = new Blob([ev.data], { type: pendingMimeRef.current });
                    enqueue({ type: 'audio', blob });
                    return;
                }
                let msg;
                try { msg = JSON.parse(ev.data); } catch { return; }
                switch (msg.type) {
                    case 'ready':
                        // The greeting audio is streamed by the server (same voice
                        // as answers). Just show it; playback + 'done' handle the rest.
                        pushLine('assistant', msg.greeting);
                        turnDoneRef.current = false;
                        setStat('speaking');
                        break;
                    case 'transcript':
                        pushLine('user', msg.text);
                        setStat('thinking');
                        break;
                    case 'answer':
                        pushLine('assistant', msg.text);
                        turnDoneRef.current = false;
                        if (msg.escalated) setEscalated(true);
                        break;
                    case 'audio_meta':
                        pendingMimeRef.current = msg.mime || 'audio/wav';
                        break;
                    case 'speak_text':
                        enqueue({ type: 'tts', text: msg.text });
                        break;
                    case 'done':
                        turnDoneRef.current = true;
                        if (!playingRef.current && queueRef.current.length === 0) resumeListening();
                        break;
                    case 'error':
                        setError(msg.message || 'Assistant unavailable');
                        setStat('error');
                        break;
                    default:
                        break;
                }
            };
            ws.onerror = () => { if (!cancelled) { setError('Connection failed'); setStat('error'); } };
            ws.onclose = () => { if (!cancelled && statusRef.current !== 'error') setStat('error'); };

            // 2. Microphone + energy VAD
            try {
                const stream = await navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                });
                if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
                streamRef.current = stream;

                const track = stream.getAudioTracks()[0];
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                const ctx = new AudioCtx();
                audioCtxRef.current = ctx;
                // Chrome often starts the context suspended; without this the
                // analyser reads pure silence and speech is never detected.
                if (ctx.state === 'suspended') { try { await ctx.resume(); } catch { /* ignore */ } }

                const source = ctx.createMediaStreamSource(stream);
                const analyser = ctx.createAnalyser();
                analyser.fftSize = 1024;
                analyser.smoothingTimeConstant = 0.2;
                source.connect(analyser);
                // Route through a silent sink to guarantee the graph is actively
                // pulled (some browsers won't process an analyser otherwise).
                const sink = ctx.createGain();
                sink.gain.value = 0;
                analyser.connect(sink);
                sink.connect(ctx.destination);
                analyserRef.current = analyser;

                // Warn if the OS/hardware hands us a muted mic (nothing to capture).
                const updateMute = () => setMicMuted(!!track && track.muted);
                updateMute();
                if (track) {
                    track.onmute = updateMute;
                    track.onunmute = updateMute;
                }
                // Retry resume shortly after (sticky-activation timing can vary).
                setTimeout(() => { ctx.resume().catch(() => {}); }, 300);

                runVad(stream, analyser);
            } catch (err) {
                setError('Microphone error: ' + (err?.name || '') + ' ' + (err?.message || ''));
                setStat('error');
            }
        };

        // Energy-based voice-activity detection + per-utterance recording.
        const runVad = (stream, analyser) => {
            const buf = new Uint8Array(analyser.fftSize);
            const rms = () => {
                analyser.getByteTimeDomainData(buf);
                let sum = 0;
                for (let i = 0; i < buf.length; i++) {
                    const v = (buf[i] - 128) / 128;
                    sum += v * v;
                }
                return Math.sqrt(sum / buf.length);
            };

            // Adaptive noise floor: tracks ambient level while quiet, so thresholds
            // self-tune per mic and recover after the greeting.
            let noiseFloor = 0.02;
            let phase = 'idle';     // idle | pre (tentative, capturing onset) | speech (confirmed)
            let confirmed = false;
            let loudFrames = 0;     // consecutive loud frames — sustained speech, not a tap
            let lastLoud = 0;
            let preStartAt = 0;
            let speechStartAt = 0;
            let frame = 0;
            const HANGOVER = 600;    // ms of quiet before we call the utterance done
            const PRE_MAX = 700;     // ms allowed in 'pre' without confirming -> discard
            const CONFIRM_FRAMES = 5; // sustained loud frames (~80ms) required to accept as speech
            const MIN_VOICED = 320;  // ms of actual voiced time required to send (rejects taps)

            const beginRecording = () => {
                chunksRef.current = [];
                const opts = { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000 };
                let recorder;
                try {
                    recorder = new MediaRecorder(stream, opts);
                } catch {
                    try { recorder = new MediaRecorder(stream, { audioBitsPerSecond: 128000 }); }
                    catch { recorder = new MediaRecorder(stream); }
                }
                recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
                recorder.onstop = () => {
                    const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
                    chunksRef.current = [];
                    // Only send genuine utterances with enough VOICED time (drops taps/echo).
                    if (confirmed && (lastLoud - speechStartAt) >= MIN_VOICED) onUserSpeechEnd(blob);
                };
                recorderRef.current = recorder;
                recorder.start();
            };

            const stopRecorder = () => { try { recorderRef.current?.stop(); } catch { /* ignore */ } };

            const tick = () => {
                rafRef.current = requestAnimationFrame(tick);
                const level = rms();
                frame += 1;

                // Half-duplex: while the assistant is speaking/thinking, don't listen
                // (kills the speaker→mic feedback loop). Abandon any tentative capture.
                if (suppressRef.current) {
                    if (phase !== 'idle') { phase = 'idle'; loudFrames = 0; confirmed = false; stopRecorder(); }
                    if (frame % 3 === 0) setMicLevel(0);
                    return;
                }

                if (frame % 3 === 0) setMicLevel(Math.min(100, Math.round(level * 350)));

                const now = performance.now();
                const preThresh = Math.max(0.022, noiseFloor * 2.2);   // low: catch word onset
                const confirmThresh = Math.max(0.038, noiseFloor * 3.4); // high: real speech
                const endThresh = Math.max(0.024, noiseFloor * 2.0);

                if (phase === 'idle') {
                    noiseFloor = Math.min(noiseFloor * 0.99 + level * 0.01, 0.2);
                    if (level > preThresh) {
                        // Start capturing immediately so the onset isn't clipped.
                        phase = 'pre';
                        confirmed = false;
                        loudFrames = 0;
                        preStartAt = now;
                        beginRecording();
                    }
                } else if (phase === 'pre') {
                    // Require SUSTAINED loudness to confirm — a tap is 1-2 frames and won't qualify.
                    if (level > confirmThresh) { loudFrames += 1; lastLoud = now; }
                    else { loudFrames = Math.max(0, loudFrames - 1); }
                    if (loudFrames >= CONFIRM_FRAMES) {
                        phase = 'speech';
                        confirmed = true;
                        speechStartAt = preStartAt;   // count from the onset we already captured
                        speakingUtterRef.current = true;
                        onUserSpeechStart();
                    } else if (now - preStartAt > PRE_MAX) {
                        // Never became sustained speech -> discard (tap/echo/noise blip).
                        phase = 'idle';
                        stopRecorder();
                    }
                } else { // speech
                    if (level > endThresh) lastLoud = now;
                    if (now - lastLoud > HANGOVER) {
                        phase = 'idle';
                        speakingUtterRef.current = false;
                        stopRecorder();
                    }
                }
            };
            tick();
        };

        start();

        return () => {
            cancelled = true;
            if (rafRef.current) cancelAnimationFrame(rafRef.current);
            try { recorderRef.current?.state === 'recording' && recorderRef.current.stop(); } catch { /* ignore */ }
            stopPlayback();
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                try { wsRef.current.send(JSON.stringify({ type: 'end' })); } catch { /* ignore */ }
            }
            try { wsRef.current?.close(); } catch { /* ignore */ }
            streamRef.current?.getTracks().forEach((t) => t.stop());
            try { audioCtxRef.current?.close(); } catch { /* ignore */ }
            if ('speechSynthesis' in window) window.speechSynthesis.cancel();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [slug, voice, lang]);

    const statusLabel = {
        connecting: 'Connecting…',
        listening: 'Listening — just speak',
        thinking: 'Thinking…',
        speaking: 'Speaking…',
        error: 'Call ended',
    }[status];

    return (
        <div className="voice-call" style={{ '--accent': accentColor }}>
            <div className={`voice-orb ${status}`}>
                <div className="orb-core" />
                <div className="orb-ring r1" />
                <div className="orb-ring r2" />
                <div className="orb-ring r3" />
                <span className="orb-emoji">{status === 'speaking' ? '🔊' : '🎤'}</span>
            </div>

            <div className="voice-status">{statusLabel}</div>
            {status !== 'error' && status !== 'connecting' && (
                <div className="mic-meter" title="Your microphone level">
                    <div className="mic-meter-fill" style={{ width: `${micLevel}%` }} />
                </div>
            )}
            {micMuted && <div className="voice-error">🎤 Your microphone looks muted — check your mic-mute key or OS sound settings.</div>}
            {escalated && <div className="voice-escalated">🤝 Connecting you to a human agent</div>}
            {error && <div className="voice-error">⚠️ {error}</div>}

            <div className="voice-transcript">
                {lines.map((l, i) => (
                    <div key={i} className={`vt-line ${l.role}`}>
                        <span className="vt-who">{l.role === 'user' ? 'You' : 'Agent'}</span>
                        <span className="vt-text">{l.text}</span>
                    </div>
                ))}
            </div>

            <div className="voice-lang" role="group" aria-label="Spoken language">
                <button
                    type="button"
                    className={`voice-lang-btn ${lang === 'en' ? 'active' : ''}`}
                    onClick={() => setLang('en')}
                >English</button>
                <button
                    type="button"
                    className={`voice-lang-btn ${lang === 'si' ? 'active' : ''}`}
                    onClick={() => setLang('si')}
                >සිංහල</button>
                <button
                    type="button"
                    className={`voice-lang-btn ${lang === 'ta' ? 'active' : ''}`}
                    onClick={() => setLang('ta')}
                >தமிழ்</button>
            </div>

            {lang === 'en' && (
                <div className="voice-picker">
                    <label htmlFor="voice-sel">Voice</label>
                    <select id="voice-sel" value={voice} onChange={(e) => setVoice(e.target.value)}>
                        {VOICES.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
                    </select>
                </div>
            )}

            <button className="voice-end" onClick={onClose}>✕ End call</button>
        </div>
    );
};

export default VoiceCall;
