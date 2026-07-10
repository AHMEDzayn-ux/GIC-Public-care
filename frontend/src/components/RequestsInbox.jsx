import { useState, useEffect } from 'react';
import { getRequests, setRequestStatus, getAccounts, seedAccounts } from '../services/api';

const KIND_ICON = {
    ticket: '🎫', callback: '📞', account_change: '🔁', account_lookup: '🔍',
};

const RequestsInbox = ({ slug }) => {
    const [items, setItems] = useState([]);
    const [openCount, setOpenCount] = useState(0);
    const [accounts, setAccounts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const [reqs, accs] = await Promise.all([getRequests(slug), getAccounts(slug)]);
            setItems(reqs.actions || []);
            setOpenCount(reqs.open_count || 0);
            setAccounts(accs.accounts || []);
        } catch (err) {
            console.error('Failed to load requests', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [slug]);

    const handleStatus = async (id, status) => {
        try {
            await setRequestStatus(slug, id, status);
            await load();
        } catch (err) {
            alert('Failed: ' + (err?.response?.data?.detail || err.message));
        }
    };

    const handleSeed = async () => {
        setBusy(true);
        try {
            const data = await seedAccounts(slug);
            setAccounts(data.accounts || []);
        } catch (err) {
            alert('Seed failed: ' + (err?.response?.data?.detail || err.message));
        } finally {
            setBusy(false);
        }
    };

    if (loading) return <div className="inbox"><p>Loading…</p></div>;

    return (
        <div className="inbox">
            <div className="inbox-head">
                <h3>🎫 Requests {openCount > 0 && <span className="inbox-badge">{openCount} open</span>}</h3>
                <button className="btn-mini" onClick={load}>Refresh</button>
            </div>

            {items.length === 0 ? (
                <p className="empty-state">No actions yet. Tickets, callbacks, and account changes the agent performs will appear here.</p>
            ) : (
                <div className="inbox-list">
                    {items.map((a) => (
                        <div key={a.id} className={`inbox-item ${a.status === 'done' ? 'resolved' : ''}`}>
                            <div className="inbox-item-head">
                                <span className="inbox-emotion">{KIND_ICON[a.kind] || '⚙️'} {a.action_type}</span>
                                {a.reference && <span className="req-ref">{a.reference}</span>}
                                <span className={`inbox-status status-${a.status === 'done' ? 'resolved' : 'open'}`}>{a.status}</span>
                                <span className="inbox-time">{a.created_at ? new Date(a.created_at).toLocaleString() : ''}</span>
                            </div>
                            {a.payload && Object.keys(a.payload).length > 0 && (
                                <div className="req-payload">
                                    {Object.entries(a.payload).map(([k, v]) => (
                                        <span key={k}><strong>{k}:</strong> {String(v)}</span>
                                    ))}
                                </div>
                            )}
                            {a.result && <div className="inbox-summary">→ {a.result}</div>}
                            <div className="inbox-actions">
                                {a.status === 'open'
                                    ? <button className="btn-mini resolve" onClick={() => handleStatus(a.id, 'done')}>Mark done</button>
                                    : <button className="btn-mini" onClick={() => handleStatus(a.id, 'open')}>Reopen</button>}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <div className="insights-block">
                <h4>🧪 Mock accounts <span className="hint">(demo data the agent can look up / change)</span></h4>
                {accounts.length === 0 ? (
                    <p className="empty-state" style={{ marginBottom: 10 }}>No demo accounts yet.</p>
                ) : (
                    <div className="acct-list">
                        {accounts.map((a) => (
                            <div key={a.id} className="acct-item">
                                <div className="acct-id">{a.identifier} <span className="q-count">{a.name}</span></div>
                                <div className="req-payload">
                                    {Object.entries(a.data || {}).map(([k, v]) => (
                                        <span key={k}><strong>{k}:</strong> {String(v)}</span>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
                <button className="btn-mini" disabled={busy} onClick={handleSeed}>
                    {busy ? 'Seeding…' : (accounts.length ? 'Re-seed demo accounts' : 'Seed demo accounts')}
                </button>
                <p className="hint" style={{ marginTop: 6 }}>Give the agent one of these identifiers to test account lookup/change.</p>
            </div>
        </div>
    );
};

export default RequestsInbox;
