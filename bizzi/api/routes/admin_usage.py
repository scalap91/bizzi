"""bizzi.api.routes.admin_usage — Endpoints admin observability.

Wiring (à ajouter dans api/main.py) :
    from api.routes import admin_usage
    app.include_router(admin_usage.router, prefix="/api/admin", tags=["Admin"])

Sécurité Phase 0 : pas d'auth — à protéger via reverse-proxy ou Depends(...)
en Phase 1. Endpoints retournent uniquement des aggregats (pas de PII).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from fastapi import APIRouter, Query

router = APIRouter()

# Même DB que le reste du projet (cf. data/_db.py, audience/_db.py).
_DB_CONFIG = dict(
    host="localhost", database="bizzi",
    user="bizzi_admin", password=os.environ.get("DB_PASSWORD", ""),
)


def _conn():
    return psycopg2.connect(cursor_factory=RealDictCursor, **_DB_CONFIG)


def _serialize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        d = dict(r)
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        out.append(d)
    return out


@router.get("/usage_stats")
def usage_stats(
    limit_stats: int = Query(100, ge=1, le=1000),
    limit_dead:  int = Query(50,  ge=1, le=500),
):
    """Retourne stats 30j + routes mortes pour l'audit doublons Pascal."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT * FROM module_usage_stats_30d LIMIT %s",
            (int(limit_stats),),
        )
        stats = _serialize([dict(r) for r in cur.fetchall()])

        cur.execute(
            "SELECT * FROM dead_routes LIMIT %s",
            (int(limit_dead),),
        )
        dead = _serialize([dict(r) for r in cur.fetchall()])

        cur.execute("SELECT count(*) AS n FROM module_usage_log")
        total = int(cur.fetchone()["n"])

        cur.execute(
            "SELECT module, count(*) AS n "
            "FROM module_usage_log "
            "WHERE called_at > now() - interval '24 hours' "
            "GROUP BY module ORDER BY n DESC",
        )
        modules_24h = _serialize([dict(r) for r in cur.fetchall()])

    return {
        "total_log_rows":     total,
        "modules_last_24h":   modules_24h,
        "stats_30d":          stats,
        "dead_routes":        dead,
    }


@router.get("/usage_recent")
def usage_recent(
    module:    Optional[str] = None,
    limit:     int = Query(50, ge=1, le=500),
    tenant_id: Optional[int] = None,
):
    """Derniers calls bruts — utile pour debug en live."""
    where = []
    params: list[Any] = []
    if module:
        where.append("module = %s"); params.append(module)
    if tenant_id is not None:
        where.append("tenant_id = %s"); params.append(int(tenant_id))
    sql = (
        "SELECT id, module, endpoint, method, status_code, tenant_id, "
        "duration_ms, called_at FROM module_usage_log"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY called_at DESC LIMIT %s"
    )
    params.append(int(limit))
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return {"items": _serialize([dict(r) for r in cur.fetchall()])}
