"""
api/main.py
============
API FastAPI du moteur bizzi.
Point d'entrée unique pour tous les clients.

Démarrage :
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.cors import CORSMiddleware
import os

from api.routes import domains, agents, pipeline, content, tools, tenant, comments, membres, meetings, articles
from phone import routes as phone_routes
from social import routes as social_routes
from org_hierarchy import routes as org_routes
from org_hierarchy.embed import embed_router as org_embed_router
from audience import routes as audience_routes
from audience import iframe_embed as audience_embed
from observability import UsageLoggerMiddleware
from api.routes import admin_usage
from api.routes import admin as admin_routes
from api.routes import peer as peer_routes
from api.routes import exports as exports_routes

app = FastAPI(
    title       = "Bizzi API",
    description = "Moteur d'agents IA autonomes configurable par domaine",
    version     = "1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.add_middleware(
    UsageLoggerMiddleware,
    db_config=dict(host="localhost", database="bizzi",
                   user="bizzi_admin", password=os.environ.get("DB_PASSWORD", "")),
    enabled=True,
)

# ── Tokens par tenant ─────────────────────────────────────────
TENANT_TOKENS = {
    os.getenv("ONYX_API_TOKEN",   "onyx-dev-token"):   "onyx",
    os.getenv("MOUV_API_TOKEN",   "mouv-dev-token"):   "mouvement",
    os.getenv("GENIUS_API_TOKEN", "genius-dev-token"): "genius-diagnostic",
}

async def get_tenant(authorization: str = Header(...)) -> str:
    token = authorization.replace("Bearer ", "").strip()
    tenant = TENANT_TOKENS.get(token)
    if not tenant:
        raise HTTPException(status_code=401, detail="Token invalide")
    return tenant

# ── Routes ────────────────────────────────────────────────────
app.include_router(domains.router,  prefix="/api/domains",  tags=["Domaines"])
app.include_router(agents.router,   prefix="/api/agents",   tags=["Agents"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["Pipeline"])
app.include_router(content.router,  prefix="/api/content",  tags=["Contenu"])
app.include_router(tools.router,    prefix="/api/tools",    tags=["Outils"])
app.include_router(tenant.router,   prefix="/api/tenant",   tags=["Tenant"])
app.include_router(comments.router, prefix="/api",          tags=["Commentaires"])
app.include_router(membres.router, prefix="/api/membres", tags=["Membres"])
app.include_router(meetings.router, prefix="/api/meetings", tags=["Réunions"])
app.include_router(articles.router, prefix="/api/articles", tags=["Articles"])
app.include_router(phone_routes.router, prefix="/api/phone", tags=["Phone"])
app.include_router(social_routes.router, prefix="/api/social", tags=["Social"])
app.include_router(org_routes.router,    prefix="/api/org",   tags=["OrgHierarchy"])
app.include_router(org_embed_router,     prefix="/embed/org", tags=["OrgEmbed"])
app.include_router(audience_routes.router, prefix="/api/audience", tags=["Audience"])
app.include_router(audience_embed.router,  prefix="/embed",        tags=["Audience-Embed"])
app.include_router(admin_usage.router, prefix="/api/admin", tags=["Admin"])
app.include_router(admin_routes.router, prefix="/api/admin", tags=["Admin"])
app.include_router(peer_routes.router, prefix="/api/peer", tags=["Peer"])
app.include_router(exports_routes.router, prefix="/api/admin/exports", tags=["Exports"])

@app.get("/")
async def root():
    return {"service": "Bizzi API", "version": "1.0.0", "status": "ok", "docs": "/docs"}

@app.get("/api/status")
async def status(tenant_id: str = Depends(get_tenant)):
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get("http://localhost:11434/api/tags")
            ollama = "ok" if r.status_code == 200 else "error"
    except:
        ollama = "offline"
    return {"tenant": tenant_id, "status": "ok", "ollama": ollama, "version": "1.0.0"}
