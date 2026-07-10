"""
Public (customer-facing) Router

Scoped to a SINGLE client slug. Customers reach only their own assistant here;
there is no listing, no admin data, and no way to touch another client. Used by
the hosted chat page (/c/{slug}) and the embeddable widget.
"""

from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session

from api.models import (
    PublicConfigResponse, ChatRequest, ChatResponse, Source,
    FeedbackRequest, MessageResponse,
)
from api.clients import get_pipeline_manager
from services import client_store
from database import get_db
from logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/{slug}/config", response_model=PublicConfigResponse)
async def public_config(slug: str, db: Session = Depends(get_db)):
    """Branding/config for the customer page + widget (no secrets)."""
    client = client_store.get_client(db, slug)
    if client is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    return PublicConfigResponse(
        slug=client.slug,
        name=client.name,
        bot_name=client.bot_name,
        greeting=client.greeting or "Hi! How can I help you today?",
        accent_color=client.accent_color,
        domain=client.domain,
    )


@router.post("/{slug}/chat", response_model=ChatResponse)
async def public_chat(slug: str, request: ChatRequest, db: Session = Depends(get_db)):
    """Customer chat, locked to this slug only."""
    # Must exist in DB — prevents reaching arbitrary/undefined collections.
    if client_store.get_client(db, slug) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")

    manager = get_pipeline_manager()
    pipeline = manager.get_pipeline(slug)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Assistant not available")
    if pipeline.llm_service.llm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Assistant temporarily unavailable",
        )

    history = [{"role": m.role, "content": m.content} for m in request.history]
    # Agentic answering: the model decides when to search the knowledge base.
    result = pipeline.agent_chat(
        message=request.message,
        conversation_history=history,
        top_k=request.top_k,
        session_id=request.session_id,
    )

    sources = None
    if result.get("sources"):
        sources = [
            Source(
                text=s["text"],
                metadata=s.get("metadata", {}),
                distance=s.get("distance"),
            )
            for s in result["sources"]
        ]
    # Log the turn (the learning-loop memory) and return its id for feedback.
    interaction_id = None
    try:
        row = client_store.log_interaction(
            db,
            client_slug=slug,
            session_id=request.session_id,
            user_message=request.message,
            answer=result.get("answer", ""),
            used_retrieval=result.get("used_retrieval", False),
            no_kb_match=result.get("no_kb_match", False),
            emotion=result.get("emotion") or {},
            escalated=result.get("escalated", False),
        )
        interaction_id = row.id
    except Exception as e:
        logger.warning(f"Failed to log interaction for {slug}: {e}")

    return ChatResponse(
        response=result["answer"],
        used_retrieval=result.get("used_retrieval", False),
        sources=sources,
        escalated=result.get("escalated", False),
        emotion=(result.get("emotion") or {}).get("emotion"),
        interaction_id=interaction_id,
    )


@router.post("/{slug}/feedback", response_model=MessageResponse)
async def public_feedback(slug: str, request: FeedbackRequest, db: Session = Depends(get_db)):
    """Record a customer 👍/👎 on an answer (feeds the learning loop)."""
    if request.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")
    row = client_store.set_feedback(db, request.interaction_id, request.rating)
    if row is None:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return MessageResponse(message="Thanks for your feedback!")
