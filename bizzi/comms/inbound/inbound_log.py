"""bizzi.comms.inbound.inbound_log — log/query appels téléphoniques entrants.

Table inbound_call_logs (cf. comms/migrations/003_inbound_call_logs.sql).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .._db import get_conn


# ── INSERT ────────────────────────────────────────────────────────

def log_call(
    *,
    tenant_id: int,
    provider: str,
    provider_call_id: Optional[str] = None,
    from_phone: Optional[str] = None,
    to_phone: Optional[str] = None,
    caller_name: Optional[str] = None,
    agent_id: Optional[int] = None,
    status: str = "received",
    started_at: Optional[datetime] = None,
    answered_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    duration_seconds: Optional[int] = None,
    recording_url: Optional[str] = None,
    transcript: Optional[list] = None,
    summary: Optional[str] = None,
    cost_eur: float = 0.0,
    metadata: Optional[dict] = None,
) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO inbound_call_logs
                 (tenant_id, agent_id, provider, provider_call_id,
                  from_phone, to_phone, caller_name,
                  status, started_at, answered_at, ended_at, duration_seconds,
                  recording_url, transcript, summary, cost_eur, metadata)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)
               RETURNING id""",
            (
                tenant_id, agent_id, provider, provider_call_id,
                from_phone, to_phone, caller_name,
                status, started_at, answered_at, ended_at, duration_seconds,
                recording_url, json.dumps(transcript or []), summary, cost_eur,
                json.dumps(metadata or {}),
            ),
        )
        call_id = cur.fetchone()[0]
        conn.commit()
        return call_id


# ── UPDATE ────────────────────────────────────────────────────────

def update_call(
    call_id: int,
    *,
    status: Optional[str] = None,
    answered_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    duration_seconds: Optional[int] = None,
    recording_url: Optional[str] = None,
    transcript: Optional[list] = None,
    summary: Optional[str] = None,
    cost_eur: Optional[float] = None,
    error: Optional[str] = None,
    metadata_patch: Optional[dict] = None,
) -> None:
    sets = ["updated_at = now()"]
    args: list = []
    if status is not None:
        sets.append("status = %s")
        args.append(status)
    if answered_at is not None:
        sets.append("answered_at = %s")
        args.append(answered_at)
    if ended_at is not None:
        sets.append("ended_at = %s")
        args.append(ended_at)
    if duration_seconds is not None:
        sets.append("duration_seconds = %s")
        args.append(duration_seconds)
    if recording_url is not None:
        sets.append("recording_url = %s")
        args.append(recording_url)
    if transcript is not None:
        sets.append("transcript = %s::jsonb")
        args.append(json.dumps(transcript))
    if summary is not None:
        sets.append("summary = %s")
        args.append(summary)
    if cost_eur is not None:
        sets.append("cost_eur = %s")
        args.append(cost_eur)
    if error is not None:
        sets.append("error = %s")
        args.append(error)
    if metadata_patch:
        sets.append("metadata = metadata || %s::jsonb")
        args.append(json.dumps(metadata_patch))

    if len(sets) == 1:  # rien à patcher
        return

    args.append(call_id)
    sql = f"UPDATE inbound_call_logs SET {', '.join(sets)} WHERE id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def update_qualification(
    call_id: int,
    *,
    intent: Optional[str] = None,
    urgency: Optional[int] = None,
    suggested_action: Optional[str] = None,
    extracted: Optional[dict] = None,
    confidence: Optional[float] = None,
    requires_human: Optional[bool] = None,
) -> None:
    sets = ["updated_at = now()"]
    args: list = []
    if intent is not None:
        sets.append("intent = %s")
        args.append(intent)
    if urgency is not None:
        sets.append("urgency = %s")
        args.append(urgency)
    if suggested_action is not None:
        sets.append("suggested_action = %s")
        args.append(suggested_action)
    if extracted is not None:
        sets.append("extracted = %s::jsonb")
        args.append(json.dumps(extracted))
    if confidence is not None:
        sets.append("confidence = %s")
        args.append(float(confidence))
    if requires_human is not None:
        sets.append("requires_human = %s")
        args.append(bool(requires_human))

    if len(sets) == 1:
        return

    args.append(call_id)
    sql = f"UPDATE inbound_call_logs SET {', '.join(sets)} WHERE id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def append_action(call_id: int, action: dict) -> None:
    """Ajoute une action effectuée (sms_sent, mail_sent, ticket, transfer_failed, …) à l'array `actions`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE inbound_call_logs SET actions = actions || %s::jsonb, updated_at = now() WHERE id = %s",
            (json.dumps([action]), call_id),
        )
        conn.commit()


# ── QUERY ─────────────────────────────────────────────────────────

def get(call_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM inbound_call_logs WHERE id = %s", (call_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_by_provider_id(provider: str, provider_call_id: str) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM inbound_call_logs WHERE provider=%s AND provider_call_id=%s",
            (provider, provider_call_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_logs(
    tenant_id: int,
    limit: int = 50,
    status: Optional[str] = None,
    intent: Optional[str] = None,
    requires_human: Optional[bool] = None,
) -> list[dict]:
    where = ["tenant_id = %s"]
    args: list = [tenant_id]
    if status:
        where.append("status = %s")
        args.append(status)
    if intent:
        where.append("intent = %s")
        args.append(intent)
    if requires_human is not None:
        where.append("requires_human = %s")
        args.append(bool(requires_human))
    args.append(limit)
    sql = f"""SELECT id, agent_id, provider, provider_call_id,
                     from_phone, to_phone, caller_name,
                     status, started_at, answered_at, ended_at, duration_seconds,
                     summary, intent, urgency, suggested_action,
                     extracted, confidence, requires_human, actions,
                     cost_eur, created_at
              FROM inbound_call_logs
              WHERE {' AND '.join(where)}
              ORDER BY created_at DESC LIMIT %s"""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
