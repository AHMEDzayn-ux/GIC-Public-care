"""
WhatsApp Bot Webhook Handler (multi-tenant)

A single webhook serves ALL clients. Inbound messages are routed to the right
client by the WhatsApp ``phone_number_id`` in the payload; the matched client's
persona/domain pipeline generates the reply and the client's own access token
sends it back.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
import requests
import logging

from config import get_settings
from database import SessionLocal
from services import client_store
from api.clients import get_pipeline_manager
from integrations.session_manager import session_manager
from integrations.whatsapp_formatter import formatter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["WhatsApp"])
settings = get_settings()


def send_whatsapp_message(phone_number_id: str, access_token: str, to: str, message: str) -> bool:
    """Send a text message from a specific client's WhatsApp number."""
    try:
        url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"WhatsApp message sent to {to}")
            return True
        logger.error(f"Failed to send WhatsApp message: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        return False


def mark_message_as_read(phone_number_id: str, access_token: str, message_id: str) -> None:
    try:
        url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        requests.post(url, headers=headers, json=payload, timeout=5)
    except Exception as e:
        logger.warning(f"Failed to mark message as read: {e}")


async def process_whatsapp_message(
    phone_number_id: str,
    sender: str,
    message_text: str,
    message_id: str,
) -> None:
    """Route an inbound message to the correct client and reply."""
    # 1. Resolve the client from the inbound phone_number_id.
    db = SessionLocal()
    try:
        client = client_store.find_by_wa_phone(db, phone_number_id)
    finally:
        db.close()

    if client is None:
        logger.warning(f"No WhatsApp-enabled client for phone_number_id={phone_number_id}; ignoring")
        return

    access_token = client.wa_access_token
    if not access_token:
        logger.error(f"Client '{client.slug}' has WhatsApp enabled but no access token")
        return

    slug = client.slug
    bot_name = client.bot_name or "Support"
    session_key = f"{slug}:{sender}"

    try:
        mark_message_as_read(phone_number_id, access_token, message_id)

        message_lower = message_text.lower().strip()
        if message_lower == "help":
            send_whatsapp_message(phone_number_id, access_token, sender, formatter.format_help_message())
            return
        if message_lower == "clear":
            session_manager.clear_session(session_key)
            send_whatsapp_message(phone_number_id, access_token, sender, formatter.format_clear_confirmation())
            return

        history = session_manager.get_history(session_key, max_messages=10)
        if len(history) == 0:
            send_whatsapp_message(phone_number_id, access_token, sender,
                                  formatter.format_welcome_message(bot_name))

        session_manager.add_message(session_key, "user", message_text)

        # 2. Use the client's own persona/domain pipeline (loaded from DB config).
        pipeline = get_pipeline_manager().get_pipeline(slug)
        if pipeline is None:
            logger.error(f"No pipeline/collection for client '{slug}'")
            send_whatsapp_message(phone_number_id, access_token, sender,
                                  formatter.format_error_message("general"))
            return

        result = pipeline.agent_chat(
            message=message_text,
            conversation_history=history,
            top_k=4,
        )
        response_text = result.get("answer", "I couldn't generate a response.")

        session_manager.add_message(session_key, "assistant", response_text)

        # Log the turn for the learning loop (session = phone number).
        try:
            db2 = SessionLocal()
            try:
                client_store.log_interaction(
                    db2,
                    client_slug=slug,
                    session_id=sender,
                    user_message=message_text,
                    answer=response_text,
                    used_retrieval=result.get("used_retrieval", False),
                    no_kb_match=result.get("no_kb_match", False),
                    emotion=result.get("emotion") or {},
                    escalated=result.get("escalated", False),
                )
            finally:
                db2.close()
        except Exception as e:
            logger.warning(f"Failed to log WhatsApp interaction: {e}")

        formatted = formatter.sanitize_markdown(response_text)
        for chunk in formatter.split_long_message(formatted):
            send_whatsapp_message(phone_number_id, access_token, sender, chunk)

        logger.info(f"[{slug}] handled WhatsApp message from {sender}: {message_text[:50]}...")

    except Exception as e:
        logger.error(f"Error processing WhatsApp message for '{slug}': {e}")
        send_whatsapp_message(phone_number_id, access_token, sender,
                              formatter.format_error_message("general"))


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    """Meta webhook verification (single global verify token for all clients)."""
    logger.info(f"Webhook verification: mode={hub_mode}")
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def handle_webhook(request: Request):
    """Receive inbound messages; route each to its client by phone_number_id."""
    try:
        payload = await request.json()
        if "entry" not in payload:
            return {"status": "ok"}

        for entry in payload["entry"]:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" not in value:
                    continue
                # The recipient business number that received the message.
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                for message in value["messages"]:
                    if message.get("type") != "text":
                        continue
                    sender = message.get("from")
                    message_text = message.get("text", {}).get("body", "")
                    message_id = message.get("id")
                    if not sender or not message_text or not phone_number_id:
                        continue
                    await process_whatsapp_message(
                        phone_number_id=phone_number_id,
                        sender=sender,
                        message_text=message_text,
                        message_id=message_id,
                    )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/whatsapp/status")
async def get_whatsapp_status():
    """Report which clients have WhatsApp enabled."""
    db = SessionLocal()
    try:
        enabled = [c.slug for c in client_store.list_clients(db) if c.wa_enabled]
    finally:
        db.close()
    return {
        "status": "running",
        "active_sessions": session_manager.get_active_sessions_count(),
        "whatsapp_enabled_clients": enabled,
    }
