"""
Telecom store — data access for the enterprise BSS/OSS schema (telecom_models).

This is the ONE place the telecom database is read and written, shared by:
  - the per-client admin portal   (api/portal.py)
  - the AI chatbot's actions       (services/actions.py, telecom domain)

So a package the bot activates and a complaint it logs land in the exact same
subscriber / activation / ledger / ticket tables the human admin manages.

Query helpers return plain dicts already shaped for the API models (money cast to
float), so the router is a thin mapping layer. Write helpers keep the money ledger
consistent (activation → charge → balance).
"""

import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from telecom_models import (
    Plan,
    Customer,
    BillingAccount,
    Subscription,
    CallDetailRecord,
    ChargingTransaction,
    Invoice,
    PackageActivation,
    Ticket,
)
from db_models import Client
from logger import get_logger

logger = get_logger(__name__)


# ---- helpers ----------------------------------------------------------------

def _f(x) -> float:
    """Decimal/None -> float for JSON responses."""
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _get_subscription(db: Session, slug: str, msisdn: str) -> Optional[Subscription]:
    """Resolve a line by MSISDN (forgiving of spaces/dashes), scoped to the tenant."""
    ident = (msisdn or "").strip()
    if not ident:
        return None
    sub = (
        db.query(Subscription)
        .filter(Subscription.client_slug == slug, Subscription.msisdn == ident)
        .first()
    )
    if sub:
        return sub
    digits = "".join(c for c in ident if c.isdigit())
    if len(digits) >= 6:
        for s in db.query(Subscription).filter(Subscription.client_slug == slug).all():
            if "".join(c for c in (s.msisdn or "") if c.isdigit()) == digits:
                return s
    return None


def verify_identity(db: Session, slug: str, msisdn: str, nic: str) -> bool:
    """Check a claimed NIC against the account holder on file for this line.

    Used to gate any chatbot action that reveals or changes account-specific
    data (call/billing/activation history, tickets, plan/line changes, etc.) —
    a phone number alone isn't proof of ownership.
    """
    sub = _get_subscription(db, slug, msisdn)
    if sub is None or sub.customer is None:
        return False
    on_file = (sub.customer.nic or "").strip().upper()
    given = (nic or "").strip().upper()
    return bool(on_file) and on_file == given


# ---- response shaping (dicts matching api/models.py) ------------------------

def _sub_dict(s: Subscription) -> dict:
    cust = s.customer
    plan = s.plan
    acct = s.account
    return {
        "id": s.id,
        "msisdn": s.msisdn,
        "status": s.status,
        "customer_id": s.customer_id,
        "customer_name": cust.full_name if cust else None,
        "account_number": acct.account_number if acct else None,
        "plan_name": plan.name if plan else None,
        "plan_type": plan.plan_type if plan else None,
        "prepaid_balance": _f(s.prepaid_balance),
        "data_balance_mb": s.data_balance_mb or 0,
        "activation_date": _iso(s.activation_date),
        "city": cust.city if cust else None,
    }


def _customer_dict(c: Customer, sub_count: int = 0) -> dict:
    return {
        "id": c.id,
        "full_name": c.full_name,
        "nic": c.nic,
        "email": c.email,
        "phone": c.phone,
        "city": c.city,
        "customer_type": c.customer_type,
        "kyc_status": c.kyc_status,
        "subscription_count": sub_count,
        "created_at": _iso(c.created_at),
    }


def _plan_dict(p: Plan) -> dict:
    return {
        "id": p.id, "code": p.code, "name": p.name, "plan_type": p.plan_type,
        "monthly_rental": _f(p.monthly_rental), "data_quota_mb": p.data_quota_mb,
        "voice_minutes": p.voice_minutes, "sms_units": p.sms_units,
        "validity_days": p.validity_days, "is_active": p.is_active,
    }


def _cdr_dict(r: CallDetailRecord) -> dict:
    return {
        "id": r.id, "msisdn": r.msisdn, "direction": r.direction,
        "other_party": r.other_party, "event_type": r.event_type,
        "start_time": _iso(r.start_time), "duration_sec": r.duration_sec,
        "bytes_used": r.bytes_used, "charged_amount": _f(r.charged_amount),
        "cell_site": r.cell_site,
    }


def _txn_dict(t: ChargingTransaction) -> dict:
    return {
        "id": t.id, "msisdn": t.msisdn, "txn_type": t.txn_type,
        "description": t.description, "amount": _f(t.amount),
        "balance_after": _f(t.balance_after) if t.balance_after is not None else None,
        "channel": t.channel, "reference": t.reference, "created_at": _iso(t.created_at),
    }


