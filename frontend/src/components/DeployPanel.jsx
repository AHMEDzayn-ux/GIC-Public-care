import { useState } from 'react';
import { API_BASE } from '../services/api';

const DeployPanel = ({ slug }) => {
    const [copied, setCopied] = useState('');

    const origin = window.location.origin;
    const customerUrl = `${origin}/c/${slug}`;
    const widgetSnippet =
        `<script src="${API_BASE}/widget.js" data-slug="${slug}"></script>`;

    const copy = (text, key) => {
        navigator.clipboard.writeText(text).then(() => {
            setCopied(key);
            setTimeout(() => setCopied(''), 1500);
        });
    };

    return (
        <div className="deploy-panel">
            <h3>🚀 Deploy — give these to your client</h3>

            <div className="deploy-item">
                <label>Hosted chat page</label>
                <div className="deploy-row">
                    <code>{customerUrl}</code>
                    <a href={customerUrl} target="_blank" rel="noreferrer" className="btn-mini">Open</a>
                    <button className="btn-mini" onClick={() => copy(customerUrl, 'url')}>
                        {copied === 'url' ? '✓' : 'Copy'}
                    </button>
                </div>
            </div>

            <div className="deploy-item">
                <label>Embeddable widget (paste into the client's website)</label>
                <div className="deploy-row">
                    <code className="snippet">{widgetSnippet}</code>
                    <button className="btn-mini" onClick={() => copy(widgetSnippet, 'widget')}>
                        {copied === 'widget' ? '✓' : 'Copy'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default DeployPanel;
