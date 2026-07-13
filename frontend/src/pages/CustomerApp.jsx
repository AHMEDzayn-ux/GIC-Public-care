import { useState, useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { publicGetConfig } from '../services/api';
import ChatInterface from '../components/ChatInterface';
import VoiceCall from '../components/VoiceCall';
import Icon from '../components/Icon';
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
        return (
            <div className="customer-status">
                <Icon name="search" size={28} className="customer-status-icon" />
                <span>Assistant not found.</span>
            </div>
        );
    }
    if (error) {
        return (
            <div className="customer-status">
                <Icon name="alert" size={28} className="customer-status-icon" />
                <span>Something went wrong. Please try again later.</span>
            </div>
        );
    }

    const accent = config.accent_color || '#4f46e5';
    const isGic = slug === 'gic';

    return (
        <div className="customer-app" style={{ '--accent': accent }}>
            {isGic && (
                <>
                    <div className="gov-topbar">
                        <div className="gov-topbar-inner">
                            <span className="gov-topbar-title">
                                <Icon name="landmark" size={13} />
                                Government of Sri Lanka
                            </span>
                            <div className="gov-topbar-links">
                                <a href="tel:1919" className="gov-topbar-link">
                                    <Icon name="phone" size={13} />
                                    Hotline 1919
                                </a>
                                <a
                                    href="https://gic.gov.lk"
                                    target="_blank"
                                    rel="noreferrer"
                                    className="gov-topbar-link gov-topbar-link-site"
                                >
                                    gic.gov.lk
                                </a>
                            </div>
                        </div>
                    </div>
                    <div className="gov-disclaimer-banner">
                        <Icon name="alert" size={16} />
                        <span>Not affiliated with or endorsed by the Government of Sri Lanka</span>
                    </div>
                </>
            )}
            <header className="customer-header">
                <div className="customer-header-inner">
                    <div className="customer-brand">
                        <span className={`customer-avatar${isGic ? ' customer-avatar-gov' : ''}`} aria-hidden="true">
                            <Icon name={isGic ? 'landmark' : 'sparkle'} size={20} />
                        </span>
                        <div className="customer-brand-text">
                            <h1>{config.bot_name || config.name}</h1>
                            <p>{config.name}</p>
                        </div>
                    </div>
                    <button
                        className="voice-toggle-btn"
                        onClick={() => setVoiceOpen((v) => !v)}
                        title={voiceOpen ? 'Back to chat' : 'Talk to the assistant'}
                    >
                        <Icon name={voiceOpen ? 'message' : 'mic'} size={16} />
                        {voiceOpen ? 'Chat' : 'Talk'}
                    </button>
                </div>
                {isGic && <div className="gov-ribbon" aria-hidden="true" />}
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
