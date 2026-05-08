"""api/routes/peer.py — bus inter-agents (Phase 3 métacognition).

Toutes les routes scoped par tenant (sauf /agents/messages/{id}/answer
qui prend l'id PK directement et la fonction underlying lit déjà le tenant
sur la row).

Isolation : aucune lecture/écriture cross-tenant possible.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tools.peer.peer_bus import ask_peer, get_inbox, answer_peer, get_thread

router = APIRouter()


class AskBody(BaseModel):
    from_agent: str = Field(..., min_length=1, max_length=80)
    to_agent: str = Field(..., min_length=1, max_length=80)
    question: str = Field(..., min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class AnswerBody(BaseModel):
    answer: str = Field(..., min_length=1)


@router.post("/{tenant}/agents/{agent}/ask_peer")
async def post_ask(tenant: str, agent: str, body: AskBody):
    """Agent {agent} (du tenant {tenant}) pose une question à un peer."""
    if body.from_agent != agent:
        raise HTTPException(
            status_code=400,
            detail=f"from_agent ({body.from_agent}) must match path agent ({agent})",
        )
    result = ask_peer(tenant, agent, body.to_agent, body.question, body.context)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/{tenant}/agents/{agent}/inbox")
async def get_inb(tenant: str, agent: str, status: str = "pending", limit: int = 20):
    """Inbox de {agent} pour {tenant} (statut filtrable, default pending)."""
    if status not in ("pending", "answered", "expired"):
        raise HTTPException(status_code=400, detail="status must be pending|answered|expired")
    msgs = get_inbox(tenant, agent, status, limit)
    return {"tenant": tenant, "agent": agent, "status": status, "count": len(msgs), "messages": msgs}


@router.post("/agents/messages/{message_id}/answer")
async def post_answer(message_id: int, body: AnswerBody):
    """Marque un message comme answered. Refuse si déjà answered."""
    result = answer_peer(message_id, body.answer)
    if result.get("error") == "message_not_found_or_already_answered":
        raise HTTPException(status_code=404, detail=result["error"])
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/{tenant}/messages/{message_id}")
async def get_msg(tenant: str, message_id: int):
    """Récupère un thread complet, scoped par tenant (404 si autre tenant)."""
    r = get_thread(tenant, message_id)
    if not r:
        raise HTTPException(status_code=404, detail="message_not_found")
    return r
