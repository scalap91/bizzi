"""api/routes/mobile_memory.py — Mémoire perpétuelle Bizzi Mobile (Phase 32 V2).

Endpoints (auth JWT obligatoire — réutilise get_current_user de auth.py) :

- GET    /api/mobile/memory                 : liste mémoire (filtres agent_slug, fact_type, limit)
- POST   /api/mobile/memory                 : ajoute un fait
- GET    /api/mobile/memory/{memory_id}     : détail fait
- DELETE /api/mobile/memory/{memory_id}     : right-to-be-forgotten
- POST   /api/mobile/memory/search          : recherche full-text simple (LIKE)

Isolation user STRICTE : chaque query filtre par user_id du JWT.
"""
from __future__ import annotations

from typing import Optional, List
from datetime import datetime

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from api.routes.auth import get_current_user, _db

router = APIRouter()


# ───────────────────────── Models ─────────────────────────


FACT_TYPES = ("preference", "fact", "event", "relation", "goal", "habit", "skill")


class MemoryAddBody(BaseModel):
    fact_type: str = Field(..., min_length=1, max_length=50)
    content: str = Field(..., min_length=1, max_length=4000)
    agent_slug: Optional[str] = Field(default=None, max_length=80)
    source: Optional[str] = Field(default=None, max_length=120)
    confidence: int = Field(default=80, ge=0, le=100)
    metadata: Optional[dict] = None
    expires_at: Optional[datetime] = None


class MemorySearchBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    agent_slug: Optional[str] = None
    fact_type: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=200)


def _row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "agent_slug": r["agent_slug"],
        "fact_type": r["fact_type"],
        "content": r["content"],
        "source": r["source"],
        "confidence": r["confidence"],
        "metadata": r.get("metadata") or {},
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None,
    }


# ───────────────────────── Routes ─────────────────────────


@router.get("/")
async def list_memory(
    user=Depends(get_current_user),
    agent_slug: Optional[str] = Query(default=None, max_length=80),
    fact_type: Optional[str] = Query(default=None, max_length=50),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Liste la mémoire du user, optionnellement scopée par agent ou fact_type.

    Filtres :
    - agent_slug (None = mémoire globale + tous agents ; "global" = uniquement globale)
    - fact_type (preference, fact, event, relation, goal, habit, skill)
    """
    sql = (
        "SELECT id, user_id, agent_slug, fact_type, content, source, confidence, "
        "metadata, created_at, expires_at "
        "FROM mobile_user_memory WHERE user_id=%s "
        "AND (expires_at IS NULL OR expires_at > NOW())"
    )
    params: list = [user["id"]]

    if agent_slug == "global":
        sql += " AND agent_slug IS NULL"
    elif agent_slug:
        sql += " AND (agent_slug = %s OR agent_slug IS NULL)"
        params.append(agent_slug)

    if fact_type:
        sql += " AND fact_type = %s"
        params.append(fact_type)

    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return {
        "user_id": user["id"],
        "count": len(rows),
        "filter": {"agent_slug": agent_slug, "fact_type": fact_type},
        "memory": [_row_to_dict(r) for r in rows],
    }


@router.post("/")
async def add_memory(body: MemoryAddBody, user=Depends(get_current_user)):
    """Ajoute un fait à la mémoire perpétuelle du user.

    fact_type recommandés : preference, fact, event, relation, goal, habit, skill.
    agent_slug = None ⇒ mémoire globale (tous agents la voient).
    """
    if body.fact_type not in FACT_TYPES:
        # On accepte mais on warn — flexible
        pass

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO mobile_user_memory
              (user_id, agent_slug, fact_type, content, source, confidence, metadata, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id, user_id, agent_slug, fact_type, content, source,
                      confidence, metadata, created_at, expires_at
            """,
            (
                user["id"],
                body.agent_slug,
                body.fact_type,
                body.content,
                body.source,
                body.confidence,
                psycopg2.extras.Json(body.metadata or {}),
                body.expires_at,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    return _row_to_dict(row)


@router.get("/{memory_id}")
async def get_memory(memory_id: int, user=Depends(get_current_user)):
    """Détail d'un fait. 404 si pas dans la mémoire du user (isolation)."""
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, user_id, agent_slug, fact_type, content, source, "
            "confidence, metadata, created_at, expires_at "
            "FROM mobile_user_memory WHERE id=%s AND user_id=%s",
            (memory_id, user["id"]),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="memory_not_found")
    return _row_to_dict(row)


@router.delete("/{memory_id}")
async def forget(memory_id: int, user=Depends(get_current_user)):
    """Right-to-be-forgotten : supprime un fait (scoped user)."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mobile_user_memory WHERE id=%s AND user_id=%s",
            (memory_id, user["id"]),
        )
        deleted = cur.rowcount
        conn.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="memory_not_found")
    return {"deleted": deleted, "memory_id": memory_id}


@router.post("/search")
async def search_memory(body: MemorySearchBody, user=Depends(get_current_user)):
    """Recherche full-text simple (ILIKE) dans le content. Cible : retrieval RAG agent."""
    sql = (
        "SELECT id, user_id, agent_slug, fact_type, content, source, confidence, "
        "metadata, created_at, expires_at "
        "FROM mobile_user_memory "
        "WHERE user_id=%s "
        "AND (expires_at IS NULL OR expires_at > NOW()) "
        "AND content ILIKE %s "
    )
    params: list = [user["id"], f"%{body.query}%"]

    if body.agent_slug:
        sql += "AND (agent_slug = %s OR agent_slug IS NULL) "
        params.append(body.agent_slug)
    if body.fact_type:
        sql += "AND fact_type = %s "
        params.append(body.fact_type)

    sql += "ORDER BY created_at DESC LIMIT %s"
    params.append(body.limit)

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return {
        "query": body.query,
        "count": len(rows),
        "results": [_row_to_dict(r) for r in rows],
    }
