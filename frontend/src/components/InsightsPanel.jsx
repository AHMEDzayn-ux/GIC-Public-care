import { useState, useEffect } from 'react';
import { getInsights, getGaps, draftGapAnswer, addKbEntry } from '../services/api';

const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');

const InsightsPanel = ({ slug }) => {
    const [insights, setInsights] = useState(null);
    const [gaps, setGaps] = useState([]);
    const [loading, setLoading] = useState(true);
    const [draftIdx, setDraftIdx] = useState(null);
    const [draft, setDraft] = useState({ title: '', content: '' });
    const [busy, setBusy] = useState(false);
    const [msg, setMsg] = useState('');

    const load = async () => {
        setLoading(true);
        try {
            const [ins, gp] = await Promise.all([getInsights(slug), getGaps(slug)]);
            setInsights(ins);
            setGaps(gp.gaps || []);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [slug]);

    const handleDraft = async (idx, gap) => {
        setDraftIdx(idx);
        setDraft({ title: '', content: '' });
        setBusy(true);
        setMsg('');
        try {
            const d = await draftGapAnswer(slug, gap.examples && gap.examples.length ? gap.examples : [gap.representative_question]);
            setDraft({ title: d.title || '', content: d.content || '' });
        } catch (err) {
            setMsg('Draft failed: ' + (err?.response?.data?.detail || err.message));
        } finally {
            setBusy(false);
        }
    };

    const handleApprove = async () => {
        if (!draft.content.trim()) { setMsg('Please write/verify the answer before adding.'); return; }
        setBusy(true);
        setMsg('');
        try {
            await addKbEntry(slug, draft.title, draft.content);
            setMsg('✓ Added to the knowledge base. The agent can now answer this.');
            setDraftIdx(null);
            setDraft({ title: '', content: '' });
            await load();
        } catch (err) {
            setMsg('Add failed: ' + (err?.response?.data?.detail || err.message));
        } finally {
            setBusy(false);
        }
    };

    if (loading) return <div className="insights"><p>Loading…</p></div>;
    if (!insights) return <div className="insights"><p>No data yet.</p></div>;

    return (
        <div className="insights">
            <div className="inbox-head">
                <h3>📊 Insights</h3>
                <button className="btn-mini" onClick={load}>Refresh</button>
            </div>

            <div className="stat-grid">
                <div className="stat-card"><div className="stat-num">{insights.total_conversations}</div><div className="stat-label">Conversations</div></div>
                <div className="stat-card"><div className="stat-num">{insights.total_turns}</div><div className="stat-label">Messages</div></div>
                <div className="stat-card good"><div className="stat-num">{pct(insights.deflection_rate)}</div><div className="stat-label">Self-served</div></div>
                <div className="stat-card warn"><div className="stat-num">{pct(insights.escalation_rate)}</div><div className="stat-label">Escalated</div></div>
                <div className="stat-card"><div className="stat-num">{pct(insights.satisfaction_rate)}</div><div className="stat-label">Satisfaction 👍</div></div>
                <div className="stat-card"><div className="stat-num">{insights.weak_count}</div><div className="stat-label">Weak answers</div></div>
            </div>

            {insights.top_questions && insights.top_questions.length > 0 && (
                <div className="insights-block">
                    <h4>Top questions</h4>
                    <ul className="top-q">
                        {insights.top_questions.map((q, i) => (
                            <li key={i}><span className="q-count">{q.count}×</span> {q.question}</li>
                        ))}
                    </ul>
                </div>
            )}

            <div className="insights-block">
                <h4>🧩 Knowledge gaps <span className="hint">(questions it couldn't answer — teach it here)</span></h4>
                {gaps.length === 0 ? (
                    <p className="empty-state">No gaps 🎉 — it's answering everything customers ask.</p>
                ) : (
                    <div className="gap-list">
                        {gaps.map((g, idx) => (
                            <div key={idx} className="gap-item">
                                <div className="gap-q"><strong>{g.representative_question}</strong> <span className="q-count">asked {g.count}×</span></div>
                                {g.examples && g.examples.length > 1 && (
                                    <div className="gap-examples">{g.examples.slice(1).map((e, i) => <span key={i}>• {e}</span>)}</div>
                                )}
                                {draftIdx === idx ? (
                                    <div className="draft-box">
                                        <input className="draft-title" placeholder="Title" value={draft.title}
                                            onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
                                        <textarea className="draft-content" rows="5"
                                            placeholder={busy ? 'Drafting…' : 'Answer (verify/edit before adding — replace any [SPECIFY])'}
                                            value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
                                        <div className="draft-actions">
                                            <button className="btn-primary" disabled={busy} onClick={handleApprove}>Add to knowledge base</button>
                                            <button className="btn-mini" disabled={busy} onClick={() => setDraftIdx(null)}>Cancel</button>
                                        </div>
                                    </div>
                                ) : (
                                    <button className="btn-mini" onClick={() => handleDraft(idx, g)}>✍️ Draft answer</button>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {msg && <div className="insights-msg">{msg}</div>}
            </div>
        </div>
    );
};

export default InsightsPanel;
