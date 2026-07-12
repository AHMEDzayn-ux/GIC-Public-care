"""
Transactional actions — the agent's "do things" tools, per-domain (mock-first).

An action is just a TOOL we hand the LLM: a name + description + JSON parameter
schema (same shape as `search_knowledge_base` / `escalate_to_human`). The agent
decides when to call one and collects the arguments conversationally. This module:

  - declares the per-domain tool set        -> get_action_tools(domain)
  - executes a called tool by its KIND      -> execute_action(...)

Every tool maps to one of four generic KINDS, so adding a domain/action is data,
not new execution code:

  ticket          -> log a request, return a reference number
  callback        -> log a callback request, return a reference
  account_lookup  -> read a (mock) account, return its details   (read-only, not recorded)
  account_change  -> update a (mock) account, return confirmation (recorded)

MOCK-FIRST: account handlers read/write the seeded `MockAccount` table. Swap a
handler body for a real API call to go live — nothing else changes.
"""

from typing import Any, Dict, List, Optional

from database import SessionLocal
from services import client_store
from logger import get_logger

logger = get_logger(__name__)

# Reference prefixes per kind (e.g. TKT-1042).
_REF_PREFIX = {"ticket": "TKT", "callback": "CB", "account_change": "CHG"}


def _tool(name: str, kind: str, description: str, properties: dict,
          required: List[str], confirm: bool = False) -> dict:
    """Build one action spec (OpenAI function-calling schema + our metadata)."""
    return {
        "kind": kind,
        "confirm": confirm,
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        },
    }


# Reusable parameter fragments.
_IDENT_PHONE = {"identifier": {"type": "string", "description": "The customer's phone number"}}
_IDENT_APPID = {"identifier": {"type": "string", "description": "The application ID or email"}}
_IDENT_GENERIC = {"identifier": {"type": "string", "description": "The customer's account email or ID"}}

# Identity-verified variant: required for any telecom tool that reveals or changes
# account-specific data (history, billing, tickets, line changes, etc.) — a phone
# number alone isn't proof of ownership. General package/plan/policy Q&A doesn't
# use this and needs no verification.
_IDENT_PHONE_VERIFIED = {
    **_IDENT_PHONE,
    "nic": {"type": "string", "description": (
        "The customer's NIC (National ID card number) on file for this line. You MUST ask "
        "for this and it MUST match before you may reveal or change anything account-specific "
        "— never proceed on the phone number alone. If they don't know it or it doesn't match, "
        "decline and offer to log a ticket or connect them with a human agent instead."
    )},
}


# ---- Per-domain registry ----------------------------------------------------

