"""bizzi.audience.storage — Insertion DB + bump trends + publication event.

Universel : aucun champ sectoriel, aucune dépendance YAML ici (la
configuration est appliquée en amont par routes/ingest). Cette couche
ne fait que persister et notifier.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from . import event_bus
from ._db import get_conn, pgvector_available
from .nlp.embedder import vec_to_bytes

logger = logging.getLogger(__name__)


def _vec_to_pg_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def insert_report(
    tenant_id: int,
    *,
    source: str,
    raw_message: str,
    cleaned_message: str,
    analysis: dict[str, Any],
    embedding: Optional[list[float]] = None,
    platform: Optional[str] = None,
    author_name: Optional[str] = None,
    author_external_id: Optional[str] = None,
    city: Optional[str] = None,
    org_unit_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Insère une remontée + bump trends + publish event. Retourne la row insérée."""
    use_vec = pgvector_available()
    cats = list(analysis.get("categories") or [])
    keywords = list(analysis.get("keywords") or [])

    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        if use_vec and embedding is not None:
            cur.execute(
                """INSERT INTO audience_reports
                   (tenant_id, source, platform, author_name, author_external_id,
                    city, org_unit_id, raw_message, cleaned_message, categories, subcategory,
                    emotion, keywords, priority_score, language, embedding, metadata)
                   VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s::vector,%s)
                   RETURNING *""",
                (
                    tenant_id, source, platform, author_name, author_external_id,
                    city, org_unit_id, raw_message, cleaned_message, cats,
                    analysis.get("subcategory") or "",
                    analysis.get("emotion") or "neutre",
                    keywords,
                    int(analysis.get("priority_score") or 0),
                    analysis.get("language") or "fr",
                    _vec_to_pg_literal(embedding),
                    Json(metadata or {}),
                ),
            )
        else:
            emb_bytes = psycopg2.Binary(vec_to_bytes(embedding)) if embedding else None
            cur.execute(
                """INSERT INTO audience_reports
                   (tenant_id, source, platform, author_name, author_external_id,
                    city, org_unit_id, raw_message, cleaned_message, categories, subcategory,
                    emotion, keywords, priority_score, language, embedding, metadata)
                   VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    tenant_id, source, platform, author_name, author_external_id,
                    city, org_unit_id, raw_message, cleaned_message, cats,
                    analysis.get("subcategory") or "",
                    analysis.get("emotion") or "neutre",
                    keywords,
                    int(analysis.get("priority_score") or 0),
                    analysis.get("language") or "fr",
                    emb_bytes,
                    Json(metadata or {}),
                ),
            )
        row = dict(cur.fetchone())

        # Bump trend counters pour CHAQUE category. NB : les compteurs
        # 24h/7d/30d ici servent uniquement de live counters incrémentaux ;
        # le recalcul exact (avec décroissance temporelle) est fait par
        # trends.py (cron 1h, Phase 1).
        for cat in cats:
            cur.execute(
                """INSERT INTO audience_trends (tenant_id, category, city,
                                                total_mentions_24h, total_mentions_7d,
                                                total_mentions_30d, top_emotion, last_updated)
                   VALUES (%s,%s,%s,1,1,1,%s, now())
                   ON CONFLICT (tenant_id, category) WHERE city IS NULL DO UPDATE SET
                       total_mentions_24h = audience_trends.total_mentions_24h + 1,
                       total_mentions_7d  = audience_trends.total_mentions_7d  + 1,
                       total_mentions_30d = audience_trends.total_mentions_30d + 1,
                       top_emotion = EXCLUDED.top_emotion,
                       last_updated = now()""",
                (tenant_id, cat, None, analysis.get("emotion") or "neutre"),
            )
            if city:
                cur.execute(
                    """INSERT INTO audience_trends (tenant_id, category, city,
                                                    total_mentions_24h, total_mentions_7d,
                                                    total_mentions_30d, top_emotion, last_updated)
                       VALUES (%s,%s,%s,1,1,1,%s, now())
                       ON CONFLICT (tenant_id, category, city) WHERE city IS NOT NULL DO UPDATE SET
                           total_mentions_24h = audience_trends.total_mentions_24h + 1,
                           total_mentions_7d  = audience_trends.total_mentions_7d  + 1,
                           total_mentions_30d = audience_trends.total_mentions_30d + 1,
                           top_emotion = EXCLUDED.top_emotion,
                           last_updated = now()""",
                    (tenant_id, cat, city, analysis.get("emotion") or "neutre"),
                )

        c.commit()

    payload = _row_for_event(row)
    try:
        event_bus.publish(tenant_id, {"type": "report.created", "data": payload})
    except Exception as e:  # noqa: BLE001
        logger.warning("audience.event_bus publish failed: %s", e)
    return payload


def _row_for_event(row: dict[str, Any]) -> dict[str, Any]:
    """Sérialise une row pour JSON (drop embedding bytes, format datetime)."""
    out = dict(row)
    out.pop("embedding", None)
    if "created_at" in out and out["created_at"] is not None:
        out["created_at"] = out["created_at"].isoformat()
    return out


_REPORT_COLS = (
    "id, tenant_id, source, platform, author_name, author_external_id, "
    "city, org_unit_id, raw_message, cleaned_message, categories, subcategory, "
    "emotion, keywords, priority_score, language, metadata, created_at"
)


def get_report(tenant_id: int, report_id: int) -> Optional[dict[str, Any]]:
    with get_conn(dict_rows=True) as c, c.cursor() as cur:
        cur.execute(
            f"SELECT {_REPORT_COLS} FROM audience_reports WHERE tenant_id=%s AND id=%s",
            (tenant_id, report_id),
        )
        r = cur.fetchone()
        return _row_for_event(dict(r)) if r else None


def list_reports(
    tenant_id: int,
    *,
    category: Optional[str] = None,
    city: Optional[str] = None,
    source: Optional[str] = None,
    emotion: Optional[str] = None,
    min_priority: Optional[int] = None,
    visible_units: Optional[list[int]] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Liste les remontées pour un tenant.

    `visible_units` : si fourni (issu d'un JWT scopé), filtre `org_unit_id` à
    cette liste. None = pas de filtre (admin / no-scope). [] = aucun accès
    (retourne []).
    """
    if visible_units == []:
        return []

    where = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if category:
        where.append("%s = ANY(categories)"); params.append(category)
    if city:
        where.append("city = %s"); params.append(city)
    if source:
        where.append("source = %s"); params.append(source)
    if emotion:
        where.append("emotion = %s"); params.append(emotion)
    if min_priority is not None:
        where.append("priority_score >= %s"); params.append(int(min_priority))
    if visible_units is not None:
        where.append("org_unit_id = ANY(%s)"); params.append(list(visible_units))

    sql = (
        f"SELECT {_REPORT_COLS} FROM audience_reports WHERE "
        + " AND ".join(where)
        + " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    )
    params.extend([int(limit), int(offset)])

    with get_conn(dict_rows=True) as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    return [_row_for_event(r) for r in rows]


def list_trends(tenant_id: int, *, city: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    where = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if city is not None:
        where.append("city = %s"); params.append(city)
    sql = (
        "SELECT id, tenant_id, category, city, total_mentions_24h, total_mentions_7d, "
        "total_mentions_30d, trend_score, evolution_pct_7d, top_keywords, top_emotion, "
        "last_updated FROM audience_trends WHERE " + " AND ".join(where)
        + " ORDER BY total_mentions_24h DESC, last_updated DESC LIMIT %s"
    )
    params.append(int(limit))
    with get_conn(dict_rows=True) as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if r.get("last_updated") is not None:
            r["last_updated"] = r["last_updated"].isoformat()
    return rows


def list_alerts(tenant_id: int, *, status: Optional[str] = "pending", limit: int = 50) -> list[dict[str, Any]]:
    where = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if status:
        where.append("status = %s"); params.append(status)
    sql = (
        "SELECT id, tenant_id, alert_type, category, city, metric_value, threshold, "
        "title, description, status, generated_content_proposals, created_at, updated_at "
        "FROM audience_alerts WHERE " + " AND ".join(where)
        + " ORDER BY created_at DESC LIMIT %s"
    )
    params.append(int(limit))
    with get_conn(dict_rows=True) as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        for k in ("created_at", "updated_at"):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
    return rows


def dismiss_alert(tenant_id: int, alert_id: int) -> bool:
    with get_conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE audience_alerts SET status='dismissed', updated_at=now() "
            "WHERE tenant_id=%s AND id=%s",
            (tenant_id, alert_id),
        )
        c.commit()
        return cur.rowcount > 0


def count_reports(
    tenant_id: int,
    since_hours: int = 24,
    *,
    visible_units: Optional[list[int]] = None,
) -> int:
    if visible_units == []:
        return 0
    where = "tenant_id=%s AND created_at > now() - (%s || ' hours')::interval"
    params: list[Any] = [tenant_id, str(since_hours)]
    if visible_units is not None:
        where += " AND org_unit_id = ANY(%s)"
        params.append(list(visible_units))
    with get_conn() as c, c.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM audience_reports WHERE {where}", params)
        return int(cur.fetchone()[0])


def log_embed_access(
    tenant_id: int,
    *,
    endpoint: str,
    org_unit_id: Optional[int],
    role: Optional[str],
    user_ref: Optional[str],
    visible_units: Optional[list[int]],
    ip: Optional[str],
    user_agent: Optional[str],
    request_id: Optional[str],
    status_code: int,
) -> None:
    """Audit log embed (rétention 90j gérée par cron Phase 1)."""
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO audience_embed_audit
                   (tenant_id, endpoint, org_unit_id, role, user_ref,
                    visible_units, ip, user_agent, request_id, status_code)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (tenant_id, endpoint, org_unit_id, role, user_ref,
                 list(visible_units or []), ip, user_agent, request_id, status_code),
            )
            c.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("audience.log_embed_access failed: %s", e)


def purge_embed_audit(retention_days: int = 90) -> int:
    """À brancher en cron quotidien (Phase 1)."""
    with get_conn() as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM audience_embed_audit "
            "WHERE created_at < now() - (%s || ' days')::interval",
            (str(retention_days),),
        )
        c.commit()
        return cur.rowcount


