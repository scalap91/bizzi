"""api/routes/agents.py"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

# Storage simple en mémoire (en prod : PostgreSQL)
AGENTS_DB: dict = {}

class AgentCreate(BaseModel):
    slug:       str
    name:       str
    agent_id:   str
    domain:     str
    specialty:  Optional[str] = ""
    color:      Optional[str] = "#4a5070"
    custom_prompt: Optional[str] = ""

class AgentPromptUpdate(BaseModel):
    prompt: str

@router.get("/list")
async def list_agents(domain: Optional[str] = None):
    agents = list(AGENTS_DB.values())
    if domain:
        agents = [a for a in agents if a.get("domain") == domain]
    return {"agents": agents, "count": len(agents)}

@router.post("/create")
async def create_agent(data: AgentCreate):
    if data.slug in AGENTS_DB:
        raise HTTPException(status_code=409, detail=f"Agent '{data.slug}' existe déjà")
    agent = {**data.dict(), "status": "active", "content_count": 0, "avg_score": 0.0}
    AGENTS_DB[data.slug] = agent
    return {"status": "created", "agent": agent}

@router.get("/{slug}")
async def get_agent(slug: str):
    if slug not in AGENTS_DB:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")
    return AGENTS_DB[slug]

@router.put("/{slug}/prompt")
async def update_prompt(slug: str, data: AgentPromptUpdate):
    if slug not in AGENTS_DB:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")
    AGENTS_DB[slug]["custom_prompt"] = data.prompt
    return {"status": "updated", "slug": slug}

@router.put("/{slug}/status")
async def update_status(slug: str, status: str):
    if slug not in AGENTS_DB:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")
    if status not in ["active", "paused", "offline"]:
        raise HTTPException(status_code=400, detail="Statut invalide")
    AGENTS_DB[slug]["status"] = status
    return {"status": "updated", "slug": slug, "new_status": status}

@router.get("/{slug}/stats")
async def get_stats(slug: str):
    if slug not in AGENTS_DB:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")
    agent = AGENTS_DB[slug]
    return {
        "slug":          slug,
        "name":          agent.get("name"),
        "content_count": agent.get("content_count", 0),
        "avg_score":     agent.get("avg_score", 0.0),
        "status":        agent.get("status", "active"),
    }
