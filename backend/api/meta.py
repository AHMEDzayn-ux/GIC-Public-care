"""
Meta Router — domain-template listing. (Auth moved to /api/auth/* in auth_routes.)
"""

from fastapi import APIRouter

from api.models import (
    DomainInfo,
    DomainListResponse,
)
from domain_templates import list_templates

router = APIRouter(tags=["meta"])


@router.get("/api/domains", response_model=DomainListResponse)
async def get_domains():
    return DomainListResponse(
        domains=[
            DomainInfo(
                key=t.key,
                display_name=t.display_name,
                persona=t.persona,
                bot_name=t.bot_name,
                greeting=t.greeting,
            )
            for t in list_templates()
        ]
    )