ACTIONS: Dict[str, List[dict]] = {
    "telecom": [
        _tool("create_ticket", "ticket",
              "Log a support ticket for a problem, complaint, fault, or cancellation request "
              "the customer wants recorded. Gather a clear subject and details first.",
              {"subject": {"type": "string", "description": "Short title of the issue"},
               "details": {"type": "string", "description": "What the customer reported"},
               "phone": {"type": "string", "description": "Customer's phone number, if known (links the ticket to their account)"}},
              ["subject"]),
        _tool("request_callback", "callback",
              "Schedule a phone callback from a human agent.",
              {"phone": {"type": "string", "description": "Number to call back"},
               "topic": {"type": "string", "description": "What it's about"},
               "name": {"type": "string", "description": "Customer name if given"}},
              ["phone"]),
        _tool("lookup_account", "account_lookup",
              "Look up the customer's account to report their plan, balance, due date, data "
              "usage, or status. Requires the customer's phone number AND their NIC to verify "
              "identity first.",
              dict(_IDENT_PHONE_VERIFIED), ["identifier", "nic"]),
        _tool("change_plan", "account_change",
              "Change the customer's mobile plan to a new plan. Confirm the exact new plan "
              "with the customer BEFORE calling this. Requires their phone number AND their NIC "
              "to verify identity first.",
              {**_IDENT_PHONE_VERIFIED, "new_plan": {"type": "string", "description": "The plan to switch to"}},
              ["identifier", "nic", "new_plan"], confirm=True),
        _tool("activate_package", "activation",
              "Activate/purchase a data or voice package on the customer's line. Confirm the "
              "exact package with the customer BEFORE calling this. Requires their phone number, "
              "the package name, AND their NIC to verify identity first.",
              {**_IDENT_PHONE_VERIFIED,
               "package": {"type": "string", "description": "The package/plan name to activate"}},
              ["identifier", "nic", "package"], confirm=True),
        _tool("check_ticket", "request_status",
              "Check the status/progress of a previously logged ticket, activation, or change "
              "using its reference number (e.g. TT-100045, ACT-500012, CB-1002).",
              {"reference": {"type": "string", "description": "The reference number to check"}},
              ["reference"]),
        _tool("check_call_history", "call_history",
              "Look up the customer's call, SMS, or data usage log/history — e.g. to confirm "
              "whether they made a call, on a specific date, or to review recent activity. "
              "Requires their phone number AND their NIC to verify identity first.",
              {**_IDENT_PHONE_VERIFIED,
               "date": {"type": "string", "description": "Specific date to check, as YYYY-MM-DD, "
                                                          "if the customer mentions one (resolve "
                                                          "relative dates against today's date)"}},
              ["identifier", "nic"]),
        _tool("check_billing", "billing_history",
              "Check the customer's billing/ledger history — recent charges, recharges, and any "
              "unpaid invoices. Requires their phone number AND their NIC to verify identity first.",
              dict(_IDENT_PHONE_VERIFIED), ["identifier", "nic"]),
        _tool("check_activation_history", "activation_history",
              "Look up everything the customer has activated/purchased over time on their line. "
              "Requires their phone number AND their NIC to verify identity first.",
              dict(_IDENT_PHONE_VERIFIED), ["identifier", "nic"]),
        _tool("list_my_tickets", "list_tickets",
              "List all support tickets/complaints logged for this customer's line, with current "
              "status — use when they ask what tickets they have open, not just one reference. "
              "Requires their phone number AND their NIC to verify identity first.",
              dict(_IDENT_PHONE_VERIFIED), ["identifier", "nic"]),
        _tool("recharge_balance", "recharge",
              "Top up / recharge the customer's prepaid balance. Confirm the exact amount with "
              "the customer BEFORE calling this. Requires their phone number, the amount in LKR, "
              "AND their NIC to verify identity first.",
              {**_IDENT_PHONE_VERIFIED, "amount": {"type": "number", "description": "Amount to add, in LKR"}},
              ["identifier", "nic", "amount"], confirm=True),
        _tool("suspend_line", "suspend",
              "Suspend the customer's line, e.g. for a lost or stolen phone/SIM. Confirm with the "
              "customer BEFORE calling this. Requires their phone number AND their NIC to verify "
              "identity first.",
              dict(_IDENT_PHONE_VERIFIED), ["identifier", "nic"], confirm=True),
        _tool("reactivate_line", "reactivate",
              "Reactivate a suspended line. Confirm with the customer BEFORE calling this. "
              "Requires their phone number AND their NIC to verify identity first.",
              dict(_IDENT_PHONE_VERIFIED), ["identifier", "nic"], confirm=True),
        _tool("update_contact_info", "contact_update",
              "Update the customer's email and/or address on file. Confirm the exact new details "
              "with the customer BEFORE calling this. Requires their phone number AND their NIC "
              "to verify identity first.",
              {**_IDENT_PHONE_VERIFIED,
               "email": {"type": "string", "description": "New email address, if changing"},
               "address": {"type": "string", "description": "New address, if changing"}},
              ["identifier", "nic"], confirm=True),
    ],
    "university": [
        _tool("create_request", "ticket",
              "Log a student request or issue (document request, complaint, general query to "
              "record). Gather a clear subject and details first.",
              {"subject": {"type": "string", "description": "Short title of the request"},
               "details": {"type": "string", "description": "Details of the request"}},
              ["subject"]),
        _tool("book_advisor", "callback",
              "Book a callback / advisor appointment for the student.",
              {"phone": {"type": "string", "description": "Contact number"},
               "topic": {"type": "string", "description": "What they need advice on"},
               "name": {"type": "string", "description": "Student name if given"}},
              ["phone"]),
        _tool("lookup_application", "account_lookup",
              "Look up a student's application/enrollment to report program, status, intake, or "
              "fees due. Requires their application ID or email.",
              dict(_IDENT_APPID), ["identifier"]),
        _tool("update_enrollment", "account_change",
              "Make an enrollment change (e.g. add or drop a course). Confirm the exact change "
              "with the student BEFORE calling this. Requires their application ID or email.",
              {**_IDENT_APPID, "change": {"type": "string", "description": "The enrollment change to make"}},
              ["identifier", "change"], confirm=True),
        _tool("check_request", "request_status",
              "Check the status/progress of a previously logged request, appointment, or change "
              "using its reference number (e.g. TKT-1005, CB-1002).",
              {"reference": {"type": "string", "description": "The reference number to check"}},
              ["reference"]),
    ],
    "generic": [
        _tool("create_ticket", "ticket",
              "Log a support ticket for any request or issue the customer wants recorded.",
              {"subject": {"type": "string", "description": "Short title of the issue"},
               "details": {"type": "string", "description": "What the customer reported"}},
              ["subject"]),
        _tool("request_callback", "callback",
              "Schedule a callback from a human agent.",
              {"phone": {"type": "string", "description": "Number to call back"},
               "topic": {"type": "string", "description": "What it's about"},
               "name": {"type": "string", "description": "Customer name if given"}},
              ["phone"]),
        _tool("lookup_account", "account_lookup",
              "Look up the customer's account to report their status, plan, or balance. "
              "Requires an account email or ID.",
              dict(_IDENT_GENERIC), ["identifier"]),
        _tool("update_account", "account_change",
              "Make a change to the customer's account. Confirm the exact change with the "
              "customer BEFORE calling this. Requires an account email or ID.",
              {**_IDENT_GENERIC, "change": {"type": "string", "description": "The change to make"}},
              ["identifier", "change"], confirm=True),
        _tool("check_ticket", "request_status",
              "Check the status/progress of a previously logged ticket, callback, or change "
              "using its reference number (e.g. TKT-1005).",
              {"reference": {"type": "string", "description": "The reference number to check"}},
              ["reference"]),
    ],
}


