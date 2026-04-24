"""api/routes/tools_rgpd.py"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from tools.rgpd.rgpd_agent import RGPDAgent, REQUESTS_DB
from config.domain_loader import DomainLoader

router = APIRouter()

class RGPDRequest(BaseModel):
    name:    str
    email:   str
    content: str
    domain:  str = "media"

@router.post("/request")
async def rgpd_request(data: RGPDRequest):
    try: cfg = DomainLoader.load_domain(data.domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = RGPDAgent(cfg)
    return await agent.process(data.name, data.email, data.content)

@router.get("/requests")
async def list_requests(status: Optional[str] = None, domain: str = "media"):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = RGPDAgent(cfg)
    return {"requests": agent.list_requests(status), "overdue": len(agent.overdue_requests())}

@router.get("/requests/{req_id}")
async def get_request(req_id: str):
    r = REQUESTS_DB.get(req_id)
    if not r: raise HTTPException(404, "Demande RGPD introuvable")
    return r

@router.post("/requests/{req_id}/complete")
async def complete_request(req_id: str, data_summary: str = "", domain: str = "media"):
    try: cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = RGPDAgent(cfg)
    result = agent.complete_request(req_id, data_summary)
    if not result: raise HTTPException(404, "Demande RGPD introuvable")
    return result

@router.get("/rights")
async def list_rights():
    """Liste tous les droits RGPD reconnus."""
    from tools.rgpd.rgpd_agent import RIGHTS
    return {"rights": RIGHTS}
