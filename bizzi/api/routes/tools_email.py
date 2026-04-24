"""api/routes/tools_email.py"""
from fastapi import APIRouter
from pydantic import BaseModel
from tools.email.email_agent import EmailAgent
from config.domain_loader import DomainLoader

router = APIRouter()

class EmailIn(BaseModel):
    sender:  str
    subject: str
    body:    str
    domain:  str = "media"

@router.post("/process")
async def process_email(data: EmailIn):
    try: cfg = DomainLoader.load_domain(data.domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = EmailAgent(cfg)
    return await agent.process(data.sender, data.subject, data.body)