def _specs(domain: str) -> List[dict]:
    return ACTIONS.get((domain or "generic").lower(), ACTIONS["generic"])


def _spec(domain: str, name: str) -> Optional[dict]:
    for s in _specs(domain):
        if s["schema"]["function"]["name"] == name:
            return s
    return None


def get_action_tools(domain: str) -> List[dict]:
    """OpenAI function-schema tool defs to bind for this domain's agent."""
    return [s["schema"] for s in _specs(domain)]


def is_action(domain: str, name: str) -> bool:
    return _spec(domain, name) is not None


# ---- Execution --------------------------------------------------------------

def _format_account(acct) -> str:
    data = acct.data or {}
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in data.items()]
    who = acct.name or acct.identifier
    return f"Account for {who} — " + "; ".join(parts) if parts else f"Account for {who} found."


def _execute_telecom(db, client_slug: str, name: str, kind: str,
                     args: Dict[str, Any]) -> Optional[str]:
    """Telecom actions run on the live BSS/OSS DB (services/telecom_store), shared
    with the admin portal. Returns a reply string, or None to fall back to the
    generic handler (e.g. callback, which has no telecom table)."""
    from services import telecom_store
    from telecom_models import Plan, Ticket, PackageActivation

    identifier = (args.get("identifier") or "").strip()

    # Identity verification gate — enforced here in code, not just left to the
    # model's judgment (a tool schema saying "nic required" doesn't stop a model
    # from calling it without one). Any kind that reveals or changes a specific
    # customer's account data must pass this before doing anything else.
    _SENSITIVE_KINDS = {
        "account_lookup", "account_change", "activation", "call_history",
        "billing_history", "activation_history", "list_tickets", "recharge",
        "suspend", "reactivate", "contact_update",
    }
    if kind in _SENSITIVE_KINDS:
        nic = (args.get("nic") or "").strip()
        if not identifier or not nic:
            return ("For your security, I need both the phone number and the NIC on file for "
                    "this account before I can share or change anything on it.")
        if not telecom_store.verify_identity(db, client_slug, identifier, nic):
            return ("That NIC doesn't match our records for this line, so I can't share or "
                    "change anything on it. Please double-check the NIC with the customer, or "
                    "I can log a ticket / connect them with a human agent instead.")

    if kind == "account_lookup":
        if not identifier:
            return "I need the customer's phone number to look up their account."
        summary = telecom_store.lookup_account_summary(db, client_slug, identifier)
        return summary or (f"No account was found for '{identifier}'. "
                           "Please double-check the number with the customer.")

    if kind == "account_change":  # change_plan
        if not identifier:
            return "I need the customer's phone number to change their plan."
        new_plan = (args.get("new_plan") or "").strip()
        plan = (db.query(Plan)
                .filter(Plan.client_slug == client_slug)
                .filter((Plan.name.ilike(f"%{new_plan}%")) | (Plan.code.ilike(f"%{new_plan}%")))
                .first()) if new_plan else None
        if plan is None:
            return (f"I couldn't find a plan matching '{new_plan}' in the catalog. "
                    "Ask the customer which package they'd like from our available plans.")
        sub = telecom_store.update_subscription(db, client_slug, identifier, plan_code=plan.code)
        if sub is None:
            return f"No account was found for '{identifier}'. Double-check the number."
        return f"Done — {sub.msisdn} is now on {plan.name}. The change is active immediately."

    if kind == "activation":  # activate_package
        package = (args.get("package") or "").strip()
        if not identifier or not package:
            return "I need the customer's phone number and the package name to activate it."
        plan = (db.query(Plan)
                .filter(Plan.client_slug == client_slug)
                .filter((Plan.name.ilike(f"%{package}%")) | (Plan.code.ilike(f"%{package}%")))
                .first())
        price = float(plan.monthly_rental) if plan else 0
        validity = plan.validity_days if plan else 30
        act = telecom_store.activate_package(
            db, client_slug, identifier, package_name=(plan.name if plan else package),
            price=price, validity_days=validity, channel="chatbot",
            plan_id=plan.id if plan else None)
        if act is None:
            return f"No account was found for '{identifier}'. Double-check the number."
        return (f"Activated {act.package_name} on {act.msisdn} — reference {act.reference}. "
                f"It's valid for {act.validity_days} days"
                + (f" and LKR {price:.2f} was charged." if price else "."))

    if kind == "ticket":  # create_ticket
        subject = (args.get("subject") or "Customer issue").strip()
        details = (args.get("details") or "").strip()
        phone = (args.get("phone") or "").strip()
        t = telecom_store.log_ticket(
            db, client_slug, subject=subject, msisdn=phone, category="complaint",
            description=details, channel="chatbot")
        return (f"Logged the complaint — reference {t.ticket_number}. "
                "Our team will follow up.")

    if kind == "call_history":  # check_call_history
        if not identifier:
            return "I need the customer's phone number to check their call history."
        date = (args.get("date") or "").strip()
        summary = telecom_store.call_history_summary(db, client_slug, identifier, date=date)
        return summary or (f"No account was found for '{identifier}'. "
                           "Please double-check the number with the customer.")

    if kind == "billing_history":  # check_billing
        if not identifier:
            return "I need the customer's phone number to check their billing."
        summary = telecom_store.billing_summary(db, client_slug, identifier)
        return summary or (f"No account was found for '{identifier}'. "
                           "Please double-check the number with the customer.")

    if kind == "activation_history":  # check_activation_history
        if not identifier:
            return "I need the customer's phone number to check their activation history."
        summary = telecom_store.activation_history_summary(db, client_slug, identifier)
        return summary or (f"No account was found for '{identifier}'. "
                           "Please double-check the number with the customer.")

    if kind == "list_tickets":  # list_my_tickets
        if not identifier:
            return "I need the customer's phone number to list their tickets."
        summary = telecom_store.tickets_summary_for_msisdn(db, client_slug, identifier)
        return summary or (f"No account was found for '{identifier}'. "
                           "Please double-check the number with the customer.")

    if kind == "recharge":  # recharge_balance
        if not identifier:
            return "I need the customer's phone number to process the recharge."
        try:
            amount = float(args.get("amount"))
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return "Please confirm a valid recharge amount with the customer first."
        sub = telecom_store.recharge_balance(db, client_slug, identifier, amount, channel="chatbot")
        if sub is None:
            return f"No account was found for '{identifier}'. Double-check the number."
        return (f"Recharged LKR {amount:.2f} on {sub.msisdn}. "
                f"New balance: LKR {float(sub.prepaid_balance or 0):.2f}.")

    if kind == "suspend":  # suspend_line
        if not identifier:
            return "I need the customer's phone number to suspend the line."
        sub = telecom_store.update_subscription(db, client_slug, identifier, status="suspended")
        if sub is None:
            return f"No account was found for '{identifier}'. Double-check the number."
        return f"{sub.msisdn} has been suspended. It can be reactivated anytime on request."

    if kind == "reactivate":  # reactivate_line
        if not identifier:
            return "I need the customer's phone number to reactivate the line."
        sub = telecom_store.update_subscription(db, client_slug, identifier, status="active")
        if sub is None:
            return f"No account was found for '{identifier}'. Double-check the number."
        return f"{sub.msisdn} is now active again."

    if kind == "contact_update":  # update_contact_info
        if not identifier:
            return "I need the customer's phone number to update their contact details."
        email = (args.get("email") or "").strip()
        address = (args.get("address") or "").strip()
        if not email and not address:
            return "Please tell me what to update — a new email and/or address."
        cust = telecom_store.update_customer_contact(
            db, client_slug, identifier, email=email or None, address=address or None)
        if cust is None:
            return f"No account was found for '{identifier}'. Double-check the number."
        changed = " and ".join(filter(None, [f"email to {email}" if email else None,
                                             f"address to {address}" if address else None]))
        return f"Updated {cust.full_name}'s {changed}."

    if kind == "request_status":  # check_ticket
        ref = (args.get("reference") or "").strip()
        norm = "".join(c for c in ref.upper() if c.isalnum())
        if not norm:
            return "Please share the reference number so I can check it."
        t = (db.query(Ticket)
             .filter(Ticket.client_slug == client_slug, Ticket.ticket_number.ilike(f"%{ref}%"))
             .first())
        if t:
            return f"{t.ticket_number} ({t.subject}) is currently '{t.status}'."
        a = (db.query(PackageActivation)
             .filter(PackageActivation.client_slug == client_slug,
                     PackageActivation.reference.ilike(f"%{ref}%")).first())
        if a:
            return f"{a.reference} — {a.package_name} is '{a.status}' (activated {a.activated_at:%b %d, %Y})."
        return None  # fall back to generic ActionRequest lookup (e.g. CB- callbacks)

    return None  # callback etc. → generic handler