def _invoice_dict(i: Invoice, account_number: Optional[str] = None) -> dict:
    return {
        "id": i.id, "invoice_number": i.invoice_number, "account_number": account_number,
        "period_start": _iso(i.period_start), "period_end": _iso(i.period_end),
        "amount_due": _f(i.amount_due), "due_date": _iso(i.due_date), "status": i.status,
    }


def _activation_dict(a: PackageActivation) -> dict:
    return {
        "id": a.id, "msisdn": a.msisdn, "package_name": a.package_name,
        "price": _f(a.price), "validity_days": a.validity_days, "channel": a.channel,
        "status": a.status, "activated_at": _iso(a.activated_at),
        "expires_at": _iso(a.expires_at), "reference": a.reference,
    }


def _ticket_dict(t: Ticket) -> dict:
    cust = t.__dict__.get("_customer_name")  # set by list join, optional
    return {
        "id": t.id, "ticket_number": t.ticket_number, "msisdn": t.msisdn,
        "customer_name": cust, "subject": t.subject, "category": t.category,
        "priority": t.priority, "status": t.status, "channel": t.channel,
        "assigned_to": t.assigned_to, "description": t.description,
        "resolution": t.resolution, "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
    }


# ---- queries ----------------------------------------------------------------

def search_subscriptions(db: Session, slug: str, q: str = "", status: str = "",
                         skip: int = 0, limit: int = 50):
    """Subscriptions joined to customer/plan/account, filtered + paginated."""
    query = (
        db.query(Subscription)
        .options(joinedload(Subscription.customer),
                 joinedload(Subscription.plan),
                 joinedload(Subscription.account))
        .filter(Subscription.client_slug == slug)
    )
    if status:
        query = query.filter(Subscription.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(Customer, Subscription.customer_id == Customer.id).filter(
            (Subscription.msisdn.ilike(like)) | (Customer.full_name.ilike(like))
        )
    total = query.count()
    rows = query.order_by(Subscription.id.asc()).offset(skip).limit(limit).all()
    return [_sub_dict(s) for s in rows], total


def get_subscription_360(db: Session, slug: str, msisdn: str) -> Optional[dict]:
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    cust = sub.customer
    cdrs = (
        db.query(CallDetailRecord)
        .filter(CallDetailRecord.subscription_id == sub.id)
        .order_by(CallDetailRecord.start_time.desc()).limit(25).all()
    )
    txns = (
        db.query(ChargingTransaction)
        .filter(ChargingTransaction.subscription_id == sub.id)
        .order_by(ChargingTransaction.created_at.desc()).limit(25).all()
    )
    acts = (
        db.query(PackageActivation)
        .filter(PackageActivation.subscription_id == sub.id)
        .order_by(PackageActivation.activated_at.desc()).all()
    )
    tickets = (
        db.query(Ticket)
        .filter(Ticket.subscription_id == sub.id)
        .order_by(Ticket.created_at.desc()).all()
    )
    invoices = (
        db.query(Invoice)
        .filter(Invoice.account_id == sub.account_id)
        .order_by(Invoice.issued_at.desc()).all()
    )
    acct_no = sub.account.account_number if sub.account else None
    return {
        "subscription": _sub_dict(sub),
        "customer": _customer_dict(cust, sub_count=len(cust.subscriptions)) if cust else None,
        "recent_cdrs": [_cdr_dict(r) for r in cdrs],
        "recent_transactions": [_txn_dict(t) for t in txns],
        "activations": [_activation_dict(a) for a in acts],
        "tickets": [_ticket_dict(t) for t in tickets],
        "invoices": [_invoice_dict(i, acct_no) for i in invoices],
    }


def list_customers(db: Session, slug: str, q: str = "", skip: int = 0, limit: int = 50):
    query = db.query(Customer).filter(Customer.client_slug == slug)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (Customer.full_name.ilike(like)) | (Customer.nic.ilike(like)) |
            (Customer.email.ilike(like)) | (Customer.phone.ilike(like))
        )
    total = query.count()
    rows = query.order_by(Customer.id.asc()).offset(skip).limit(limit).all()
    # subscription counts in one grouped query
    counts = dict(
        db.query(Subscription.customer_id, func.count(Subscription.id))
        .filter(Subscription.client_slug == slug)
        .group_by(Subscription.customer_id).all()
    )
    return [_customer_dict(c, counts.get(c.id, 0)) for c in rows], total


def list_plans(db: Session, slug: str) -> List[dict]:
    rows = (
        db.query(Plan).filter(Plan.client_slug == slug)
        .order_by(Plan.monthly_rental.asc()).all()
    )
    return [_plan_dict(p) for p in rows]


def list_cdrs(db: Session, slug: str, msisdn: str = "", skip: int = 0, limit: int = 100):
    query = db.query(CallDetailRecord).filter(CallDetailRecord.client_slug == slug)
    if msisdn:
        query = query.filter(CallDetailRecord.msisdn == msisdn.strip())
    total = query.count()
    rows = query.order_by(CallDetailRecord.start_time.desc()).offset(skip).limit(limit).all()
    return [_cdr_dict(r) for r in rows], total


