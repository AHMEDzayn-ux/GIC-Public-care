"""
ORM Models for the Multi-Tenant Chatbot Framework

- Client:   one tenant. Metadata source of truth (persona, domain, branding,
            WhatsApp creds). Vectors live in FAISS collection ``client_{slug}``.
- Document: a file ingested into a client's knowledge base (makes the docs
            listing real instead of a stub).
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Integer,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    """An operator account. Owns one or more clients (tenant isolation)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    is_superadmin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Client(Base):
    """A tenant / deployed assistant. Identified by a URL-safe ``slug``."""

    __tablename__ = "clients"

    # slug is the public, URL-safe identifier used everywhere (/c/{slug},
    # FAISS collection client_{slug}, widget data-slug=...).
    slug = Column(String, primary_key=True, index=True)
    # Which operator owns this client. NULL = legacy/unclaimed (claimed by the
    # first user on bootstrap). Enforces per-operator tenant isolation.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String, nullable=False, default="")
    description = Column(String, nullable=False, default="")

    # Vertical/persona configuration
    domain = Column(String, nullable=False, default="generic")
    persona = Column(Text, nullable=True)  # None -> fall back to domain template

    # Public access token (optional hardening for widget/public API)
    public_token = Column(String, nullable=True)

    # Branding (rendered by the customer page + widget)
    bot_name = Column(String, nullable=False, default="Assistant")
    greeting = Column(Text, nullable=True)
    accent_color = Column(String, nullable=False, default="#4f46e5")

    # Per-client WhatsApp Business credentials
    wa_enabled = Column(Boolean, nullable=False, default=False)
    wa_phone_number_id = Column(String, nullable=True, index=True)
    wa_access_token = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    documents = relationship(
        "Document",
        back_populates="client",
        cascade="all, delete-orphan",
    )

    @property
    def collection_name(self) -> str:
        return f"client_{self.slug}"


class Interaction(Base):
    """One logged chat turn — the persistent memory that the learning loop grows from."""

    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_slug = Column(String, index=True, nullable=False)
    session_id = Column(String, index=True, nullable=True)  # groups a conversation
    user_message = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    used_retrieval = Column(Boolean, nullable=False, default=False)
    no_kb_match = Column(Boolean, nullable=False, default=False)  # searched but found nothing
    emotion = Column(String, nullable=True)
    intensity = Column(Integer, nullable=True)
    escalated = Column(Boolean, nullable=False, default=False)
    is_weak = Column(Boolean, nullable=False, default=False, index=True)
    weak_reason = Column(String, nullable=True)  # no_kb_match | escalated | negative_emotion | thumbs_down
    feedback = Column(String, nullable=True)  # up | down | None
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Escalation(Base):
    """A conversation handed off to a human agent."""

    __tablename__ = "escalations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_slug = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False, default="")
    summary = Column(Text, nullable=True)
    emotion = Column(String, nullable=True)      # detected mood at handoff
    intensity = Column(Integer, nullable=True)   # 1-5
    transcript = Column(Text, nullable=True)     # recent conversation
    status = Column(String, nullable=False, default="open")  # open | resolved
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ActionRequest(Base):
    """A transactional action the agent performed (mock-first).

    Every ticket/callback/account-change the agent takes is recorded here and
    shown in the admin "Requests" inbox. Account *lookups* are read-only and are
    not recorded.
    """

    __tablename__ = "action_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_slug = Column(String, index=True, nullable=False)
    session_id = Column(String, index=True, nullable=True)   # links to the conversation
    action_type = Column(String, nullable=False)             # the tool name, e.g. change_plan
    kind = Column(String, nullable=False)                    # ticket | callback | account_change
    payload = Column(JSON, nullable=True)                    # the args the agent collected
    reference = Column(String, nullable=True)                # human ref, e.g. TKT-1042
    result = Column(Text, nullable=True)                     # what we told the customer
    status = Column(String, nullable=False, default="open")  # open | done
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MockAccount(Base):
    """Seeded demo account data the agent can look up / change (mock backend).

    Stands in for a real telecom/university system so account actions can be
    demoed end-to-end. Swap the handlers in services/actions.py for real APIs later.
    """

    __tablename__ = "mock_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_slug = Column(String, index=True, nullable=False)
    identifier = Column(String, index=True, nullable=False)  # phone / email / application-id
    name = Column(String, nullable=True)
    data = Column(JSON, nullable=True)                        # plan/balance/status/program…
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Document(Base):
    """A file ingested into a client's knowledge base."""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_slug = Column(
        String, ForeignKey("clients.slug", ondelete="CASCADE"), index=True, nullable=False
    )
    filename = Column(String, nullable=False)
    doc_type = Column(String, nullable=False, default="")  # pdf | json
    chunk_count = Column(Integer, nullable=False, default=0)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    client = relationship("Client", back_populates="documents")
