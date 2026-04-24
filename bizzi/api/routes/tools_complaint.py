"""api/routes/tools_complaint.py"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from tools.complaint.complaint_agent import ComplaintAgent, TICKETS_DB
from config.domain_loader import DomainLoader

router = APIRouter()

class ComplaintIn(BaseModel):
    name:    str
    email:   str
    content: str
    channel: str = "web"
    domain:  str = "media"

class TicketUpdate(BaseModel):
    status: str
    note:   Optional[str] = ""

@router.post("/submit")
async def submit_complaint(data: ComplaintIn):
    try: cfg = DomainLoader.load_domain(data.domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = ComplaintAgent(cfg)
    return await agent.process(data.name, data.email, data.content, data.channel)

@router.get("/tickets")
async def list_tickets(status: Optional[str] = None, priority: Optional[str] = None, domain: str = "media"):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = ComplaintAgent(cfg)
    return {"tickets": agent.list_tickets(status, priority)}

@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    ticket = TICKETS_DB.get(ticket_id)
    if not ticket: raise HTTPException(404, "Ticket introuvable")
    return ticket

@router.put("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, data: TicketUpdate, domain: str = "media"):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent  = ComplaintAgent(cfg)
    ticket = agent.update_ticket(ticket_id, data.status, data.note or "")
    if not ticket: raise HTTPException(404, "Ticket introuvable")
    return ticket
