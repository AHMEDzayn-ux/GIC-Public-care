import React, { useState, useRef, useEffect } from 'react';
import { API_BASE } from '../services/api';
import './VoiceChat.css';

const VoiceChat = ({ clientId = "default" }) => {
    const [isRecording, setIsRecording] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    const [isPlayingResponse, setIsPlayingResponse] = useState(false);
    const [transcription, setTranscription] = useState('');
    const [responseText, setResponseText] = useState('');
    const [error, setError] = useState('');
    const [audioLevel, setAudioLevel] = useState(0);

    const mediaRecorderRef = useRef(null);
    const audioChunksRef = useRef([]);
    const audioContextRef = useRef(null);
    const analyserRef = useRef(null);
    const animationFrameRef = useRef(null);

    // Initialize audio context for visualization
    useEffect(() => {
        audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();

        return () => {
            if (audioContextRef.current) {
                audioContextRef.current.close();
            }
            if (animationFrameRef.current) {
                cancelAnimationFrame(animationFrameRef.current);
            }
        };
    }, []);

    // Visualize audio levels while recording
    const visualizeAudio = (stream) => {
        const audioContext = audioContextRef.current;
        const analyser = audioContext.createAnalyser();
        const microphone = audioContext.createMediaStreamSource(stream);

        analyser.fftSize = 256;
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        microphone.connect(analyser);
        analyserRef.current = analyser;

        const updateLevel = () => {
            analyser.getByteFrequencyData(dataArray);
            const average = dataArray.reduce((a, b) => a + b) / bufferLength;
            setAudioLevel(average / 255 * 100);

            if (isRecording) {
                animationFrameRef.current = requestAnimationFrame(updateLevel);
            }
        };

        updateLevel();
    };

    // Start recording
    const startRecording = async () => {
        try {
            console.log('Starting recording...');
            setError('');
            setTranscription('');
            setResponseText('');

            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            console.log('Microphone access granted');

            // Visualize audio
            visualizeAudio(stream);

            const mediaRecorder = new MediaRecorder(stream, {
                mimeType: 'audio/webm;codecs=opus'
            });

            mediaRecorderRef.current = mediaRecorder;
            audioChunksRef.current = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    console.log('Audio chunk received:', event.data.size);
                    audioChunksRef.current.push(event.data);
                }
            };

            mediaRecorder.onstop = async () => {
                console.log('Recording stopped, processing...');
                // Stop all tracks
                stream.getTracks().forEach(track => track.stop());

                // Process the recorded audio
                await processRecording();
            };

            mediaRecorder.start();
            setIsRecording(true);
            console.log('Recording started');

        } catch (err) {
            console.error('Error accessing microphone:', err);
            setError('Could not access microphone. Please grant permission.');
        }
    };

    // Stop recording
    const stopRecording = () => {
        console.log('Stop recording called');
        if (mediaRecorderRef.current && isRecording) {
            mediaRecorderRef.current.stop();
            setIsRecording(false);
            setAudioLevel(0);

            if (animationFrameRef.current) {
                cancelAnimationFrame(animationFrameRef.current);
            }
            console.log('Recording stop initiated');
        }
    };

    // Process recorded audio
    const processRecording = async () => {
        try {
            setIsProcessing(true);
            console.log('Processing recording...');

            // Create audio blob
            const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
            console.log('Audio blob created, size:', audioBlob.size);

            if (audioBlob.size === 0) {
                throw new Error('No audio data recorded');
            }

            // Create form data
            const formData = new FormData();
            formData.append('audio', audioBlob, 'recording.webm');
            formData.append('client_id', clientId);

            console.log('Sending to backend...', clientId);

            // Send to backend (env-driven base URL — works in production)
            const response = await fetch(`${API_BASE}/voice/chat`, {
                method: 'POST',
                body: formData
            });

            console.log('Response status:', response.status);

            if (!response.ok) {
                const errorText = await response.text();
                console.error('Server error:', errorText);
                throw new Error(`Server error: ${response.statusText} - ${errorText}`);
            }

            // Get JSON response
            const data = await response.json();
            console.log('Response data:', data);

            setTranscription(data.transcription || 'Transcription not available');
            setResponseText(data.response || 'No response generated');

            // Use browser TTS to speak the response
            if (data.response) {
                console.log('Speaking response...');
                speakText(data.response);
            } else {
                console.warn('No response text to speak');
            }

        } catch (err) {
            console.error('Error processing recording:', err);
            setError(`Processing failed: ${err.message}`);
        } finally {
            setIsProcessing(false);
        }
    };

    // Speak text using browser TTS
    const speakText = (text) => {
        console.log('speakText called with:', text.substring(0, 100));

        if (!('speechSynthesis' in window)) {
            console.error('Speech synthesis not supported');
            setError('Text-to-speech not supported in your browser');
            return;
        }

        // Cancel any ongoing speech
        window.speechSynthesis.cancel();

        console.log('Creating speech utterance...');
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        utterance.onstart = () => {
            console.log('Speech started');
            setIsPlayingResponse(true);
        };

        utterance.onend = () => {
            console.log('Speech ended');
            setIsPlayingResponse(false);
        };

        utterance.onerror = (event) => {
            console.error('Speech error:', event.error);
            setIsPlayingResponse(false);
            setError('Text-to-speech failed: ' + event.error);
        };

        console.log('Speaking...');
        window.speechSynthesis.speak(utterance);
    };

    // Stop speaking
    const stopSpeaking = () => {
        if ('speechSynthesis' in window) {
            window.speechSynthesis.cancel();
            setIsPlayingResponse(false);
        }
    };

    return (
        <div className="voice-chat-container">
            <div className="voice-chat-header">
                <h2>🎙️ Live Voice Chat</h2>
                <p>Automatic: Record → AI Processes → Speaks Response</p>
            </div>

            {/* Voice button with animation */}
            <div className="voice-button-container">
                {!isRecording && !isProcessing && !isPlayingResponse && (
                    <button
                        className="voice-button record"
                        onClick={startRecording}
                        aria-label="Start recording"
                    >
                        <div className="mic-icon">🎤</div>
                        <span>Tap to speak</span>
                    </button>
                )}

                {isRecording && (
                    <button
                        className="voice-button recording"
                        onClick={stopRecording}
                        aria-label="Stop recording"
                    >
                        <div
                            className="audio-visualizer"
                            style={{
                                transform: `scale(${1 + audioLevel / 50})`,
                                opacity: 0.5 + (audioLevel / 200)
                            }}
                        >
                            <div className="pulse-ring"></div>
                            <div className="pulse-ring delay-1"></div>
                            <div className="pulse-ring delay-2"></div>
                        </div>
                        <div className="mic-icon recording">🎤</div>
                        <span>Recording... (tap to stop)</span>
                    </button>
                )}

                {isProcessing && (
                    <div className="voice-button processing">
                        <div className="spinner"></div>
                        <span>Processing your question...</span>
                    </div>
                )}

                {isPlayingResponse && (
                    <button
                        className="voice-button playing"
                        onClick={stopSpeaking}
                        aria-label="Stop playback"
                    >
                        <div className="sound-waves">
                            <div className="wave"></div>
                            <div className="wave"></div>
                            <div className="wave"></div>
                            <div className="wave"></div>
                            <div className="wave"></div>
                        </div>
                        <div className="speaker-icon">🔊</div>
                        <span>Playing response... (tap to stop)</span>
                    </button>
                )}
            </div>

            {/* Transcription and response */}
            {transcription && (
                <div className="voice-result">
                    <div className="transcription">
                        <strong>You said:</strong>
                        <p>"{transcription}"</p>
                    </div>
                </div>
            )}

            {responseText && (
                <div className="voice-result">
                    <div className="response">
                        <strong>Response:</strong>
                        <p>{responseText}</p>
                    </div>
                </div>
            )}

            {/* Error message */}
            {error && (
                <div className="voice-error">
                    <span>⚠️</span>
                    <p>{error}</p>
                </div>
            )}

            {/* Instructions */}
            <div className="voice-instructions">
                <h3>How it works:</h3>
                <ol>
                    <li>🎤 <strong>Tap</strong> the microphone to start recording</li>
                    <li>🗣️ <strong>Speak</strong> your question clearly</li>
                    <li>⏹️ <strong>Tap again</strong> to stop recording</li>
                    <li>🤖 <strong>Wait</strong> while AI processes (auto)</li>
                    <li>🔊 <strong>Listen</strong> to the spoken response (auto)</li>
                </ol>
                <p className="note">
                    💡 <strong>Fully Automatic:</strong> After you stop recording, the system will automatically transcribe your speech, send it to the AI, get the answer, and speak it back to you!
                </p>
                <p className="note">
                    🔍 <strong>Debugging:</strong> Open browser console (F12) to see detailed logs
                </p>
            </div>
        </div>
    );
};

export default VoiceChat;
