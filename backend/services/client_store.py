"""
Client Store — DB-backed CRUD for tenants, plus reconciliation of pre-existing
on-disk FAISS collections into the database.

The DB is the source of truth for client METADATA. FAISS collections
(``client_{slug}``) remain the source of truth for vectors. On startup we
import any orphan on-disk ``client_*`` collection that has no DB row, so data
created before this framework existed (e.g. ``client_Nexus``) is not lost.
"""

import secrets
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from db_models import Client, Document, Escalation, Interaction, ActionRequest, MockAccount
from domain_templates import get_template, DEFAULT_DOMAIN
from config import get_settings
from logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _faiss_dir() -> Path:
    return Path(settings.vector_stores_dir) / "faiss"


def list_disk_slugs() -> List[str]:
    """Scan the FAISS directory for ``client_<slug>.index`` collections."""
    slugs = []
    faiss_dir = _faiss_dir()
    if faiss_dir.exists():
        for index_file in faiss_dir.glob("client_*.index"):
            name = index_file.stem  # e.g. client_Nexus
            if name.startswith("client_"):
                slugs.append(name[len("client_"):])
    return slugs


def reconcile_disk_collections(db: Session) -> int:
    """Create DB rows for on-disk collections lacking one. Returns count imported."""
    imported = 0
    existing = {c.slug for c in db.query(Client.slug).all()}
    for slug in list_disk_slugs():
        if slug in existing:
            continue
        template = get_template(DEFAULT_DOMAIN)
        client = Client(
            slug=slug,
            name=slug,
            description="Imported from existing knowledge base",
            domain=DEFAULT_DOMAIN,
            persona=None,
            public_token=secrets.token_urlsafe(16),
            bot_name=template.bot_name,
            greeting=template.greeting,
        )
        db.add(client)
        imported += 1
        logger.info(f"Reconciled on-disk collection into DB: {slug}")
    if imported:
        db.commit()
    return imported


