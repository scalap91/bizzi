"""bizzi.comms.mail.mail_log — log/query emails sortants.

Table mail_logs (cf. comms/migrations/002_mail_logs.sql).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .._db import get_conn


# ── INSERT ────────────────────────────────────────────────────────

def log_mail(
    *,
    tenant_id: int,
    to_addrs: list[str],
    subject: str,
    provider: str,
    html: Optional[str] = None,
    text: Optional[str] = None,
    cc_addrs: Optional[list[str]] = None,
    bcc_addrs: Optional[list[str]] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    agent_id: Optional[int] = None,
    template_id: Optional[str] = None,
    template_context: Optional[dict] = None,
    attachments_meta: Optional[list[dict]] = None,
    track_opens: bool = True,
    track_clicks: bool = True,
    status: str = "pending",
    shadow: bool = True,
    estimated_cost_eur: float = 0.0,
    scheduled_at: Optional[datetime] = None,
    metadata: Optional[dict] = None,
    created_by: Optional[str] = None,
) -> int:
    atts = attachments_meta or []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_logs
                 (tenant_id, agent_id, to_addrs, cc_addrs, bcc_addrs,
                  from_email, from_name, reply_to,
                  subject, html, text, template_id, template_context,
                  attachments_meta, has_attachments,
                  provider, status, shadow,
                  track_opens, track_clicks, cost_eur,
                  scheduled_at, metadata, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                       %s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
               RETURNING id""",
            (
                tenant_id, agent_id, to_addrs,
                cc_addrs or [], bcc_addrs or [],
                from_email, from_name, reply_to,
                subject, html, text, template_id, json.dumps(template_context or {}),
                json.dumps(atts), bool(atts),
                provider, status, shadow,
                track_opens, track_clicks, estimated_cost_eur,
                scheduled_at, json.dumps(metadata or {}), created_by,
            ),
        )
        mail_id = cur.fetchone()[0]
        conn.commit()
        return mail_id


# ── UPDATE ────────────────────────────────────────────────────────

def update_status(
    mail_id: int,
    status: str,
    *,
    provider_message_id: Optional[str] = None,
    cost_eur: Optional[float] = None,
    error: Optional[str] = None,
    sent: bool = False,
    delivered: bool = False,
    bounced: bool = False,
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
    if bounced:
        sets.append("bounced_at = %s")
        args.append(now)
    if metadata_patch:
        sets.append("metadata = metadata || %s::jsonb")
        args.append(json.dumps(metadata_patch))
    args.append(mail_id)
    sql = f"UPDATE mail_logs SET {', '.join(sets)} WHERE id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def increment_open(mail_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_logs
               SET opens = opens + 1, last_open_at = now(), updated_at = now()
               WHERE id = %s""",
            (mail_id,),
        )
        conn.commit()


def increment_click(mail_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_logs
               SET clicks = clicks + 1, last_click_at = now(), updated_at = now()
               WHERE id = %s""",
            (mail_id,),
        )
        conn.commit()


def approve(mail_id: int, approved_by: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_logs SET status='approved',
                                    approved_by=%s,
                                    approved_at=now(),
                                    updated_at=now()
               WHERE id=%s AND status='pending'""",
            (approved_by, mail_id),
        )
        conn.commit()


def reject(mail_id: int, approved_by: str, reason: str = "") -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_logs SET status='rejected',
                                    approved_by=%s,
                                    approved_at=now(),
                                    error=COALESCE(NULLIF(%s,''), error),
                                    updated_at=now()
               WHERE id=%s AND status IN ('pending','approved')""",
            (approved_by, reason, mail_id),
        )
        conn.commit()


# ── QUERY ─────────────────────────────────────────────────────────

def get(mail_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mail_logs WHERE id = %s", (mail_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_by_provider_id(provider: str, provider_message_id: str) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM mail_logs WHERE provider=%s AND provider_message_id=%s",
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
                """SELECT id, agent_id, to_addrs, subject, template_id, provider,
                          provider_message_id, status, shadow, cost_eur,
                          opens, clicks, last_open_at, last_click_at,
                          error, sent_at, delivered_at, bounced_at, created_at
                   FROM mail_logs WHERE tenant_id=%s AND status=%s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, status, limit),
            )
        else:
            cur.execute(
                """SELECT id, agent_id, to_addrs, subject, template_id, provider,
                          provider_message_id, status, shadow, cost_eur,
                          opens, clicks, last_open_at, last_click_at,
                          error, sent_at, delivered_at, bounced_at, created_at
                   FROM mail_logs WHERE tenant_id=%s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
        return [dict(r) for r in cur.fetchall()]


def list_pending(tenant_id: int, limit: int = 50) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, to_addrs, subject, template_id, provider,
                      cost_eur, has_attachments, created_at
               FROM mail_logs
               WHERE tenant_id=%s AND status='pending'
               ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_month_spent_eur(tenant_id: int) -> float:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(cost_eur), 0)
               FROM mail_logs
               WHERE tenant_id = %s
                 AND status IN ('sent','delivered','bounced','complained')
                 AND date_trunc('month', sent_at) = date_trunc('month', now())""",
            (tenant_id,),
        )
        return float(cur.fetchone()[0] or 0.0)


def count_recent_for_email(tenant_id: int, email: str, hours: int = 24) -> int:
    """Compte les mails récents (status non-rejected) envoyés à `email`. Utilisé par rate_limit."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM mail_logs
               WHERE tenant_id = %s
                 AND %s = ANY(to_addrs)
                 AND status NOT IN ('rejected')
                 AND created_at > now() - (%s || ' hours')::interval""",
            (tenant_id, email, str(hours)),
        )
        return int(cur.fetchone()[0] or 0)


def count_recent_for_tenant(tenant_id: int, hours: int = 1) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM mail_logs
               WHERE tenant_id = %s
                 AND status NOT IN ('rejected')
                 AND created_at > now() - (%s || ' hours')::interval""",
            (tenant_id, str(hours)),
        )
        return int(cur.fetchone()[0] or 0)
