"""api/routes/tools_chat.py — endpoints chat multi-tenant Claude API."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tools.chat.chat_agent import get_session
from tenant_db import list_tenants, TenantNotFound

router = APIRouter()

DEFAULT_TENANT = "airbizness"


class ChatMessage(BaseModel):
    session_id: str
    message:    str
    tenant:     str | None = None


def _resolve_tenant(slug: str | None) -> str:
    t = (slug or DEFAULT_TENANT).strip()
    available = list_tenants()
    if t not in available:
        raise HTTPException(
            status_code=400,
            detail=f"unknown tenant '{t}', available: {available}",
        )
    return t


@router.post("/message")
async def chat_message(data: ChatMessage):
    tenant_slug = _resolve_tenant(data.tenant)
    try:
        session = get_session(data.session_id, tenant_slug)
    except TenantNotFound as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"session_init_failed: {type(e).__name__}: {e}",
        )
    return await session.reply(data.message)


@router.get("/history/{session_id}")
async def chat_history(session_id: str, tenant: str | None = None):
    tenant_slug = _resolve_tenant(tenant)
    try:
        session = get_session(session_id, tenant_slug)
    except TenantNotFound as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "session_id": session_id,
        "tenant": tenant_slug,
        "history": session.history,
        "count": len(session.history),
    }
