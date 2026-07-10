import React, { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { publicChat, submitFeedback } from '../services/api';
import './ChatInterface.css';

const ChatInterface = ({ clientId, isPublic = false, greeting = '', accentColor, sessionId = null }) => {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [showSources, setShowSources] = useState(!isPublic);
    const [feedbackGiven, setFeedbackGiven] = useState({});
    const sessionRef = useRef(sessionId || 'sess-' + Math.random().toString(36).slice(2));
    const [expandedSources, setExpandedSources] = useState({});
    const [expandedAnswers, setExpandedAnswers] = useState({});
    const messagesEndRef = useRef(null);
    
    // Voice chat states
    const [isListening, setIsListening] = useState(false);
    const [isSpeaking, setIsSpeaking] = useState(false);
    const [voiceEnabled, setVoiceEnabled] = useState(false);
    const [speechSupported, setSpeechSupported] = useState(false);
    const recognitionRef = useRef(null);
    const synthRef = useRef(window.speechSynthesis);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    // Initialize speech recognition
    useEffect(() => {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            setSpeechSupported(true);
            const recognition = new SpeechRecognition();
            recognition.continuous = false;
            recognition.interimResults = false;
            recognition.lang = 'en-US';

            recognition.onstart = () => {
                setIsListening(true);
            };

            recognition.onresult = (event) => {
                const transcript = event.results[0][0].transcript;
                setInput(transcript);
                setIsListening(false);
            };

            recognition.onerror = (event) => {
                console.error('Speech recognition error:', event.error);
                setIsListening(false);
            };

            recognition.onend = () => {
                setIsListening(false);
            };

            recognitionRef.current = recognition;
        }

        return () => {
            if (recognitionRef.current) {
                recognitionRef.current.abort();
            }
            if (synthRef.current) {
                synthRef.current.cancel();
            }
        };
    }, []);

    // Toggle voice listening
    const toggleListening = () => {
        if (!speechSupported) {
            alert('Speech recognition is not supported in your browser. Please use Chrome, Edge, or Safari.');
            return;
        }

        if (isListening) {
            recognitionRef.current.stop();
        } else {
            recognitionRef.current.start();
        }
    };

    // Speak text using text-to-speech
    const speakText = (text) => {
        if (!speechSupported) return;

        // Cancel any ongoing speech
        synthRef.current.cancel();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        utterance.onstart = () => {
            setIsSpeaking(true);
        };

        utterance.onend = () => {
            setIsSpeaking(false);
        };

        utterance.onerror = () => {
            setIsSpeaking(false);
        };

        synthRef.current.speak(utterance);
    };

    // Stop speaking
    const stopSpeaking = () => {
        synthRef.current.cancel();
        setIsSpeaking(false);
    };

    // Extract short answer from full response
    const extractShortAnswer = (fullAnswer) => {
        if (!fullAnswer) return { short: '', full: fullAnswer };

        // Check if answer contains a numbered list
        const hasNumberedList = /\d+\.\s+[A-Z]/.test(fullAnswer);
        const hasBulletList = /\n\s*[-•*]\s/.test(fullAnswer);

        // If it's a list-based answer, extract intro + ALL list items as short, rest as full
        if (hasNumberedList || hasBulletList) {
            // Find the last list item by looking for the last numbered item or bullet
            let lastListItemIndex = -1;

            if (hasNumberedList) {
                // Find all numbered list items (1., 2., 3., etc.)
                const listItemMatches = [...fullAnswer.matchAll(/\n\s*\d+\.\s/g)];
                if (listItemMatches.length > 0) {
                    const lastMatch = listItemMatches[listItemMatches.length - 1];
                    // Find the end of that last list item (next paragraph break or end of string)
                    const afterLastItem = fullAnswer.substring(lastMatch.index);
                    const paragraphBreak = afterLastItem.match(/\n\s*\n(?!\s*\d+\.)/);

                    if (paragraphBreak) {
                        lastListItemIndex = lastMatch.index + paragraphBreak.index;
                    } else {
                        // No paragraph break after list, entire answer is the list
                        return { short: fullAnswer, full: '' };
                    }
                }
            }

            if (lastListItemIndex > 0) {
                // Split after the complete list
                const short = fullAnswer.substring(0, lastListItemIndex).trim();
                const full = fullAnswer.substring(lastListItemIndex).trim();
                return { short, full };
            } else {
                // Entire answer is the list
                return { short: fullAnswer, full: '' };
            }
        }

        // For non-list answers, split by sentences
        const sentences = fullAnswer.split(/(?<=[.!?])\s+(?!\d+\.)/);

        // If the answer is short (1-2 sentences or < 150 chars), treat it all as short answer
        if (sentences.length <= 2 || fullAnswer.length < 150) {
            return { short: fullAnswer, full: '' };
        }

        // Extract the first 1-2 sentences as short answer
        let shortAnswer = sentences[0];

        // If first sentence is very short (< 80 chars), include second sentence
        if (shortAnswer.length < 80 && sentences.length > 1) {
            shortAnswer += ' ' + sentences[1];
        }

        // The rest is the full description
        const usedSentences = shortAnswer.split(/(?<=[.!?])\s+(?!\d+\.)/).length;
        const remainingSentences = sentences.slice(usedSentences);
        const fullDescription = remainingSentences.join(' ');

        return {
            short: shortAnswer.trim(),
            full: fullDescription.trim()
        };
    };

    const handleSend = async () => {
        if (!input.trim() || loading) return;

        const userMessage = {
            role: 'user',
            content: input.trim(),
            timestamp: new Date().toLocaleTimeString(),
        };

        setMessages((prev) => [...prev, userMessage]);
        setInput('');
        setLoading(true);

        try {
            // Conversational chat via the scoped public endpoint
            const history = messages
                .filter((m) => m.role === 'user' || m.role === 'assistant')
                .map((m) => ({ role: m.role, content: m.content }));

            const response = await publicChat(clientId, userMessage.content, history, sessionRef.current);

            const assistantMessage = {
                role: 'assistant',
                content: response.response,
                sources: response.sources || [],
                confidence: response.confidence || 0,
                is_uncertain: response.is_uncertain || false,
                usedRetrieval: response.used_retrieval,
                escalated: response.escalated,
                interactionId: response.interaction_id,
                timestamp: new Date().toLocaleTimeString(),
            };

            setMessages((prev) => [...prev, assistantMessage]);

            // Auto-speak response if voice is enabled
            if (voiceEnabled && response.response) {
                speakText(response.response);
            }
        } catch (err) {
            const errorMessage = {
                role: 'error',
                content: 'Error: ' + err.message,
                timestamp: new Date().toLocaleTimeString(),
            };
            setMessages((prev) => [...prev, errorMessage]);
        } finally {
            setLoading(false);
        }
    };

    const handleFeedback = async (index, interactionId, rating) => {
        if (!interactionId || feedbackGiven[index]) return;
        setFeedbackGiven((prev) => ({ ...prev, [index]: rating }));
        try {
            await submitFeedback(clientId, interactionId, rating);
        } catch (err) {
            console.error('Feedback failed', err);
        }
    };

    const handleKeyPress = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const clearChat = () => {
        if (window.confirm('Clear chat history?')) {
            setMessages([]);
        }
    };

    if (!clientId) {
        return (
            <div className="chat-interface">
                <div className="no-client-selected">
                    <h3>💬 Chat Interface</h3>
                    <p>Please select a client and upload documents to start chatting</p>
                </div>
            </div>
        );
    }

    return (
        <div className="chat-interface">
            <div className="chat-header">
                <h3>💬 Chat with Documents</h3>
                <div className="chat-controls">
                    {/* Voice Controls */}
                    {speechSupported && (
                        <div className="voice-controls">
                            <button
                                className={`btn-voice-toggle ${voiceEnabled ? 'active' : ''}`}
                                onClick={() => {
                                    setVoiceEnabled(!voiceEnabled);
                                    if (voiceEnabled) stopSpeaking();
                                }}
                                title={voiceEnabled ? 'Disable voice responses' : 'Enable voice responses'}
                            >
                                {voiceEnabled ? '🔊' : '🔇'}
                            </button>
                            {isSpeaking && (
                                <button
                                    className="btn-stop-speaking"
                                    onClick={stopSpeaking}
                                    title="Stop speaking"
                                >
                                    ⏹️
                                </button>
                            )}
                        </div>
                    )}
                    
                    <label className="sources-toggle">
                        <input
                            type="checkbox"
                            checked={showSources}
                            onChange={(e) => setShowSources(e.target.checked)}
                        />
                        Show Sources
                    </label>
                    <button onClick={clearChat} className="btn-clear-chat">
                        Clear
                    </button>
                </div>
            </div>

            <div className="chat-messages">
                {messages.length === 0 ? (
                    <div className="empty-chat">
                        <p>{greeting || '👋 Start a conversation!'}</p>
                        <small>Ask a question to get started</small>
                    </div>
                ) : (
                    <>
                        {messages.map((msg, index) => (
                            <div key={index} className={`message message-${msg.role}`}>
                                <div className="message-header">
                                    <span className="message-role">
                                        {msg.role === 'user' ? '👤 You' : msg.role === 'assistant' ? '🤖 Assistant' : '⚠️ Error'}
                                    </span>
                                    <span className="message-time">{msg.timestamp}</span>
                                </div>
                                {msg.role === 'assistant' && msg.content && (
                                    <>
                                        {/* Uncertainty Warning */}
                                        {msg.is_uncertain && (
                                            <div className="uncertainty-warning">
                                                ⚠️ <strong>Limited Information:</strong> This answer may be incomplete or require human verification.
                                            </div>
                                        )}

                                        {/* Confidence Indicator - only show if confidence is meaningful */}
                                        {msg.confidence > 0 && msg.confidence < 1 && !msg.is_uncertain && (
                                            <div className={`confidence-indicator confidence-${msg.confidence >= 0.7 ? 'high' : msg.confidence >= 0.5 ? 'medium' : 'low'}`}>
                                                <span className="confidence-label">Confidence:</span>
                                                <div className="confidence-bar">
                                                    <div
                                                        className="confidence-fill"
                                                        style={{ width: `${msg.confidence * 100}%` }}
                                                    ></div>
                                                </div>
                                                <span className="confidence-value">{Math.round(msg.confidence * 100)}%</span>
                                            </div>
                                        )}

                                        {/* Full answer, rendered as markdown (bold, bullets, links) */}
                                        <div className="message-content markdown-body">
                                            <ReactMarkdown
                                                components={{
                                                    a: ({ node, ...props }) => (
                                                        <a target="_blank" rel="noreferrer" {...props} />
                                                    ),
                                                }}
                                            >
                                                {msg.content}
                                            </ReactMarkdown>
                                        </div>
                                    </>
                                )}
                                {msg.role !== 'assistant' && <div className="message-content">{msg.content}</div>}
                                {msg.sources && msg.sources.length > 0 && showSources && (
                                    <div className="message-sources">
                                        <button
                                            className="sources-toggle-btn"
                                            onClick={() => setExpandedSources(prev => ({ ...prev, [index]: !prev[index] }))}
                                        >
                                            📚 {expandedSources[index] ? 'Hide' : 'View'} Sources ({msg.sources.length})
                                        </button>
                                        {expandedSources[index] && (
                                            <div className="sources-content">
                                                {msg.sources.map((source, idx) => (
                                                    <div key={idx} className="source-item">
                                                        <div className="source-header">
                                                            <span className="citation-label">
                                                                [{source.citation_label || `Source ${idx + 1}`}]
                                                            </span>
                                                            <span className={`relevance-badge relevance-${(source.relevance || '').toLowerCase().replace(' ', '-')}`}>
                                                                {source.relevance || 'Relevant'}
                                                            </span>
                                                        </div>
                                                        <div className="source-text">{source.text}</div>
                                                        <div className="source-meta">
                                                            {source.source_file && (
                                                                <span className="source-file">
                                                                    📄 {source.source_file}
                                                                    {source.section && ` → ${source.section}`}
                                                                </span>
                                                            )}
                                                            {source.distance !== undefined && (
                                                                <span className="source-distance" title="Lower is more relevant">
                                                                    Distance: {source.distance.toFixed(3)}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                                {msg.usedRetrieval !== undefined && (
                                    <small className="retrieval-status">
                                        {msg.usedRetrieval ? '✓ Used document context' : 'ℹ️ No retrieval'}
                                    </small>
                                )}
                                {msg.escalated && (
                                    <small className="escalated-badge">🙋 Connected to a human agent</small>
                                )}
                                {msg.role === 'assistant' && msg.interactionId && (
                                    <div className="feedback-row">
                                        {feedbackGiven[index] ? (
                                            <small className="feedback-thanks">Thanks for your feedback 🙏</small>
                                        ) : (
                                            <>
                                                <button className="fb-btn" title="Helpful"
                                                    onClick={() => handleFeedback(index, msg.interactionId, 'up')}>👍</button>
                                                <button className="fb-btn" title="Not helpful"
                                                    onClick={() => handleFeedback(index, msg.interactionId, 'down')}>👎</button>
                                            </>
                                        )}
                                    </div>
                                )}
                            </div>
                        ))}
                        <div ref={messagesEndRef} />
                    </>
                )}
                {loading && (
                    <div className="message message-loading">
                        <div className="loading-dots">
                            <span></span>
                            <span></span>
                            <span></span>
                        </div>
                    </div>
                )}
            </div>

            <div className="chat-input-container">
                {speechSupported && (
                    <button
                        className={`btn-microphone ${isListening ? 'listening' : ''}`}
                        onClick={toggleListening}
                        disabled={loading}
                        title={isListening ? 'Stop listening' : 'Start voice input'}
                    >
                        {isListening ? '🎤' : '🎙️'}
                    </button>
                )}
                <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyPress={handleKeyPress}
                    placeholder={isListening ? "Listening..." : "Type your question here..."}
                    disabled={loading}
                    rows="2"
                />
                <button onClick={handleSend} disabled={!input.trim() || loading}>
                    {loading ? '⏳' : '📤'} Send
                </button>
            </div>
        </div>
    );
};

export default ChatInterface;
