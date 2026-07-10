"""
Domain Templates — the "divertable across domains" core of the framework.

Each template is a reusable preset for a vertical (telecom, university, ...).
Creating a client = picking a domain; its persona/branding/normalization
context auto-fill from the template and remain overridable per client.

Adding a NEW vertical = adding one entry here. No pipeline code changes.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class DomainTemplate:
    key: str
    display_name: str
    # System-role / persona text injected into the LLM system prompt.
    persona: str
    # Domain hint used by query normalization (replaces hard-coded strings).
    normalization_context: str
    # Vertical-appropriate few-shot examples injected into the chat prompt,
    # so telecom examples never bleed into a university assistant.
    few_shot_examples: List[str] = field(default_factory=list)
    bot_name: str = "Assistant"
    greeting: str = "Hi! How can I help you today?"


DOMAIN_TEMPLATES: Dict[str, DomainTemplate] = {
    "generic": DomainTemplate(
        key="generic",
        display_name="Generic Customer Care",
        persona=(
            "a friendly, professional customer support assistant. You answer "
            "questions accurately using the provided knowledge base, stay on "
            "topic, and are warm, concise, and helpful."
        ),
        normalization_context="general customer support documents",
        few_shot_examples=[],
        bot_name="Support Assistant",
        greeting="Hi! 👋 How can I help you today?",
    ),
    "telecom": DomainTemplate(
        key="telecom",
        display_name="Telecommunications",
        persona=(
            "a friendly telecommunications customer support assistant. You help "
            "customers with data/voice packages, prices, activation, FUP limits, "
            "and billing. Be confident and specific about the customer's plan; "
            "never invent policy IDs or numbers that are not in the knowledge base."
        ),
        normalization_context="telecommunication packages, data plans, and billing policies",
        few_shot_examples=[
            "User: hi\n"
            "You: Hi! 👋 How can I help you today — packages, billing, or something else?",
            "User: how do i replace my sim\n"
            "You: To replace your SIM, visit an authorized store or contact support, fill out a replacement form, bring a valid ID, and pay a small replacement fee. Want the exact fee and locations?  (Note: answer ONLY the SIM process — do not list any plan details.)",
            "User: give me a youtube package\n"
            "You: Unlimited Pro includes YouTube (plus Netflix, Spotify, TikTok) on unlimited 5G for $45/month. Want the full details?\n"
            "User: what is the fup limit\n"
            "You: It's 50GB — after that your data is deprioritized (slower at peak times). Need anything else?",
        ],
        bot_name="Customer Support",
        greeting="Hi! 👋 I can help with packages, billing, and activation. What do you need?",
    ),
    "university": DomainTemplate(
        key="university",
        display_name="University / Education",
        persona=(
            "a helpful university admissions and student-services assistant. You "
            "help prospective and current students with course details, enrollment "
            "steps, fees, deadlines, and university procedures. Be clear and "
            "encouraging; only state requirements and dates found in the documents."
        ),
        normalization_context="university courses, enrollment procedures, and admission requirements",
        few_shot_examples=[
            "User: how do i enroll in the CS program\n"
            "You: To enroll in Computer Science you submit the online application, upload your transcripts, and pay the registration fee. Want the full step-by-step?\n"
            "User: what are the fees\n"
            "You: The CS program fee is the amount listed for that intake — I can break down tuition vs. registration if you'd like.",
        ],
        bot_name="Student Assistant",
        greeting="Hi! 🎓 Ask me about courses, enrollment, fees, or university procedures.",
    ),
}

DEFAULT_DOMAIN = "generic"


def get_template(domain: str) -> DomainTemplate:
    """Return the template for ``domain``, falling back to generic."""
    return DOMAIN_TEMPLATES.get((domain or "").lower(), DOMAIN_TEMPLATES[DEFAULT_DOMAIN])


def list_templates() -> List[DomainTemplate]:
    return list(DOMAIN_TEMPLATES.values())
