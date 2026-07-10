import { useState, useEffect } from 'react';
import { getClient, updateClient } from '../services/api';

const WhatsAppConfig = ({ slug }) => {
    const [cfg, setCfg] = useState({ wa_enabled: false, wa_phone_number_id: '', wa_access_token: '' });
    const [status, setStatus] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        getClient(slug).then((c) => {
            setCfg({
                wa_enabled: !!c.wa_enabled,
                wa_phone_number_id: c.wa_phone_number_id || '',
                wa_access_token: '', // never returned; leave blank unless changing
            });
        }).catch(() => {});
    }, [slug]);

    const save = async (e) => {
        e.preventDefault();
        setLoading(true);
        setStatus('');
        try {
            const payload = {
                wa_enabled: cfg.wa_enabled,
                wa_phone_number_id: cfg.wa_phone_number_id,
            };
            // Only send the token if the operator entered a new one.
            if (cfg.wa_access_token) payload.wa_access_token = cfg.wa_access_token;
            await updateClient(slug, payload);
            setStatus('Saved ✓');
        } catch (err) {
            setStatus('Save failed: ' + (err?.response?.data?.detail || err.message));
        } finally {
            setLoading(false);
        }
    };

    return (
        <form className="wa-config" onSubmit={save}>
            <h3>💬 WhatsApp</h3>
            <p className="hint">Route this client's WhatsApp number to this assistant. One webhook serves all clients; messages are matched by phone-number ID.</p>
            <label className="checkbox">
                <input type="checkbox" checked={cfg.wa_enabled}
                    onChange={(e) => setCfg({ ...cfg, wa_enabled: e.target.checked })} />
                Enable WhatsApp for this client
            </label>
            <label>Phone number ID
                <input type="text" value={cfg.wa_phone_number_id}
                    onChange={(e) => setCfg({ ...cfg, wa_phone_number_id: e.target.value })}
                    placeholder="e.g. 123456789012345" />
            </label>
            <label>Access token {cfg.wa_phone_number_id && <small>(leave blank to keep existing)</small>}
                <input type="password" value={cfg.wa_access_token}
                    onChange={(e) => setCfg({ ...cfg, wa_access_token: e.target.value })}
                    placeholder="WhatsApp Business access token" />
            </label>
            <button type="submit" disabled={loading} className="btn-primary">
                {loading ? 'Saving…' : 'Save WhatsApp settings'}
            </button>
            {status && <div className="wa-status">{status}</div>}
        </form>
    );
};

export default WhatsAppConfig;
