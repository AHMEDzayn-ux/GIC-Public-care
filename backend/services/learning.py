"""
Learning-loop analytics and knowledge-gap detection.

All free / local: aggregates the persisted Interaction log and clusters
unanswered questions using the local embedding model. No LLM calls here
(the only LLM cost is the on-demand KB-answer draft, which lives in the pipeline).
"""

from collections import Counter
from typing import List, Dict, Any

from sqlalchemy.orm import Session

from services import client_store
from logger import get_logger

logger = get_logger(__name__)


def compute_insights(db: Session, client_slug: str) -> Dict[str, Any]:
    """Aggregate conversation metrics for a client from the Interaction log."""
    rows = client_store.list_interactions(db, client_slug, limit=5000)
    total_turns = len(rows)

    if total_turns == 0:
        return {
            "total_conversations": 0, "total_turns": 0, "deflection_rate": 0.0,
            "escalation_rate": 0.0, "satisfaction_rate": None, "thumbs_up": 0,
            "thumbs_down": 0, "weak_count": 0, "emotion_breakdown": {}, "top_questions": [],
        }

    session_ids = {r.session_id for r in rows if r.session_id}
    total_conversations = len(session_ids) if session_ids else total_turns

    escalated = sum(1 for r in rows if r.escalated)
    thumbs_up = sum(1 for r in rows if r.feedback == "up")
    thumbs_down = sum(1 for r in rows if r.feedback == "down")
    weak = sum(1 for r in rows if r.is_weak)

    emotions = Counter((r.emotion or "neutral") for r in rows)
    q_counter = Counter((r.user_message or "").strip().lower() for r in rows if r.user_message)
    top_questions = [{"question": q, "count": c} for q, c in q_counter.most_common(10) if q]

    satisfaction = (thumbs_up / (thumbs_up + thumbs_down)) if (thumbs_up + thumbs_down) else None

    return {
        "total_conversations": total_conversations,
        "total_turns": total_turns,
        "deflection_rate": round((total_turns - escalated) / total_turns, 3),
        "escalation_rate": round(escalated / total_turns, 3),
        "satisfaction_rate": round(satisfaction, 3) if satisfaction is not None else None,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "weak_count": weak,
        "emotion_breakdown": dict(emotions),
        "top_questions": top_questions,
    }


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def cluster_gaps(db: Session, client_slug: str, embeddings_service,
                 similarity_threshold: float = 0.75) -> List[Dict[str, Any]]:
    """Cluster weak (unanswered / thumbs-down) questions into knowledge gaps."""
    rows = client_store.list_weak_interactions(db, client_slug, reasons=["no_kb_match", "thumbs_down"])
    questions = [r.user_message.strip() for r in rows if r.user_message and r.user_message.strip()]
    if not questions:
        return []

    # Embed each question (local, free) and greedily cluster by cosine similarity.
    embeddings = [embeddings_service.embed_text(q) for q in questions]
    clusters: List[Dict[str, Any]] = []  # {centroid, members: [idx]}

    for i, emb in enumerate(embeddings):
        placed = False
        for cl in clusters:
            if _cosine(emb, cl["centroid"]) >= similarity_threshold:
                cl["members"].append(i)
                placed = True
                break
        if not placed:
            clusters.append({"centroid": emb, "members": [i]})

    result = []
    for cl in clusters:
        member_qs = [questions[j] for j in cl["members"]]
        # Representative = most frequent phrasing in the cluster.
        rep = Counter(q.lower() for q in member_qs).most_common(1)[0][0]
        rep_display = next((q for q in member_qs if q.lower() == rep), member_qs[0])
        result.append({
            "representative_question": rep_display,
            "count": len(member_qs),
            "examples": list(dict.fromkeys(member_qs))[:3],
        })

    result.sort(key=lambda c: c["count"], reverse=True)
    return result