def list_transactions(db: Session, slug: str, msisdn: str = "", txn_type: str = "",
                      skip: int = 0, limit: int = 100):
    query = db.query(ChargingTransaction).filter(ChargingTransaction.client_slug == slug)
    if msisdn:
        query = query.filter(ChargingTransaction.msisdn == msisdn.strip())
    if txn_type:
        query = query.filter(ChargingTransaction.txn_type == txn_type)
    total = query.count()
    rows = query.order_by(ChargingTransaction.created_at.desc()).offset(skip).limit(limit).all()
    return [_txn_dict(t) for t in rows], total


def list_invoices(db: Session, slug: str, status: str = "", skip: int = 0, limit: int = 100):
    query = (
        db.query(Invoice, BillingAccount.account_number)
        .join(BillingAccount, Invoice.account_id == BillingAccount.id)
        .filter(Invoice.client_slug == slug)
    )
    if status:
        query = query.filter(Invoice.status == status)
    total = query.count()
    rows = query.order_by(Invoice.issued_at.desc()).offset(skip).limit(limit).all()
    return [_invoice_dict(i, acct_no) for i, acct_no in rows], total


def list_activations(db: Session, slug: str, msisdn: str = "", channel: str = "",
                     skip: int = 0, limit: int = 100):
    query = db.query(PackageActivation).filter(PackageActivation.client_slug == slug)
    if msisdn:
        query = query.filter(PackageActivation.msisdn == msisdn.strip())
    if channel:
        query = query.filter(PackageActivation.channel == channel)
    total = query.count()
    rows = query.order_by(PackageActivation.activated_at.desc()).offset(skip).limit(limit).all()
    return [_activation_dict(a) for a in rows], total


def list_tickets(db: Session, slug: str, status: str = "", priority: str = "",
                 category: str = "", skip: int = 0, limit: int = 100):
    query = (
        db.query(Ticket, Customer.full_name)
        .outerjoin(Customer, Ticket.customer_id == Customer.id)
        .filter(Ticket.client_slug == slug)
    )
    if status:
        query = query.filter(Ticket.status == status)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if category:
        query = query.filter(Ticket.category == category)
    total = query.count()
    open_count = (
        db.query(func.count(Ticket.id))
        .filter(Ticket.client_slug == slug,
                Ticket.status.in_(["open", "in_progress", "pending"])).scalar() or 0
    )
    rows = query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()
    out = []
    for t, cust_name in rows:
        t._customer_name = cust_name
        out.append(_ticket_dict(t))
    return out, total, int(open_count)


def overview_kpis(db: Session, slug: str) -> dict:
    now = datetime.utcnow()
    day_start = datetime(now.year, now.month, now.day)
    month_start = datetime(now.year, now.month, 1)

    def _sub_count(status=None):
        q = db.query(func.count(Subscription.id)).filter(Subscription.client_slug == slug)
        if status:
            q = q.filter(Subscription.status == status)
        return int(q.scalar() or 0)

    customers_total = int(
        db.query(func.count(Customer.id)).filter(Customer.client_slug == slug).scalar() or 0
    )
    tickets_open = int(
        db.query(func.count(Ticket.id)).filter(
            Ticket.client_slug == slug,
            Ticket.status.in_(["open", "in_progress", "pending"])).scalar() or 0
    )
    activations_today = int(
        db.query(func.count(PackageActivation.id)).filter(
            PackageActivation.client_slug == slug,
            PackageActivation.activated_at >= day_start).scalar() or 0
    )

    def _revenue(since):
        # money IN = recharges + package purchases (charged to the customer)
        val = (
            db.query(func.coalesce(func.sum(func.abs(ChargingTransaction.amount)), 0))
            .filter(ChargingTransaction.client_slug == slug,
                    ChargingTransaction.created_at >= since,
                    ChargingTransaction.txn_type.in_(["package_purchase", "recharge", "usage_charge"]))
            .scalar()
        )
        return _f(val)

    tickets_by_status = dict(
        db.query(Ticket.status, func.count(Ticket.id))
        .filter(Ticket.client_slug == slug).group_by(Ticket.status).all()
    )
    recent_acts, _ = list_activations(db, slug, limit=6)
    recent_tix, _, _ = list_tickets(db, slug, limit=6)
    return {
        "subscribers_total": _sub_count(),
        "subscribers_active": _sub_count("active"),
        "subscribers_suspended": _sub_count("suspended"),
        "customers_total": customers_total,
        "tickets_open": tickets_open,
        "activations_today": activations_today,
        "revenue_today": _revenue(day_start),
        "revenue_month": _revenue(month_start),
        "tickets_by_status": {k: int(v) for k, v in tickets_by_status.items()},
        "recent_activations": recent_acts,
        "recent_tickets": recent_tix,
    }


