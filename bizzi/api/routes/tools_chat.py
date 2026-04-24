"""api/routes/tools_chat.py"""
from fastapi import APIRouter
from pydantic import BaseModel
from tools.chat.chat_agent import get_session
from config.domain_loader import DomainLoader

router = APIRouter()

class ChatMessage(BaseModel):
    session_id: str
    message:    str
    tenant:     str = "default"
    domain:     str = "media"

@router.post("/message")
async def chat_message(data: ChatMessage):
    try:
        domain = DomainLoader.load_domain(data.domain)
    except:
        domain = DomainLoader.load_domain("media")
    session = get_session(data.session_id, domain)
    return await session.reply(data.message)

@router.get("/history/{session_id}")
async def chat_history(session_id: str, domain: str = "media"):
    try:
        domain_cfg = DomainLoader.load_domain(domain)
    except:
        domain_cfg = DomainLoader.load_domain("media")
    session = get_session(session_id, domain_cfg)
    return {"session_id": session_id, "history": session.history, "count": len(session.history)}
