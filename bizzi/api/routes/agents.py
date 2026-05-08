import os
"""api/routes/agents.py
========================
Endpoints pour l'équipe de chaque tenant.
Source de vérité : table `agents` (PostgreSQL), filtrée par tenant via Bearer token.

Routes :
    GET  /api/agents/list           — équipe complète du tenant
    GET  /api/agents/{slug}         — détail + stats compétences (knowledge base)
    PUT  /api/agents/{slug}/prompt  — édite le prompt système (= la personnalité)
    PUT  /api/agents/{slug}/status  — active | paused | offline
    GET  /api/agents/{slug}/stats   — stats de production (count, score moyen)

La création d'un agent se fait via le YAML du tenant (`domains/<slug>.yaml`,
section `personnes`) puis `python3 -m scripts.sync_agents --tenant <slug>`.
"""

import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text

router = APIRouter()
logger = logging.getLogger("api.agents")

_db = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))

VALID_STATUSES = ("active", "paused", "offline")


# ── Auth tenant ────────────────────────────────────────────────

def require_tenant(request: Request) -> tuple[int, str]:
    """Extrait (tenant_id, slug) depuis le Bearer token. 401 si invalide."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token requis")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token vide")
    with _db.connect() as conn:
        row = conn.execute(
            text("SELECT id, slug FROM tenants WHERE token_hash = :t"),
            {"t": token},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Token invalide")
    return row[0], row[1]


# ── Modèles ────────────────────────────────────────────────────

class PromptUpdate(BaseModel):
    prompt: str


class StatusUpdate(BaseModel):
    status: str


def _row_to_agent(row) -> dict:
    return {
        "slug":          row[0],
        "name":          row[1],
        "role":          row[2],
        "agent_id":      row[3],
        "specialty":     row[4] or "",
        "personality":   row[5] or "",
        "system_prompt": row[6] or "",
        "color":         row[7] or "#374151",
        "status":        row[8],
        "created_at":    row[9].isoformat() if row[9] else None,
        "updated_at":    row[10].isoformat() if row[10] else None,
    }


# ── Routes ─────────────────────────────────────────────────────

@router.get("/list")
async def list_agents(request: Request):
    """Équipe complète du tenant, triée par hiérarchie (direction → production)."""
    tenant_id, tenant_slug = require_tenant(request)
    with _db.connect() as conn:
        rows = conn.execute(text("""
            SELECT slug, name, role, agent_id, specialty, personality,
                   system_prompt, color, status, created_at, updated_at
            FROM agents
            WHERE tenant_id = :tid
            ORDER BY
                CASE role
                    WHEN 'direction'     THEN 1
                    WHEN 'validation'    THEN 2
                    WHEN 'distribution'  THEN 3
                    WHEN 'verification'  THEN 4
                    WHEN 'production'    THEN 5
                    ELSE 9
                END,
                name
        """), {"tid": tenant_id}).fetchall()
    return {
        "tenant": tenant_slug,
        "count":  len(rows),
        "agents": [_row_to_agent(r) for r in rows],
    }


@router.get("/{slug}")
async def get_agent(slug: str, request: Request):
    """Détail d'un agent + stats de sa knowledge base."""
    tenant_id, _ = require_tenant(request)
    with _db.connect() as conn:
        row = conn.execute(text("""
            SELECT slug, name, role, agent_id, specialty, personality,
                   system_prompt, color, status, created_at, updated_at
            FROM agents
            WHERE tenant_id = :tid AND slug = :s
        """), {"tid": tenant_id, "s": slug}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")

    agent = _row_to_agent(row)

    try:
        from tools.knowledge.knowledge_engine import KnowledgeEngine
        agent["knowledge"] = KnowledgeEngine(agent_slug=slug).stats()
    except Exception as e:
        logger.warning(f"[AGENTS] Knowledge stats indisponibles pour {slug}: {e}")
        agent["knowledge"] = None

    return agent


@router.put("/{slug}/prompt")
async def update_prompt(slug: str, data: PromptUpdate, request: Request):
    """Édite le prompt système (la personnalité IA) de l'agent."""
    tenant_id, _ = require_tenant(request)
    with _db.begin() as conn:
        row = conn.execute(text("""
            UPDATE agents
            SET system_prompt = :p, updated_at = now()
            WHERE tenant_id = :tid AND slug = :s
            RETURNING slug
        """), {"p": data.prompt, "tid": tenant_id, "s": slug}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")
    logger.info(f"[AGENTS] Prompt mis à jour : tenant={tenant_id} agent={slug} ({len(data.prompt)} chars)")
    return {"status": "updated", "slug": slug, "prompt_length": len(data.prompt)}


@router.put("/{slug}/status")
async def update_status(slug: str, data: StatusUpdate, request: Request):
    """Change le statut d'un agent (active | paused | offline)."""
    tenant_id, _ = require_tenant(request)
    if data.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Statut invalide. Valeurs : {', '.join(VALID_STATUSES)}",
        )
    with _db.begin() as conn:
        row = conn.execute(text("""
            UPDATE agents
            SET status = :st, updated_at = now()
            WHERE tenant_id = :tid AND slug = :s
            RETURNING slug
        """), {"st": data.status, "tid": tenant_id, "s": slug}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")
    logger.info(f"[AGENTS] Statut : tenant={tenant_id} agent={slug} → {data.status}")
    return {"status": "updated", "slug": slug, "new_status": data.status}


@router.get("/{slug}/stats")
async def get_stats(slug: str, request: Request):
    """Stats de production d'un agent depuis la table productions."""
    tenant_id, _ = require_tenant(request)
    with _db.connect() as conn:
        ag = conn.execute(
            text("SELECT name, role, status FROM agents WHERE tenant_id = :tid AND slug = :s"),
            {"tid": tenant_id, "s": slug},
        ).fetchone()
        if not ag:
            raise HTTPException(status_code=404, detail=f"Agent '{slug}' introuvable")

        content_count = 0
        avg_score = 0
        last_at = None
        for col in ("journalist_slug", "agent_slug", "author_slug"):
            try:
                stats = conn.execute(text(f"""
                    SELECT COUNT(*) AS total,
                           COALESCE(AVG(quality_score), 0)::int AS avg_score,
                           MAX(created_at) AS last_at
                    FROM productions
                    WHERE tenant_id = :tid AND {col} = :s
                """), {"tid": tenant_id, "s": slug}).fetchone()
                content_count = stats[0] or 0
                avg_score = stats[1] or 0
                last_at = stats[2].isoformat() if stats[2] else None
                break
            except Exception:
                continue

    return {
        "slug":               slug,
        "name":               ag[0],
        "role":               ag[1],
        "status":             ag[2],
        "content_count":      content_count,
        "avg_score":          avg_score,
        "last_production_at": last_at,
    }


@router.post("/create")
async def create_agent_disabled():
    raise HTTPException(
        status_code=405,
        detail=(
            "Création d'agent désactivée via l'API. "
            "Édite /opt/bizzi/bizzi/domains/<tenant>.yaml (section 'personnes'), "
            "puis lance 'python3 -m scripts.sync_agents --tenant <slug>'."
        ),
    )


# ────────────────────────────────────────────────────────────────
# ENDPOINTS PUBLICS — sans auth, pour les pages publiques /equipe et /auteur
# Compatibles Google News (schema.org Person + NewsArticle.author).
# ────────────────────────────────────────────────────────────────

@router.get("/public/list")
async def public_list_agents(tenant: str = "onyx"):
    """Liste publique des journalistes actifs d'un tenant. Pas de token requis."""
    with _db.connect() as conn:
        rows = conn.execute(text("""
            SELECT a.slug, a.name, a.role, a.specialty, a.photo_url,
                   a.bio_public, a.color, a.personality
            FROM agents a JOIN tenants t ON t.id = a.tenant_id
            WHERE t.slug = :ts AND a.role = 'production' AND a.status = 'active'
              AND a.photo_url IS NOT NULL
            ORDER BY a.name
        """), {"ts": tenant}).fetchall()
    return {
        "tenant": tenant,
        "count": len(rows),
        "agents": [
            {
                "slug": r[0], "name": r[1], "role": r[2],
                "specialty": r[3] or "", "photo_url": r[4] or "",
                "bio_public": r[5] or "", "color": r[6] or "#374151",
                "personality": r[7] or "",
            }
            for r in rows
        ],
    }


@router.get("/public/{slug}")
async def public_agent(slug: str, tenant: str = "onyx"):
    """Détail public d'un journaliste + ses 12 derniers articles publiés."""
    with _db.connect() as conn:
        row = conn.execute(text("""
            SELECT a.id, a.slug, a.name, a.role, a.specialty, a.photo_url,
                   a.bio_public, a.color, a.personality
            FROM agents a JOIN tenants t ON t.id = a.tenant_id
            WHERE t.slug = :ts AND a.slug = :s
              AND a.photo_url IS NOT NULL
        """), {"ts": tenant, "s": slug}).fetchone()
        if not row:
            raise HTTPException(404, f"Auteur introuvable")
        agent_db_id = row[0]
        articles = conn.execute(text("""
            SELECT p.id, p.title, p.slug, p.image_url, p.published_at,
                   c.name AS category_name, p.word_count
            FROM productions p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.agent_id = :aid AND p.status = 'published'
            ORDER BY p.published_at DESC NULLS LAST
            LIMIT 12
        """), {"aid": agent_db_id}).fetchall()
    return {
        "slug": row[1], "name": row[2], "role": row[3],
        "specialty": row[4] or "", "photo_url": row[5] or "",
        "bio_public": row[6] or "", "color": row[7] or "#374151",
        "personality": row[8] or "",
        "articles": [
            {
                "id": a[0], "title": a[1], "slug": a[2],
                "image_url": a[3] or "",
                "published_at": a[4].isoformat() if a[4] else None,
                "category": a[5] or "Une", "word_count": a[6] or 0,
            }
            for a in articles
        ],
    }
