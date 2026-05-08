"""bizzi.comms.calendar.event_log — log/query événements calendrier.

Table calendar_events (cf. comms/migrations/004_calendar_events.sql).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .._db import get_conn


# ── INSERT ────────────────────────────────────────────────────────

def log_event(
    *,
    tenant_id: int,
    title: str,
    start_at: datetime,
    end_at: datetime,
    provider: str,
    timezone_name: str = "Europe/Paris",
    description: Optional[str] = None,
    location: Optional[str] = None,
    organizer_email: Optional[str] = None,
    attendees: Optional[list[str]] = None,
    agent_id: Optional[int] = None,
    provider_calendar_id: Optional[str] = None,
    template_id: Optional[str] = None,
    template_context: Optional[dict] = None,
    reminders_minutes: Optional[list[int]] = None,
    status: str = "pending",
    shadow: bool = True,
    metadata: Optional[dict] = None,
    created_by: Optional[str] = None,
) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO calendar_events
                 (tenant_id, agent_id, provider, provider_calendar_id,
                  title, description, location, start_at, end_at, timezone,
                  organizer_email, attendees,
                  status, shadow,
                  reminders_minutes, template_id, template_context,
                  metadata, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
               RETURNING id""",
            (
                tenant_id, agent_id, provider, provider_calendar_id,
                title, description, location, start_at, end_at, timezone_name,
                organizer_email, attendees or [],
                status, shadow,
                reminders_minutes or [], template_id, json.dumps(template_context or {}),
                json.dumps(metadata or {}), created_by,
            ),
        )
        eid = cur.fetchone()[0]
        conn.commit()
        return eid


# ── UPDATE ────────────────────────────────────────────────────────

def update_event(
    event_id: int,
    *,
    status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    attendees: Optional[list[str]] = None,
    provider_event_id: Optional[str] = None,
    html_link: Optional[str] = None,
    ical_uid: Optional[str] = None,
    error: Optional[str] = None,
    metadata_patch: Optional[dict] = None,
) -> None:
    sets = ["updated_at = now()"]
    args: list = []
    if status is not None:
        sets.append("status = %s")
        args.append(status)
    if title is not None:
        sets.append("title = %s")
        args.append(title)
    if description is not None:
        sets.append("description = %s")
        args.append(description)
    if location is not None:
        sets.append("location = %s")
        args.append(location)
    if start_at is not None:
        sets.append("start_at = %s")
        args.append(start_at)
    if end_at is not None:
        sets.append("end_at = %s")
        args.append(end_at)
    if attendees is not None:
        sets.append("attendees = %s")
        args.append(attendees)
    if provider_event_id is not None:
        sets.append("provider_event_id = %s")
        args.append(provider_event_id)
    if html_link is not None:
        sets.append("html_link = %s")
        args.append(html_link)
    if ical_uid is not None:
        sets.append("ical_uid = %s")
        args.append(ical_uid)
    if error is not None:
        sets.append("error = %s")
        args.append(error)
    if metadata_patch:
        sets.append("metadata = metadata || %s::jsonb")
        args.append(json.dumps(metadata_patch))

    if len(sets) == 1:
        return

    args.append(event_id)
    sql = f"UPDATE calendar_events SET {', '.join(sets)} WHERE id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def approve(event_id: int, approved_by: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE calendar_events
               SET status='approved', approved_by=%s, approved_at=now(), updated_at=now()
               WHERE id=%s AND status='pending'""",
            (approved_by, event_id),
        )
        conn.commit()


def reject(event_id: int, approved_by: str, reason: str = "") -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE calendar_events
               SET status='rejected', approved_by=%s, approved_at=now(),
                   error=COALESCE(NULLIF(%s,''), error), updated_at=now()
               WHERE id=%s AND status IN ('pending','approved')""",
            (approved_by, reason, event_id),
        )
        conn.commit()


