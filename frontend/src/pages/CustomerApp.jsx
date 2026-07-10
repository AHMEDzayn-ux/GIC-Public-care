import { useState, useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { publicGetConfig } from '../services/api';
import ChatInterface from '../components/ChatInterface';
import VoiceCall from '../components/VoiceCall';
import './CustomerApp.css';

// Persistent per-browser conversation id so a customer's turns group together.
const getSessionId = (slug) => {
    const key = `chat_session_${slug}`;
    let sid = localStorage.getItem(key);
    if (!sid) {
        sid = 'sess-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
        localStorage.setItem(key, sid);
    }
    return sid;
};

const CustomerApp = () => {
    const { slug } = useParams();
    const [config, setConfig] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [voiceOpen, setVoiceOpen] = useState(false);

    useEffect(() => {
        publicGetConfig(slug)
            .then(setConfig)
            .catch((err) => setError(err?.response?.status === 404 ? 'notfound' : 'error'))
            .finally(() => setLoading(false));
    }, [slug]);

    if (loading) {
        return <div className="customer-status">Loading…</div>;
    }
    if (error === 'notfound') {
        return <div className="customer-status">🔍 Assistant not found.</div>;
    }
    if (error) {
        return <div className="customer-status">⚠️ Something went wrong. Please try again later.</div>;
    }

    const accent = config.accent_color || '#4f46e5';

    return (
        <div className="customer-app" style={{ '--accent': accent }}>
            <header className="customer-header" style={{ background: accent }}>
                <h1>{config.bot_name || config.name}</h1>
                <p>{config.name}</p>
                <button
                    className="voice-toggle-btn"
                    onClick={() => setVoiceOpen((v) => !v)}
                    title={voiceOpen ? 'Back to chat' : 'Talk to the assistant'}
                >
                    {voiceOpen ? '💬 Chat' : '🎙️ Talk'}
                </button>
            </header>
            <div className="customer-chat">
                {voiceOpen ? (
                    <VoiceCall slug={slug} accentColor={accent} onClose={() => setVoiceOpen(false)} />
                ) : (
                    <ChatInterface
                        clientId={slug}
                        isPublic
                        greeting={config.greeting}
                        accentColor={accent}
                        sessionId={getSessionId(slug)}
                    />
                )}
            </div>
        </div>
    );
};

export default CustomerApp;
