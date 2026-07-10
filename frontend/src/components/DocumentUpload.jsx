import React, { useState, useEffect } from 'react';
import { uploadDocument, listDocuments, clearDocuments } from '../services/api';
import './DocumentUpload.css';

const DocumentUpload = ({ clientId }) => {
    const [documents, setDocuments] = useState([]);
    const [selectedFile, setSelectedFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');
    const [chunkPreviews, setChunkPreviews] = useState([]);
    const [showChunks, setShowChunks] = useState(false);

    useEffect(() => {
        if (clientId) {
            loadDocuments();
        }
    }, [clientId]);

    const loadDocuments = async () => {
        try {
            setLoading(true);
            const data = await listDocuments(clientId);
            setDocuments(data.documents || []);
            setError('');
        } catch (err) {
            setError('Failed to load documents: ' + err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleFileSelect = (e) => {
        const file = e.target.files[0];
        if (file) {
            // Accept PDF and JSON files
            const validTypes = ['application/pdf', 'application/json'];
            const isValidType = validTypes.includes(file.type) || file.name.endsWith('.json') || file.name.endsWith('.pdf');

            if (!isValidType) {
                setError('Please select a PDF or JSON file');
                setSelectedFile(null);
                return;
            }
            setSelectedFile(file);
            setError('');
            setSuccess('');
        }
    };

    const handleUpload = async () => {
        if (!selectedFile) {
            setError('Please select a file first');
            return;
        }

        try {
            setUploading(true);
            setError('');
            const data = await uploadDocument(clientId, selectedFile);
            console.log('Upload response:', data);  // Debug logging
            setSuccess(`✓ Uploaded "${selectedFile.name}" - ${data.chunks_created} chunks created`);

            // Store chunk previews if available
            if (data.chunk_previews && data.chunk_previews.length > 0) {
                console.log('Chunk previews:', data.chunk_previews.length);  // Debug logging
                setChunkPreviews(data.chunk_previews);
                setShowChunks(true);
            } else {
                console.log('No chunk previews in response');  // Debug logging
            }

            setSelectedFile(null);
            // Reset file input
            document.getElementById('file-input').value = '';
            await loadDocuments();
        } catch (err) {
            setError('Failed to upload document: ' + err.message);
        } finally {
            setUploading(false);
        }
    };

    const handleClearAll = async () => {
        if (!window.confirm('Delete all documents? This cannot be undone.')) return;

        try {
            setLoading(true);
            await clearDocuments(clientId);
            setSuccess('✓ All documents cleared');
            await loadDocuments();
            setError('');
        } catch (err) {
            setError('Failed to clear documents: ' + err.message);
        } finally {
            setLoading(false);
        }
    };

    if (!clientId) {
        return (
            <div className="document-upload">
                <div className="no-client-selected">
                    <h3>📁 Document Upload</h3>
                    <p>Please select a client first to upload documents</p>
                </div>
            </div>
        );
    }

    return (
        <div className="document-upload">
            <h3>📁 Document Management</h3>

            {error && <div className="error-message">{error}</div>}
            {success && <div className="success-message">{success}</div>}

            <div className="upload-section">
                <div className="file-input-wrapper">
                    <input
                        id="file-input"
                        type="file"
                        accept=".pdf,.json"
                        onChange={handleFileSelect}
                        disabled={uploading}
                    />
                    <label htmlFor="file-input" className="file-input-label">
                        {selectedFile ? selectedFile.name : 'Choose file (PDF or JSON)...'}
                    </label>
                </div>
                <button
                    onClick={handleUpload}
                    disabled={!selectedFile || uploading}
                    className="btn-upload"
                >
                    {uploading ? 'Uploading...' : 'Upload'}
                </button>
            </div>

            {showChunks && chunkPreviews.length > 0 && (
                <div className="chunks-preview-section">
                    <div className="chunks-header">
                        <h4>📝 Document Chunks Preview ({chunkPreviews.length})</h4>
                        <button
                            onClick={() => setShowChunks(false)}
                            className="btn-close-chunks"
                        >
                            ✕ Close
                        </button>
                    </div>
                    <div className="chunks-list">
                        {chunkPreviews.map((chunk, index) => (
                            <div key={index} className="chunk-item">
                                <div className="chunk-header">
                                    <span className="chunk-number">Chunk #{chunk.chunk_index + 1}</span>
                                    <span className="chunk-size">{chunk.chunk_size} chars</span>
                                </div>
                                <div className="chunk-text">
                                    {chunk.text_preview}
                                </div>
                                {chunk.metadata && Object.keys(chunk.metadata).length > 0 && (
                                    <div className="chunk-metadata">
                                        {Object.entries(chunk.metadata).map(([key, value]) => (
                                            <span key={key} className="metadata-tag">
                                                {key}: {typeof value === 'object' ? JSON.stringify(value) : value}
                                            </span>
                                        ))}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            <div className="documents-section">
                <div className="documents-header">
                    <h4>Uploaded Documents ({documents.length})</h4>
                    {documents.length > 0 && (
                        <button
                            onClick={handleClearAll}
                            disabled={loading}
                            className="btn-clear"
                        >
                            Clear All
                        </button>
                    )}
                </div>

                {loading ? (
                    <div className="loading">Loading documents...</div>
                ) : documents.length === 0 ? (
                    <div className="empty-state">
                        <p>No documents uploaded yet</p>
                        <small>Upload PDF or JSON files to enable Q&A</small>
                    </div>
                ) : (
                    <div className="documents-list">
                        {documents.map((doc, index) => (
                            <div key={index} className="document-item">
                                <div className="doc-icon">📄</div>
                                <div className="doc-info">
                                    <span className="doc-name">{doc.text_preview || doc.metadata?.filename || `Document ${index + 1}`}</span>
                                    {doc.metadata?.chunk_count != null && <small>{doc.metadata.chunk_count} chunks</small>}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default DocumentUpload;
