"""bizzi.comms.sms.sms_log — log/query SMS sortants.

Table sms_logs (cf. comms/migrations/001_sms_logs.sql).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .._db import get_conn


# ── INSERT ────────────────────────────────────────────────────────

def log_sms(
    *,
    tenant_id: int,
    to_phone: str,
    body: str,
    provider: str,
    agent_id: Optional[int] = None,
    sender_id: Optional[str] = None,
    template_id: Optional[str] = None,
    template_context: Optional[dict] = None,
    status: str = "pending",
    shadow: bool = True,
    estimated_cost_eur: float = 0.0,
    segments: int = 1,
    scheduled_at: Optional[datetime] = None,
    metadata: Optional[dict] = None,
    created_by: Optional[str] = None,
) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sms_logs
                 (tenant_id, agent_id, to_phone, sender_id, body,
                  template_id, template_context, provider,
                  status, shadow, cost_eur, segments,
                  scheduled_at, metadata, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
               RETURNING id""",
            (
                tenant_id, agent_id, to_phone, sender_id, body,
                template_id, json.dumps(template_context or {}), provider,
                status, shadow, estimated_cost_eur, segments,
                scheduled_at, json.dumps(metadata or {}), created_by,
            ),
        )
        sms_id = cur.fetchone()[0]
        conn.commit()
        return sms_id


# ── UPDATE ────────────────────────────────────────────────────────

def update_status(
    sms_id: int,
    status: str,
    *,
    provider_message_id: Optional[str] = None,
    cost_eur: Optional[float] = None,
    segments: Optional[int] = None,
    error: Optional[str] = None,
    sent: bool = False,
    delivered: bool = False,
    metadata_patch: Optional[dict] = None,
) -> None:
    sets = ["status = %s", "updated_at = now()"]
    args: list = [status]
    if provider_message_id is not None:
        sets.append("provider_message_id = %s")
        args.append(provider_message_id)
    if cost_eur is not None:
        sets.append("cost_eur = %s")
        args.append(cost_eur)
    if segments is not None:
        sets.append("segments = %s")
        args.append(segments)
    if error is not None:
        sets.append("error = %s")
        args.append(error)
    now = datetime.now(timezone.utc)
    if sent:
        sets.append("sent_at = %s")
        args.append(now)
    if delivered:
        sets.append("delivered_at = %s")
        args.append(now)
    if metadata_patch:
        sets.append("metadata = metadata || %s::jsonb")
        args.append(json.dumps(metadata_patch))
    args.append(sms_id)
    sql = f"UPDATE sms_logs SET {', '.join(sets)} WHERE id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def approve(sms_id: int, approved_by: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE sms_logs SET status='approved',
                                   approved_by=%s,
                                   approved_at=now(),
                                   updated_at=now()
               WHERE id=%s AND status='pending'""",
            (approved_by, sms_id),
        )
        conn.commit()


def reject(sms_id: int, approved_by: str, reason: str = "") -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE sms_logs SET status='rejected',
                                   approved_by=%s,
                                   approved_at=now(),
                                   error=COALESCE(NULLIF(%s,''), error),
                                   updated_at=now()
               WHERE id=%s AND status IN ('pending','approved')""",
            (approved_by, reason, sms_id),
        )
        conn.commit()


# ── QUERY ─────────────────────────────────────────────────────────

def get(sms_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM sms_logs WHERE id = %s", (sms_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_by_provider_id(provider: str, provider_message_id: str) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM sms_logs WHERE provider=%s AND provider_message_id=%s",
            (provider, provider_message_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_logs(
    tenant_id: int,
    limit: int = 50,
    status: Optional[str] = None,
) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        if status:
            cur.execute(
                """SELECT id, agent_id, to_phone, body, template_id, provider,
                          provider_message_id, status, shadow, cost_eur, segments,
                          error, sent_at, delivered_at, created_at
                   FROM sms_logs WHERE tenant_id=%s AND status=%s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, status, limit),
            )
        else:
            cur.execute(
                """SELECT id, agent_id, to_phone, body, template_id, provider,
                          provider_message_id, status, shadow, cost_eur, segments,
                          error, sent_at, delivered_at, created_at
                   FROM sms_logs WHERE tenant_id=%s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
        return [dict(r) for r in cur.fetchall()]


def list_pending(tenant_id: int, limit: int = 50) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, to_phone, body, template_id, provider,
                      cost_eur, segments, created_at
               FROM sms_logs
               WHERE tenant_id=%s AND status='pending'
               ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_month_spent_eur(tenant_id: int) -> float:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(cost_eur), 0)
               FROM sms_logs
               WHERE tenant_id = %s
                 AND status IN ('sent','delivered')
                 AND date_trunc('month', sent_at) = date_trunc('month', now())""",
            (tenant_id,),
        )
        return float(cur.fetchone()[0] or 0.0)