def create_client(
    db: Session,
    slug: str,
    name: str = "",
    description: str = "",
    domain: str = DEFAULT_DOMAIN,
    persona: Optional[str] = None,
    bot_name: Optional[str] = None,
    greeting: Optional[str] = None,
    accent_color: Optional[str] = None,
    owner_id: Optional[int] = None,
) -> Client:
    """Insert a new client row. Branding defaults come from the domain template."""
    template = get_template(domain)
    client = Client(
        slug=slug,
        owner_id=owner_id,
        name=name or slug,
        description=description,
        domain=domain,
        persona=persona,  # None -> resolved from template at pipeline build time
        public_token=secrets.token_urlsafe(16),
        bot_name=bot_name or template.bot_name,
        greeting=greeting or template.greeting,
        accent_color=accent_color or "#4f46e5",
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def get_client(db: Session, slug: str) -> Optional[Client]:
    return db.get(Client, slug)


def list_clients(db: Session, owner_id: Optional[int] = None) -> List[Client]:
    q = db.query(Client)
    if owner_id is not None:
        q = q.filter(Client.owner_id == owner_id)
    return q.order_by(Client.created_at.desc()).all()


def update_client(db: Session, slug: str, **fields) -> Optional[Client]:
    client = db.get(Client, slug)
    if client is None:
        return None
    allowed = {
        "name", "description", "domain", "persona", "bot_name", "greeting",
        "accent_color", "wa_enabled", "wa_phone_number_id", "wa_access_token",
    }
    for key, value in fields.items():
        if key in allowed and value is not None:
            setattr(client, key, value)
    db.commit()
    db.refresh(client)
    return client


def delete_client(db: Session, slug: str) -> bool:
    client = db.get(Client, slug)
    if client is None:
        return False
    db.delete(client)
    db.commit()
    return True


def delete_collection_files(slug: str) -> None:
    """Remove a client's FAISS files directly (no model load required)."""
    faiss_dir = _faiss_dir()
    for suffix in (".index", "_metadata.pkl"):
        path = faiss_dir / f"client_{slug}{suffix}"
        try:
            if path.exists():
                path.unlink()
        except Exception as e:  # pragma: no cover - best effort cleanup
            logger.warning(f"Could not delete {path}: {e}")


def find_by_wa_phone(db: Session, phone_number_id: str) -> Optional[Client]:
    """Route an inbound WhatsApp message to the right client by phone-number id."""
    return (
        db.query(Client)
        .filter(Client.wa_phone_number_id == phone_number_id, Client.wa_enabled == True)  # noqa: E712
        .first()
    )


def resolve_persona(client: Client) -> str:
    """Effective persona: explicit override or the domain template default."""
    if client.persona and client.persona.strip():
        return client.persona
    return get_template(client.domain).persona


# ---- Document tracking -------------------------------------------------------

def add_document(db: Session, client_slug: str, filename: str, doc_type: str, chunk_count: int) -> Document:
    doc = Document(
        client_slug=client_slug,
        filename=filename,
        doc_type=doc_type,
        chunk_count=chunk_count,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def list_documents(db: Session, client_slug: str) -> List[Document]:
    return (
        db.query(Document)
        .filter(Document.client_slug == client_slug)
        .order_by(Document.uploaded_at.desc())
        .all()
    )


def clear_documents(db: Session, client_slug: str) -> int:
    count = db.query(Document).filter(Document.client_slug == client_slug).delete()
    db.commit()
    return count


# ---- Escalations (human handoff) --------------------------------------------

def create_escalation(db: Session, client_slug: str, reason: str, summary: str = "",
                      emotion: str = None, intensity: int = None, transcript: str = "") -> Escalation:
    esc = Escalation(
        client_slug=client_slug,
        reason=reason,
        summary=summary,
        emotion=emotion,
        intensity=intensity,
        transcript=transcript,
    )
    db.add(esc)
    db.commit()
    db.refresh(esc)
    return esc


def list_escalations(db: Session, client_slug: str, status: Optional[str] = None) -> List[Escalation]:
    q = db.query(Escalation).filter(Escalation.client_slug == client_slug)
    if status:
        q = q.filter(Escalation.status == status)
    return q.order_by(Escalation.created_at.desc()).all()


def resolve_escalation(db: Session, escalation_id: int) -> Optional[Escalation]:
    esc = db.get(Escalation, escalation_id)
    if esc is None:
        return None
    esc.status = "resolved"
    db.commit()
    db.refresh(esc)
    return esc


# ---- Interactions (learning-loop memory) ------------------------------------

def log_interaction(db: Session, *, client_slug: str, session_id: str, user_message: str,
                    answer: str, used_retrieval: bool, no_kb_match: bool,
                    emotion: dict = None, escalated: bool = False) -> Interaction:
    """Persist one chat turn and compute its weak-answer flag from free signals."""
    emotion = emotion or {}
    emo = emotion.get("emotion")
    intensity = emotion.get("intensity")

    weak_reason = None
    if no_kb_match:
        weak_reason = "no_kb_match"
    elif escalated:
        weak_reason = "escalated"
    elif emo in ("angry", "frustrated") and (intensity or 0) >= 3:
        weak_reason = "negative_emotion"

    row = Interaction(
        client_slug=client_slug,
        session_id=session_id,
        user_message=user_message,
        answer=answer,
        used_retrieval=used_retrieval,
        no_kb_match=no_kb_match,
        emotion=emo,
        intensity=intensity,
        escalated=escalated,
        is_weak=weak_reason is not None,
        weak_reason=weak_reason,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def set_feedback(db: Session, interaction_id: int, rating: str) -> Optional[Interaction]:
    row = db.get(Interaction, interaction_id)
    if row is None:
        return None
    row.feedback = rating
    if rating == "down":
        row.is_weak = True
        row.weak_reason = row.weak_reason or "thumbs_down"
    db.commit()
    db.refresh(row)
    return row


def list_interactions(db: Session, client_slug: str, limit: int = 1000) -> List[Interaction]:
    return (
        db.query(Interaction)
        .filter(Interaction.client_slug == client_slug)
        .order_by(Interaction.created_at.desc())
        .limit(limit)
        .all()
    )


def list_weak_interactions(db: Session, client_slug: str, reasons: List[str] = None) -> List[Interaction]:
    q = db.query(Interaction).filter(
        Interaction.client_slug == client_slug, Interaction.is_weak == True  # noqa: E712
    )
    if reasons:
        q = q.filter(Interaction.weak_reason.in_(reasons))
    return q.order_by(Interaction.created_at.desc()).all()


# ---- Transactional actions --------------------------------------------------

def create_action_request(db: Session, *, client_slug: str, session_id: Optional[str],
                          action_type: str, kind: str, payload: dict,
                          result: str = "") -> ActionRequest:
    row = ActionRequest(
        client_slug=client_slug,
        session_id=session_id,
        action_type=action_type,
        kind=kind,
        payload=payload or {},
        result=result,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_action_requests(db: Session, client_slug: str, status: Optional[str] = None) -> List[ActionRequest]:
    q = db.query(ActionRequest).filter(ActionRequest.client_slug == client_slug)
    if status:
        q = q.filter(ActionRequest.status == status)
    return q.order_by(ActionRequest.created_at.desc()).all()


def _norm_ref(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def get_action_by_reference(db: Session, client_slug: str, reference: str) -> Optional[ActionRequest]:
    """Find a logged action by its reference (forgiving of spaces/dashes/case)."""
    want = _norm_ref(reference)
    if not want:
        return None
    rows = db.query(ActionRequest).filter(ActionRequest.client_slug == client_slug).all()
    for r in rows:
        if _norm_ref(r.reference) == want:
            return r
    return None


def set_action_status(db: Session, action_id: int, status: str) -> Optional[ActionRequest]:
    row = db.get(ActionRequest, action_id)
    if row is None:
        return None
    row.status = status
    db.commit()
    db.refresh(row)
    return row


# ---- Mock accounts (demo backend for account lookup/change) ------------------

def get_mock_account(db: Session, client_slug: str, identifier: str) -> Optional[MockAccount]:
    """Look up a demo account by (loose) identifier match — phone/email/app-id."""
    ident = (identifier or "").strip().lower()
    if not ident:
        return None
    rows = db.query(MockAccount).filter(MockAccount.client_slug == client_slug).all()
    for r in rows:
        if (r.identifier or "").strip().lower() == ident:
            return r
    # Fall back to a forgiving digits-only match (phone numbers typed with spaces/dashes).
    digits = "".join(c for c in ident if c.isdigit())
    if len(digits) >= 6:
        for r in rows:
            rd = "".join(c for c in (r.identifier or "") if c.isdigit())
            if rd and rd == digits:
                return r
    return None


def list_mock_accounts(db: Session, client_slug: str) -> List[MockAccount]:
    return (
        db.query(MockAccount)
        .filter(MockAccount.client_slug == client_slug)
        .order_by(MockAccount.created_at.asc())
        .all()
    )


def upsert_mock_account(db: Session, client_slug: str, identifier: str,
                        name: str = "", data: dict = None) -> MockAccount:
    row = get_mock_account(db, client_slug, identifier)
    if row is None:
        row = MockAccount(client_slug=client_slug, identifier=identifier, name=name, data=data or {})
        db.add(row)
    else:
        if name:
            row.name = name
        if data is not None:
            row.data = data
    db.commit()
    db.refresh(row)
    return row


# Domain-appropriate demo accounts so the account-lookup demo has known identifiers.
_DEMO_ACCOUNTS = {
    "telecom": [
        ("0771234567", "Alex Fernando",
         {"plan": "Value 20", "monthly_price": "$20", "balance": "$12.50",
          "due_date": "2026-07-20", "data_used": "14 GB of 25 GB", "status": "active"}),
        ("0777654321", "Priya Kumar",
         {"plan": "Unlimited Pro", "monthly_price": "$45", "balance": "$0.00",
          "due_date": "2026-07-28", "data_used": "unlimited 5G", "status": "active"}),
    ],
    "university": [
        ("APP-10234", "Sara Nimal",
         {"program": "BSc Computer Science", "application_status": "under review",
          "intake": "Fall 2026", "fees_due": "$1,200", "advisor": "Dr. Perera"}),
        ("sara.n@example.edu", "Sara Nimal",
         {"program": "BSc Computer Science", "application_status": "under review",
          "intake": "Fall 2026", "fees_due": "$1,200", "advisor": "Dr. Perera"}),
    ],
    "generic": [
        ("jamie@example.com", "Jamie Lee",
         {"plan": "Standard", "status": "active", "balance": "$0.00", "member_since": "2024"}),
    ],
}


def seed_demo_accounts(db: Session, client_slug: str, domain: str) -> List[MockAccount]:
    """Insert a small set of realistic demo accounts for this client's domain.

    Idempotent: clears any existing mock accounts for the client, then inserts fresh.
    """
    db.query(MockAccount).filter(MockAccount.client_slug == client_slug).delete()
    demos = _DEMO_ACCOUNTS.get((domain or "generic").lower(), _DEMO_ACCOUNTS["generic"])
    for identifier, name, data in demos:
        db.add(MockAccount(client_slug=client_slug, identifier=identifier, name=name, data=data))
    db.commit()
    return list_mock_accounts(db, client_slug)