def search_by_embedding(
    tenant_id: int,
    query_vec: list[float],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Recherche top-k par cosine similarity.

    pgvector → ORDER BY embedding <=> query (très rapide).
    BYTEA fallback → on charge les N derniers reports et calcule en Python.
    """
    use_vec = pgvector_available()
    if use_vec:
        with get_conn(dict_rows=True) as c, c.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, source, platform, city, raw_message,
                          cleaned_message, categories, subcategory, emotion,
                          keywords, priority_score, language, metadata, created_at,
                          1 - (embedding <=> %s::vector) AS score
                   FROM audience_reports
                   WHERE tenant_id = %s AND embedding IS NOT NULL
                   ORDER BY embedding <=> %s::vector
                   LIMIT %s""",
                (_vec_to_pg_literal(query_vec), tenant_id, _vec_to_pg_literal(query_vec), int(limit)),
            )
            rows = [dict(r) for r in cur.fetchall()]
    else:
        # Fallback : top 500 récents, cosine in-Python.
        from .nlp.embedder import bytes_to_vec, cosine
        with get_conn(dict_rows=True) as c, c.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, source, platform, city, raw_message,
                          cleaned_message, categories, subcategory, emotion,
                          keywords, priority_score, language, metadata, created_at,
                          embedding
                   FROM audience_reports
                   WHERE tenant_id = %s AND embedding IS NOT NULL
                   ORDER BY created_at DESC LIMIT 500""",
                (tenant_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            v = bytes_to_vec(bytes(r["embedding"])) if r.get("embedding") else []
            r["score"] = cosine(query_vec, v) if v else 0.0
            r.pop("embedding", None)
        rows.sort(key=lambda x: x["score"], reverse=True)
        rows = rows[: int(limit)]

    for r in rows:
        if r.get("created_at") is not None:
            r["created_at"] = r["created_at"].isoformat()
    return rows