def execute_action(client_slug: str, session_id: Optional[str], name: str,
                   args: Dict[str, Any], domain: str) -> str:
    """Run a called action and return a short result string for the agent to relay.

    Opens its own DB session (like _record_escalation). Never raises — returns a
    plain message the model can read out even on failure.
    """
    spec = _spec(domain, name)
    if spec is None:
        return "That action isn't available."
    kind = spec["kind"]
    args = args or {}
    db = SessionLocal()
    try:
        # Telecom clients run on the live enterprise BSS/OSS database (shared with
        # the admin portal), not the generic MockAccount demo backend.
        if (domain or "").lower() == "telecom":
            telecom_reply = _execute_telecom(db, client_slug, name, kind, args)
            if telecom_reply is not None:
                return telecom_reply

        if kind in ("ticket", "callback"):
            row = client_store.create_action_request(
                db, client_slug=client_slug, session_id=session_id,
                action_type=name, kind=kind, payload=args,
            )
            row.reference = f"{_REF_PREFIX[kind]}-{1000 + row.id}"
            db.commit()
            what = "callback" if kind == "callback" else "request"
            return (f"Logged the {what} — reference {row.reference}. "
                    "A human agent will follow up.")

        if kind == "request_status":
            ref = (args.get("reference") or "").strip()
            row = client_store.get_action_by_reference(db, client_slug, ref)
            if row is None:
                return (f"No request was found with reference '{ref}'. Ask the customer to "
                        "double-check the number.")
            when = row.created_at.strftime("%b %d, %Y") if row.created_at else "recently"
            state = "completed" if row.status == "done" else "open (being worked on)"
            subj = (row.payload or {}).get("subject") or (row.payload or {}).get("topic") or row.action_type
            return (f"{row.reference} ({subj}) was logged on {when} and is currently {state}.")

        identifier = (args.get("identifier") or "").strip()
        if not identifier:
            return "I need the account identifier (phone/email/ID) first — please ask the customer for it."

        acct = client_store.get_mock_account(db, client_slug, identifier)
        if acct is None:
            return f"No account was found for '{identifier}'. Double-check the details with the customer."

        if kind == "account_lookup":
            return _format_account(acct)   # read-only, not recorded

        # account_change: apply the mock change, record it, confirm.
        data = dict(acct.data or {})
        if args.get("new_plan"):
            data["plan"] = args["new_plan"]
            change_desc = f"plan changed to {args['new_plan']}"
        elif args.get("change"):
            data.setdefault("recent_changes", [])
            data["recent_changes"] = (data.get("recent_changes") or [])[-4:] + [args["change"]]
            change_desc = args["change"]
        else:
            change_desc = "account updated"
        client_store.upsert_mock_account(db, client_slug, acct.identifier, name=acct.name, data=data)
        row = client_store.create_action_request(
            db, client_slug=client_slug, session_id=session_id,
            action_type=name, kind=kind, payload=args, result=change_desc,
        )
        row.reference = f"{_REF_PREFIX['account_change']}-{1000 + row.id}"
        db.commit()
        return f"Done — {change_desc}, confirmed. Reference {row.reference}."
    except Exception as e:
        logger.warning(f"Action '{name}' failed for {client_slug}: {e}")
        return "Sorry, I couldn't complete that action just now."
    finally:
        db.close()
