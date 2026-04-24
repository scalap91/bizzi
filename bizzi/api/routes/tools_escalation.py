"""api/routes/tools_escalation.py"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from tools.escalation.escalation_engine import EscalationEngine, SIGNALS_DB, ISSUES_DB, PROJECTS_DB
from config.domain_loader import DomainLoader

router = APIRouter()

class SignalCreate(BaseModel):
    tenant:   str
    domain:   str = "politics"
    content:  str
    location: str
    author:   str
    contact:  Optional[str] = ""

class ProjectValidate(BaseModel):
    project_id: str; validated_by: str; tenant: str; domain: str = "politics"

def _engine(tenant: str, domain: str):
    try:    cfg = DomainLoader.load_domain(domain)
    except: cfg = DomainLoader.load_domain("politics")
    return EscalationEngine(cfg)

@router.post("/signal")
async def create_signal(data: SignalCreate):
    return await _engine(data.tenant, data.domain).process_signal(
        data.content, data.location, data.author, data.contact)

@router.get("/signals")
async def list_signals(tenant: str, domain: str = "politics",
    scope: str = None, scope_value: str = None,
    category: str = None, status: str = None):
    return {"signals": _engine(tenant, domain).get_signals(scope, scope_value, category, status)}

@router.get("/issues")
async def list_issues(tenant: str, domain: str = "politics",
    level: int = None, scope: str = None, scope_value: str = None):
    return {"issues": _engine(tenant, domain).get_issues(level, scope, scope_value)}

@router.get("/projects")
async def list_projects(tenant: str, domain: str = "politics", status: str = None):
    projs = [p for p in PROJECTS_DB if p['tenant'] == tenant]
    if status: projs = [p for p in projs if p['status'] == status]
    return {"projects": sorted(projs, key=lambda x: x['created_at'], reverse=True)}

@router.post("/projects/validate")
async def validate_project(data: ProjectValidate):
    proj = _engine(data.tenant, data.domain).validate_project(data.project_id, data.validated_by)
    if not proj: raise HTTPException(404, "Projet introuvable")
    return proj

@router.get("/stats")
async def get_stats(tenant: str, domain: str = "politics",
    scope: str = None, scope_value: str = None):
    return _engine(tenant, domain).get_stats(scope, scope_value)
