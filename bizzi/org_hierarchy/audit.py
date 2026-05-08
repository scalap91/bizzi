"""bizzi.org_hierarchy.audit — Logs de conformité.

Toute requête embed (iframe) DOIT être loggée avec :
- tenant_id, user_id, role, org_unit_id (scope JWT)
- IP, path, method, query
- timestamp

Retention 90j (purge cron en Phase 1).
Export disponible pour responsable_territorial+ (Phase 1).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from ._db import get_conn


def log_request(
    tenant_id: int,
    role: str,
    user_id: Optional[str],
    org_unit_id: Optional[int],
    path: str,
    method: str,
    ip: Optional[str] = None,
    query: Optional[dict[str, Any]] = None,
    status_code: Optional[int] = None,
) -> int:
    sql = """
        INSERT INTO org_audit_log (
            tenant_id, role, user_id, org_unit_id, path, method, ip, query, status_code
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                tenant_id,
                role,
                user_id,
                org_unit_id,
                path,
                method,
                ip,
                json.dumps(query) if query else None,
                status_code,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


def export_logs(
    tenant_id: int,
    user_id: Optional[str] = None,
    org_unit_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 1000,
) -> list[dict]:
    """Export filtré pour conformité (responsable_territorial+).

    Limit pour éviter exports massifs. Phase 1.5 : pagination + streaming CSV.
    """
    sql = "SELECT * FROM org_audit_log WHERE tenant_id = %s"
    params: list = [tenant_id]
    if user_id:
        sql += " AND user_id = %s"
        params.append(user_id)
    if org_unit_id is not None:
        sql += " AND org_unit_id = %s"
        params.append(org_unit_id)
    if since:
        sql += " AND created_at >= %s"
        params.append(since)
    if until:
        sql += " AND created_at <= %s"
        params.append(until)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def purge_old_logs(retention_days: int = 90) -> int:
    """Purge des logs > retention_days. Retourne le count supprimé.

    Cron-able : à brancher sur un timer (Phase 2). Sécurité : ne jamais
    supprimer < 7j même si retention_days < 7.
    """
    safe_days = max(retention_days, 7)
    sql = "DELETE FROM org_audit_log WHERE created_at < NOW() - INTERVAL '%s days'"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql % safe_days)  # INTERVAL ne supporte pas %s bind
        deleted = cur.rowcount
        conn.commit()
        return deleted