# ---- shared write paths (portal + bot) --------------------------------------

def activate_package(db: Session, slug: str, msisdn: str, package_name: str,
                     price: float = 0, validity_days: int = 30, channel: str = "admin",
                     plan_id: Optional[int] = None) -> Optional[PackageActivation]:
    """Provision a package on a line and keep the money ledger consistent.

    Writes: PackageActivation + a ChargingTransaction (package_purchase) + decrements
    the line's prepaid balance. Returns the activation, or None if the line is unknown.
    Shared by the portal 'activate' endpoint AND the bot's activate_package action.
    """
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    now = datetime.utcnow()
    price_d = Decimal(str(price or 0))
    validity_days = int(validity_days or 30)

    act = PackageActivation(
        client_slug=slug, subscription_id=sub.id, msisdn=sub.msisdn, plan_id=plan_id,
        package_name=package_name, price=price_d, validity_days=validity_days,
        channel=channel, status="active", activated_at=now,
        expires_at=now + timedelta(days=validity_days),
    )
    db.add(act)

    new_balance = Decimal(str(sub.prepaid_balance or 0)) - price_d
    sub.prepaid_balance = new_balance
    txn = ChargingTransaction(
        client_slug=slug, account_id=sub.account_id, subscription_id=sub.id,
        msisdn=sub.msisdn, txn_type="package_purchase",
        description=f"Package activation — {package_name}", amount=-price_d,
        balance_after=new_balance, channel=channel,
    )
    db.add(txn)
    db.flush()  # assign ids for human references
    act.reference = f"ACT-{500000 + act.id}"
    txn.reference = f"TXN-{900000 + txn.id}"
    db.commit()
    db.refresh(act)
    return act


def log_ticket(db: Session, slug: str, subject: str, msisdn: str = "",
               category: str = "general", description: str = "",
               channel: str = "admin", priority: str = "medium") -> Ticket:
    """Create a trouble ticket, linked to the customer/line when the MSISDN is known.

    Shared by the portal 'create ticket' endpoint AND the bot's create_ticket action.
    """
    sub = _get_subscription(db, slug, msisdn) if msisdn else None
    now = datetime.utcnow()
    t = Ticket(
        client_slug=slug, msisdn=(msisdn or None),
        customer_id=sub.customer_id if sub else None,
        subscription_id=sub.id if sub else None,
        # ticket_number is NOT NULL + unique-per-tenant; use a temp token so the
        # INSERT succeeds, then set the final id-based number after we have the id.
        ticket_number=f"TMP-{uuid.uuid4().hex}",
        subject=subject or "Customer issue", category=category or "general",
        priority=priority or "medium", status="open", channel=channel,
        description=description or None, sla_due_at=now + timedelta(days=2),
    )
    db.add(t)
    db.flush()
    t.ticket_number = f"TT-{100000 + t.id}"
    db.commit()
    db.refresh(t)
    return t


def update_subscription(db: Session, slug: str, msisdn: str, *, plan_code: str = None,
                        status: str = None, prepaid_balance: float = None) -> Optional[Subscription]:
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    if plan_code:
        plan = (
            db.query(Plan)
            .filter(Plan.client_slug == slug, Plan.code == plan_code).first()
        )
        if plan:
            sub.plan_id = plan.id
    if status:
        sub.status = status
    if prepaid_balance is not None:
        sub.prepaid_balance = Decimal(str(prepaid_balance))
    db.commit()
    db.refresh(sub)
    return sub


def update_ticket(db: Session, slug: str, ticket_id: int, *, status: str = None,
                  priority: str = None, assigned_to: str = None,
                  resolution: str = None) -> Optional[Ticket]:
    t = db.get(Ticket, ticket_id)
    if t is None or t.client_slug != slug:
        return None
    if status:
        t.status = status
        if status in ("resolved", "closed") and not t.resolved_at:
            t.resolved_at = datetime.utcnow()
    if priority:
        t.priority = priority
    if assigned_to is not None:
        t.assigned_to = assigned_to
    if resolution is not None:
        t.resolution = resolution
    db.commit()
    db.refresh(t)
    return t


def lookup_account_summary(db: Session, slug: str, msisdn: str) -> Optional[str]:
    """Human-readable one-liner for the bot's lookup_account action (telecom)."""
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    plan = sub.plan.name if sub.plan else "no plan"
    who = sub.customer.full_name if sub.customer else sub.msisdn
    data_gb = round((sub.data_balance_mb or 0) / 1024, 1)
    return (
        f"Account for {who} ({sub.msisdn}) — plan: {plan}; status: {sub.status}; "
        f"prepaid balance: LKR {_f(sub.prepaid_balance):.2f}; data remaining: {data_gb} GB."
    )