def cancel(event_id: int, cancelled_by: str, reason: str = "") -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE calendar_events
               SET status='cancelled', cancelled_by=%s, cancelled_at=now(),
                   error=COALESCE(NULLIF(%s,''), error), updated_at=now()
               WHERE id=%s AND status IN ('pending','approved','created','confirmed')""",
            (cancelled_by, reason, event_id),
        )
        conn.commit()


def append_reminder_sent(event_id: int, entry: dict) -> None:
    """Append à reminders_sent : {minutes_before, channel, sent_at, ref_id, ok}."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE calendar_events SET reminders_sent = reminders_sent || %s::jsonb, updated_at = now() WHERE id = %s",
            (json.dumps([entry]), event_id),
        )
        conn.commit()


# ── QUERY ─────────────────────────────────────────────────────────

def get(event_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM calendar_events WHERE id = %s", (event_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_by_provider_id(provider: str, provider_event_id: str) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM calendar_events WHERE provider=%s AND provider_event_id=%s",
            (provider, provider_event_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_events(
    tenant_id: int,
    *,
    status: Optional[str] = None,
    from_at: Optional[datetime] = None,
    to_at: Optional[datetime] = None,
    limit: int = 100,
) -> list[dict]:
    where = ["tenant_id = %s"]
    args: list = [tenant_id]
    if status:
        where.append("status = %s")
        args.append(status)
    if from_at:
        where.append("start_at >= %s")
        args.append(from_at)
    if to_at:
        where.append("start_at <= %s")
        args.append(to_at)
    args.append(limit)
    sql = f"""SELECT id, agent_id, provider, provider_event_id, provider_calendar_id,
                     title, description, location, start_at, end_at, timezone,
                     organizer_email, attendees,
                     status, shadow, reminders_minutes, reminders_sent,
                     html_link, ical_uid, error, created_at
              FROM calendar_events
              WHERE {' AND '.join(where)}
              ORDER BY start_at ASC LIMIT %s"""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def list_pending(tenant_id: int, limit: int = 50) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, title, start_at, end_at, attendees,
                      provider, template_id, created_at
               FROM calendar_events
               WHERE tenant_id=%s AND status='pending'
               ORDER BY start_at ASC LIMIT %s""",
            (tenant_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def list_due_reminders(
    *,
    tenant_id: Optional[int] = None,
    lookahead_minutes: int = 5,
) -> list[dict]:
    """Liste les événements pour lesquels un reminder est dû dans la fenêtre.

    Pour chaque event actif (status in created/confirmed) avec start_at > now,
    on retourne ceux dont au moins un `reminders_minutes[i]` correspond à
    `start_at - now ∈ [m, m + lookahead]` ET pas déjà envoyé.

    Filtre tenant_id optionnel. Pas de pagination (volume attendu faible).
    Le caller boucle et envoie via comms.sms / comms.mail.
    """
    where = ["status IN ('created','confirmed')", "start_at > now()", "array_length(reminders_minutes, 1) > 0"]
    args: list = []
    if tenant_id is not None:
        where.append("tenant_id = %s")
        args.append(tenant_id)
    args.append(lookahead_minutes)
    sql = f"""SELECT id, tenant_id, agent_id, title, description, location,
                     start_at, end_at, timezone, organizer_email, attendees,
                     reminders_minutes, reminders_sent, metadata
              FROM calendar_events
              WHERE {' AND '.join(where)}
                AND start_at <= now() + (
                    (SELECT MAX(m) FROM unnest(reminders_minutes) AS m)
                    || ' minutes'
                )::interval + (%s || ' minutes')::interval"""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def overlaps(
    tenant_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    organizer_email: Optional[str] = None,
    exclude_event_id: Optional[int] = None,
) -> list[dict]:
    """Renvoie les événements existants qui chevauchent [start_at, end_at).

    Conflit = overlap strict (touche-touche autorisé). Filtré sur status actif.
    Si `organizer_email` fourni, restreint au calendrier de cet organizer.
    """
    where = [
        "tenant_id = %s",
        "status IN ('approved','created','confirmed')",
        "start_at < %s",
        "end_at > %s",
    ]
    args: list = [tenant_id, end_at, start_at]
    if organizer_email:
        where.append("organizer_email = %s")
        args.append(organizer_email)
    if exclude_event_id is not None:
        where.append("id <> %s")
        args.append(exclude_event_id)
    sql = f"""SELECT id, title, start_at, end_at, organizer_email, attendees, status
              FROM calendar_events
              WHERE {' AND '.join(where)}
              ORDER BY start_at ASC"""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
