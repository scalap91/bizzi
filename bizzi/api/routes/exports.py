"""api/routes/exports.py — endpoints d'export `chat_logs` (data-resale-ready).

Phase 11 — Bizzi en moteur d'intelligence collective.

Routes exposées sous `/api/admin/exports` :
    GET /insights         → rows anonymisées (filtre industry, période, format).
    GET /sectoral_report  → stats agrégées par industry.

Filtre de sécurité : SEUL `resale_consent = TRUE` est exporté. Aucun champ
brut (`message_user`, `message_agent`) n'est jamais retourné — uniquement les
versions `_anon`.

Wiring (api/main.py) :
    from api.routes import exports as exports_routes
    app.include_router(exports_routes.router, prefix="/api/admin/exports", tags=["Exports"])
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

load_dotenv("/opt/bizzi/bizzi/.env")

logger = logging.getLogger("api.routes.exports")
router = APIRouter()

DB_URL = os.getenv("DATABASE_URL")
DEFAULT_LIMIT = 10000
MAX_LIMIT = 50000


def _conn():
    if not DB_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL missing")
    return psycopg2.connect(DB_URL)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        raise HTTPException(status_code=400, detail=f"datetime invalide: {s}")


# ── GET /insights ────────────────────────────────────────────────────────

@router.get("/insights")
def insights(
    industry: str | None = Query(None, description="Filtre tenant_industry (ex: travel)"),
    date_from: str | None = Query(None, alias="from", description="ISO datetime"),
    date_to: str | None = Query(None, alias="to", description="ISO datetime"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    fmt: str = Query("json", alias="format", pattern="^(json|csv)$"),
) -> Any:
    """Exporte les chat_logs anonymisés exportables (resale_consent=TRUE).

    Champs retournés : tenant_industry, tenant_size_bucket, tenant_region,
    intent, topic_tags, pii_detected, message_user_anon, message_agent_anon,
    outcome, outcome_value, confidence, created_at.
    """
    dt_from = _parse_dt(date_from)
    dt_to = _parse_dt(date_to)

    where = ["resale_consent = TRUE"]
    params: list[Any] = []
    if industry:
        where.append("tenant_industry = %s")
        params.append(industry)
    if dt_from:
        where.append("created_at >= %s")
        params.append(dt_from)
    if dt_to:
        where.append("created_at <= %s")
        params.append(dt_to)

    sql = f"""
        SELECT tenant_industry, tenant_size_bucket, tenant_region,
               intent, topic_tags, pii_detected,
               message_user_anon, message_agent_anon,
               outcome, outcome_value, confidence, created_at
        FROM chat_logs
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as e:
        logger.exception("insights query failed")
        raise HTTPException(status_code=500, detail=f"db_error: {type(e).__name__}")

    # Sérialisation : datetimes → ISO ; topic_tags reste une liste JSON.
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        # outcome_value est Decimal → float
        if d.get("outcome_value") is not None:
            d["outcome_value"] = float(d["outcome_value"])
        out.append(d)

    if fmt == "csv":
        if not out:
            return PlainTextResponse("", media_type="text/csv")
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(out[0].keys()))
        writer.writeheader()
        for d in out:
            d_csv = {
                k: (",".join(v) if isinstance(v, list) else v) for k, v in d.items()
            }
            writer.writerow(d_csv)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")

    return JSONResponse({"count": len(out), "rows": out})


# ── GET /sectoral_report ─────────────────────────────────────────────────

_PERIOD_SQL = {
    "current_quarter": "created_at >= date_trunc('quarter', NOW())",
    "current_month": "created_at >= date_trunc('month', NOW())",
    "last_30d": "created_at >= NOW() - INTERVAL '30 days'",
    "last_7d": "created_at >= NOW() - INTERVAL '7 days'",
    "all_time": "TRUE",
}


@router.get("/sectoral_report")
def sectoral_report(
    industry: str | None = Query(None, description="Filtre tenant_industry"),
    period: str = Query("current_quarter", pattern="^(current_quarter|current_month|last_30d|last_7d|all_time)$"),
) -> Any:
    """Stats agrégées par industry sur une période donnée.

    Retourne :
        - total_logs
        - intents : count par intent
        - top_topic_tags : top 20 tags par fréquence
        - outcomes : count par outcome (NULL inclus comme "unknown")
        - booking_rate : ratio outcome IN ('booking_complete') / total
    """
    period_clause = _PERIOD_SQL[period]

    where = ["resale_consent = TRUE", period_clause]
    params: list[Any] = []
    if industry:
        where.append("tenant_industry = %s")
        params.append(industry)
    where_sql = " AND ".join(where)

    try:
        with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Total
            cur.execute(f"SELECT COUNT(*)::int AS n FROM chat_logs WHERE {where_sql}", params)
            total = cur.fetchone()["n"]

            # Intents
            cur.execute(
                f"""
                SELECT COALESCE(intent, 'unknown') AS intent, COUNT(*)::int AS n
                FROM chat_logs WHERE {where_sql}
                GROUP BY intent ORDER BY n DESC
                """,
                params,
            )
            intents = [dict(r) for r in cur.fetchall()]

            # Top topic_tags (jsonb_array_elements_text)
            cur.execute(
                f"""
                SELECT tag, COUNT(*)::int AS n
                FROM chat_logs, LATERAL jsonb_array_elements_text(topic_tags) AS tag
                WHERE {where_sql}
                GROUP BY tag
                ORDER BY n DESC
                LIMIT 20
                """,
                params,
            )
            top_tags = [dict(r) for r in cur.fetchall()]

            # Outcomes
            cur.execute(
                f"""
                SELECT COALESCE(outcome, 'unknown') AS outcome, COUNT(*)::int AS n
                FROM chat_logs WHERE {where_sql}
                GROUP BY outcome ORDER BY n DESC
                """,
                params,
            )
            outcomes = [dict(r) for r in cur.fetchall()]

            # Booking rate
            cur.execute(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE outcome = 'booking_complete')::float
                  / NULLIF(COUNT(*), 0)::float AS rate
                FROM chat_logs WHERE {where_sql}
                """,
                params,
            )
            br_row = cur.fetchone()
            booking_rate = float(br_row["rate"]) if br_row and br_row["rate"] is not None else 0.0
    except Exception as e:
        logger.exception("sectoral_report failed")
        raise HTTPException(status_code=500, detail=f"db_error: {type(e).__name__}")

    return {
        "industry": industry,
        "period": period,
        "total_logs": total,
        "intents": intents,
        "top_topic_tags": top_tags,
        "outcomes": outcomes,
        "booking_rate": round(booking_rate, 4),
    }