def call_history_summary(db: Session, slug: str, msisdn: str, date: str = "") -> Optional[str]:
    """Human-readable call/SMS/data usage log for the bot's check_call_history action.

    `date` is an optional YYYY-MM-DD string (the model resolves relative dates like
    "10th of July" against today's date, which is injected into its system prompt).
    """
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None

    query = db.query(CallDetailRecord).filter(CallDetailRecord.subscription_id == sub.id)
    day = None
    if date:
        try:
            day = datetime.strptime(date.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            day = None
    if day:
        start = datetime.combine(day, datetime.min.time())
        query = query.filter(CallDetailRecord.start_time >= start,
                              CallDetailRecord.start_time < start + timedelta(days=1))
    rows = query.order_by(CallDetailRecord.start_time.desc()).limit(20).all()

    when = f" on {day.strftime('%b %d, %Y')}" if day else ""
    if not rows:
        return f"No calls, SMS, or data sessions found on {sub.msisdn}{when}."

    lines = []
    for r in rows:
        ts = r.start_time.strftime("%b %d, %Y %H:%M")
        if r.event_type == "voice":
            lines.append(f"- {ts}: {r.direction} call with {r.other_party}, "
                         f"{r.duration_sec}s, LKR {_f(r.charged_amount):.2f}")
        elif r.event_type == "sms":
            lines.append(f"- {ts}: SMS ({r.direction}) with {r.other_party}")
        else:
            mb = round((r.bytes_used or 0) / (1024 * 1024), 1)
            lines.append(f"- {ts}: data session, {mb} MB, LKR {_f(r.charged_amount):.2f}")
    return f"Activity for {sub.msisdn}{when}:\n" + "\n".join(lines)


def recharge_balance(db: Session, slug: str, msisdn: str, amount: float,
                     channel: str = "chatbot") -> Optional[Subscription]:
    """Top up prepaid balance and post the matching ledger entry. Shared by the
    portal and the bot's recharge_balance action."""
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    amt = Decimal(str(amount or 0))
    new_balance = Decimal(str(sub.prepaid_balance or 0)) + amt
    sub.prepaid_balance = new_balance
    txn = ChargingTransaction(
        client_slug=slug, account_id=sub.account_id, subscription_id=sub.id,
        msisdn=sub.msisdn, txn_type="recharge",
        description=f"Recharge via {channel}", amount=amt,
        balance_after=new_balance, channel=channel,
    )
    db.add(txn)
    db.flush()
    txn.reference = f"TXN-{900000 + txn.id}"
    db.commit()
    db.refresh(sub)
    return sub


def update_customer_contact(db: Session, slug: str, msisdn: str, *, email: str = None,
                            address: str = None) -> Optional[Customer]:
    """Update the contact details of the customer that owns this line."""
    sub = _get_subscription(db, slug, msisdn)
    if sub is None or sub.customer is None:
        return None
    cust = sub.customer
    if email:
        cust.email = email
    if address:
        cust.address = address
    db.commit()
    db.refresh(cust)
    return cust


def billing_summary(db: Session, slug: str, msisdn: str, limit: int = 10) -> Optional[str]:
    """Readable ledger + unpaid-invoice snapshot for the bot's check_billing action."""
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    lines = [f"Current prepaid balance: LKR {_f(sub.prepaid_balance):.2f}."]
    txns = (
        db.query(ChargingTransaction)
        .filter(ChargingTransaction.subscription_id == sub.id)
        .order_by(ChargingTransaction.created_at.desc()).limit(limit).all()
    )
    if txns:
        lines.append("Recent transactions:")
        for t in txns:
            sign = "+" if (t.amount or 0) >= 0 else ""
            lines.append(f"- {t.created_at:%b %d, %Y}: {t.txn_type} {sign}LKR {_f(t.amount):.2f} "
                         f"({t.description}), balance after LKR {_f(t.balance_after):.2f}")
    if sub.account_id:
        invoices = (
            db.query(Invoice)
            .filter(Invoice.account_id == sub.account_id, Invoice.status != "paid")
            .order_by(Invoice.due_date.asc()).all()
        )
        if invoices:
            lines.append("Unpaid invoices:")
            for i in invoices:
                lines.append(f"- {i.invoice_number}: LKR {_f(i.amount_due):.2f} due "
                             f"{i.due_date:%b %d, %Y} ({i.status})")
    return "\n".join(lines)


def activation_history_summary(db: Session, slug: str, msisdn: str, limit: int = 10) -> Optional[str]:
    """Readable package-activation history for the bot's check_activation_history action."""
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    acts = (
        db.query(PackageActivation)
        .filter(PackageActivation.subscription_id == sub.id)
        .order_by(PackageActivation.activated_at.desc()).limit(limit).all()
    )
    if not acts:
        return f"No package activations found for {sub.msisdn}."
    lines = [f"Activation history for {sub.msisdn}:"]
    for a in acts:
        lines.append(f"- {a.activated_at:%b %d, %Y}: {a.package_name} (LKR {_f(a.price):.2f}, "
                     f"{a.validity_days} days) — {a.status}, ref {a.reference}")
    return "\n".join(lines)


def tickets_summary_for_msisdn(db: Session, slug: str, msisdn: str, limit: int = 10) -> Optional[str]:
    """Readable ticket list for the bot's list_my_tickets action."""
    sub = _get_subscription(db, slug, msisdn)
    if sub is None:
        return None
    tix = (
        db.query(Ticket)
        .filter(Ticket.msisdn == sub.msisdn)
        .order_by(Ticket.created_at.desc()).limit(limit).all()
    )
    if not tix:
        return f"No tickets found for {sub.msisdn}."
    lines = [f"Tickets for {sub.msisdn}:"]
    for t in tix:
        lines.append(f"- {t.ticket_number}: {t.subject} — {t.status} ({t.category}, {t.priority})")
    return "\n".join(lines)


# ---- seed (large, deterministic, idempotent) --------------------------------

_PLANS = [
    ("STARTER", "Starter", "prepaid", 8, 3072, 60, 50, 30),
    ("SMART_15", "Smart 15", "prepaid", 15, 10240, 150, 150, 30),
    ("VALUE_20", "Value 20", "prepaid", 20, 25600, 300, 300, 30),
    ("DATA_MAX", "Data Max", "prepaid", 35, 51200, 500, 500, 30),
    ("UNLIMITED_PRO", "Unlimited Pro", "postpaid", 45, 0, 0, 0, 30),
    ("BUSINESS_PLUS", "Business Plus", "postpaid", 60, 102400, 0, 0, 30),
]

_FIRST = ["Nimal", "Kamal", "Sunil", "Priya", "Sara", "Ashen", "Dilani", "Ruwan",
          "Tharindu", "Ishara", "Kasun", "Nadeesha", "Chamara", "Hasini", "Roshan",
          "Amaya", "Sanjaya", "Kavindi", "Buddhika", "Sachini", "Lahiru", "Menaka",
          "Dinesh", "Tharuka", "Gayan", "Nethmi", "Pasan", "Shanika", "Isuru", "Devon"]
_LAST = ["Fernando", "Perera", "Silva", "Jayasuriya", "Bandara", "Wickramasinghe",
         "Gunawardena", "Rajapaksa", "Dissanayake", "Kumara", "Herath", "Ekanayake",
         "Senanayake", "Weerasinghe", "Amarasinghe", "Rathnayake"]
_CITIES = ["Colombo", "Kandy", "Galle", "Jaffna", "Negombo", "Kurunegala", "Matara",
           "Anuradhapura", "Batticaloa", "Ratnapura", "Gampaha", "Kalutara"]
_CELLS = ["COL-042", "COL-118", "KAN-007", "GAL-023", "JAF-014", "NEG-031",
          "KUR-009", "MAT-017", "GMP-052", "KAL-028"]
_TICKET_SUBJECTS = [
    ("Slow data speeds in my area", "network", "high"),
    ("Was charged twice for a recharge", "billing", "urgent"),
    ("Package activation not working", "activation", "high"),
    ("Request SIM replacement", "sim", "medium"),
    ("Unable to make outgoing calls", "network", "urgent"),
    ("Bill higher than expected", "billing", "medium"),
    ("Roaming not activated", "activation", "medium"),
    ("Frequent call drops", "network", "high"),
    ("Wrong plan applied to my number", "billing", "high"),
    ("No coverage at home", "network", "medium"),
    ("Data balance disappeared", "billing", "high"),
    ("Requesting upgrade to postpaid", "general", "low"),
]
_TICKET_STATUS = ["open", "in_progress", "pending", "resolved", "closed"]
_AGENTS = ["A. Silva", "R. Perera", "M. Fernando", "D. Jayasuriya", "Unassigned"]


def _delete_tenant_telecom(db: Session, slug: str) -> None:
    """Remove this tenant's telecom rows (FK-safe order) for idempotent re-seed."""
    for model in (Ticket, PackageActivation, Invoice, ChargingTransaction,
                  CallDetailRecord, Subscription, BillingAccount, Customer, Plan):
        db.query(model).filter(model.client_slug == slug).delete()
    db.commit()


def seed_telecom_demo(db: Session, slug: str, num_customers: int = 50) -> dict:
    """Insert a large, consistent, realistic telecom dataset for one tenant.

    Deterministic (seeded RNG) and idempotent (clears the tenant's telecom rows
    first). Also flips the client to domain='telecom' so the portal shows the full
    enterprise suite. Returns a small summary of what was created.
    """
    rng = random.Random(42)
    _delete_tenant_telecom(db, slug)
    now = datetime.utcnow()

    # 1) Plan catalog
    plans: List[Plan] = []
    for code, name, ptype, rental, data_mb, mins, sms, valid in _PLANS:
        p = Plan(client_slug=slug, code=code, name=name, plan_type=ptype,
                 monthly_rental=Decimal(str(rental)), data_quota_mb=data_mb,
                 voice_minutes=mins, sms_units=sms, validity_days=valid, is_active=True)
        db.add(p)
        plans.append(p)
    db.flush()  # ids

    used_msisdn = set()

    def _new_msisdn() -> str:
        while True:
            m = "07" + str(rng.choice([0, 1, 2, 4, 5, 6, 7, 8])) + "".join(
                str(rng.randint(0, 9)) for _ in range(7))
            if m not in used_msisdn:
                used_msisdn.add(m)
                return m

    subscriptions: List[Subscription] = []
    accounts: List[BillingAccount] = []
    cdrs: List[CallDetailRecord] = []
    txns: List[ChargingTransaction] = []
    activations: List[PackageActivation] = []
    invoices: List[Invoice] = []
    # Reference-number counters. Seed history uses the same ranges runtime uses
    # (id-based: TT-100000+id, ACT-500000+id, INV-200000+id) so live rows created
    # later by the portal/bot continue the sequence without collisions.
    act_seq = 0
    inv_seq = 0

    # 2) Customers -> accounts -> subscriptions -> usage/ledger/activations
    for i in range(num_customers):
        name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        city = rng.choice(_CITIES)
        ctype = "corporate" if rng.random() < 0.12 else "individual"
        cust = Customer(
            client_slug=slug, full_name=name,
            nic=f"{rng.randint(70, 99)}{rng.randint(1000000, 9999999)}V",
            email=f"{name.split()[0].lower()}{rng.randint(1, 999)}@example.lk",
            phone=_new_msisdn(), address=f"{rng.randint(1, 400)}, {city}", city=city,
            customer_type=ctype, kyc_status="verified" if rng.random() < 0.9 else "pending",
            created_at=now - timedelta(days=rng.randint(30, 1400)),
        )
        db.add(cust)
        db.flush()  # cust.id

        acct = BillingAccount(
            client_slug=slug, customer_id=cust.id,
            account_number=f"ACC-{100000 + i}",
            billing_cycle_day=rng.choice([1, 5, 10, 15, 20]),
            payment_method=rng.choice(["prepaid", "credit_card", "direct_debit"]),
            status="active", currency="LKR",
            credit_limit=Decimal(str(rng.choice([0, 5000, 10000, 25000]))),
            current_balance=Decimal("0"),
        )
        db.add(acct)
        db.flush()  # acct.id
        accounts.append(acct)

        for _ in range(1 if rng.random() < 0.7 else 2):
            plan = rng.choice(plans)
            status = "active" if rng.random() < 0.82 else rng.choice(["suspended", "deactivated"])
            balance = Decimal(str(round(rng.uniform(0, 3000), 2))) if plan.plan_type == "prepaid" else Decimal("0")
            sub = Subscription(
                client_slug=slug, customer_id=cust.id, account_id=acct.id, plan_id=plan.id,
                msisdn=_new_msisdn(), imsi=f"41303{rng.randint(1000000000, 9999999999)}",
                sim_iccid=f"8994{rng.randint(10**14, 10**15 - 1)}", status=status,
                activation_date=now - timedelta(days=rng.randint(10, 1200)),
                prepaid_balance=balance,
                data_balance_mb=rng.randint(0, plan.data_quota_mb or 20480),
            )
            db.add(sub)
            db.flush()  # sub.id
            subscriptions.append(sub)

            # CDRs (usage events) over the last 30 days
            running = balance
            for _ in range(rng.randint(15, 40)):
                etype = rng.choices(["voice", "data", "sms", "video"],
                                    weights=[40, 40, 15, 5])[0]
                when = now - timedelta(days=rng.randint(0, 30),
                                       hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
                dur = rng.randint(20, 900) if etype in ("voice", "video") else 0
                by = rng.randint(1_000_000, 800_000_000) if etype == "data" else 0
                charge = Decimal(str(round(
                    (dur / 60 * rng.uniform(1.5, 3)) if etype in ("voice", "video")
                    else (by / 1_000_000 * rng.uniform(0.2, 0.6)) if etype == "data"
                    else rng.uniform(1, 3), 2)))
                cdrs.append(CallDetailRecord(
                    client_slug=slug, subscription_id=sub.id, msisdn=sub.msisdn,
                    direction=rng.choice(["inbound", "outbound"]),
                    other_party=_new_msisdn() if rng.random() < 0.6 else f"07{rng.randint(10**7, 10**8 - 1)}",
                    event_type=etype, start_time=when, duration_sec=dur, bytes_used=by,
                    rate_applied=Decimal(str(round(rng.uniform(0.2, 3), 4))),
                    charged_amount=charge, cell_site=rng.choice(_CELLS),
                ))

            # Charging ledger: recharges + usage charges
            for _ in range(rng.randint(10, 30)):
                when = now - timedelta(days=rng.randint(0, 30),
                                       hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
                if rng.random() < 0.35:
                    amt = Decimal(str(rng.choice([100, 200, 300, 500, 1000])))
                    ttype, desc = "recharge", "Prepaid recharge"
                else:
                    amt = Decimal(str(-round(rng.uniform(2, 60), 2)))
                    ttype, desc = "usage_charge", rng.choice(
                        ["Voice usage", "Data usage", "SMS bundle", "Value added service"])
                running = running + amt
                txns.append(ChargingTransaction(
                    client_slug=slug, account_id=acct.id, subscription_id=sub.id,
                    msisdn=sub.msisdn, txn_type=ttype, description=desc, amount=amt,
                    balance_after=running, channel=rng.choice(["ussd", "app", "web", "system"]),
                    created_at=when,
                ))

            # Package activation history
            for _ in range(rng.randint(1, 3)):
                aplan = rng.choice(plans)
                a_when = now - timedelta(days=rng.randint(0, 60))
                act_seq += 1
                activations.append(PackageActivation(
                    client_slug=slug, subscription_id=sub.id, msisdn=sub.msisdn,
                    plan_id=aplan.id, package_name=aplan.name,
                    price=aplan.monthly_rental, validity_days=aplan.validity_days,
                    channel=rng.choice(["ussd", "app", "agent", "web"]),
                    status=rng.choices(["active", "expired"], weights=[60, 40])[0],
                    activated_at=a_when,
                    expires_at=a_when + timedelta(days=aplan.validity_days),
                    reference=f"ACT-{500000 + act_seq}",
                ))

            # Postpaid invoices
            if plan.plan_type == "postpaid" and rng.random() < 0.8:
                for m in range(rng.randint(1, 2)):
                    p_end = datetime(now.year, now.month, 1) - timedelta(days=30 * m)
                    p_start = p_end - timedelta(days=30)
                    inv_seq += 1
                    invoices.append(Invoice(
                        client_slug=slug, account_id=acct.id,
                        invoice_number=f"INV-{200000 + inv_seq}",
                        period_start=p_start, period_end=p_end,
                        amount_due=Decimal(str(round(rng.uniform(1500, 12000), 2))),
                        due_date=p_end + timedelta(days=14),
                        status=rng.choices(["paid", "unpaid", "overdue"], weights=[60, 30, 10])[0],
                        issued_at=p_end,
                    ))

    db.add_all(cdrs + txns + activations + invoices)

    # 3) Trouble tickets across random subscribers
    tickets: List[Ticket] = []
    for tkt_seq in range(1, max(30, num_customers) + 1):
        sub = rng.choice(subscriptions)
        subj, cat, prio = rng.choice(_TICKET_SUBJECTS)
        st = rng.choices(_TICKET_STATUS, weights=[30, 20, 12, 25, 13])[0]
        created = now - timedelta(days=rng.randint(0, 45), hours=rng.randint(0, 23))
        tickets.append(Ticket(
            client_slug=slug, customer_id=sub.customer_id, subscription_id=sub.id,
            msisdn=sub.msisdn, ticket_number=f"TT-{100000 + tkt_seq}",
            subject=subj, category=cat, priority=prio, status=st,
            channel=rng.choice(["chatbot", "voice", "whatsapp", "admin"]),
            assigned_to=None if st == "open" else rng.choice(_AGENTS),
            description=f"Customer reported: {subj.lower()}.",
            resolution="Resolved and confirmed with customer." if st in ("resolved", "closed") else None,
            sla_due_at=created + timedelta(days=2), created_at=created, updated_at=created,
            resolved_at=(created + timedelta(days=rng.randint(1, 5))) if st in ("resolved", "closed") else None,
        ))
    db.add_all(tickets)

    # Flip the client to telecom so the portal renders the full enterprise suite.
    client = db.get(Client, slug)
    if client is not None:
        client.domain = "telecom"

    db.commit()
    return {
        "plans": len(plans),
        "customers": num_customers,
        "subscriptions": len(subscriptions),
        "cdrs": len(cdrs),
        "transactions": len(txns),
        "activations": len(activations),
        "invoices": len(invoices),
        "tickets": len(tickets),
    }
