"""bizzi.phone.call_log — log/query appels téléphoniques.

Table calls(id, tenant_id, agent_id, contact_id, direction, status, phone_number,
            started_at, ended_at, duration_seconds, recording_url, transcript jsonb,
            summary, voice_profile_id, metadata jsonb, ...).

Convention : cost_eur, outcome, provider_call_id, use_case, shadow_mode, validation
sont stockés dans metadata jsonb (pas de colonne dédiée pour rester compatible
avec le schéma existant).
"""
import json
from datetime import datetime, timezone
from typing import Optional
from ._db import get_conn


def log_call(
    tenant_id: int,
    agent_id: int,
    contact_id: Optional[int],
    direction: str = "outbound",
    status: str = "initiated",
    phone_number: Optional[str] = None,
    use_case: Optional[str] = None,
    provider: str = "vapi",
    provider_call_id: Optional[str] = None,
    shadow_mode: bool = False,
    estimated_cost_eur: float = 0.0,
    extra_metadata: Optional[dict] = None,
) -> int:
    """Crée une entrée calls et retourne id."""
    metadata = {
        "use_case": use_case,
        "provider": provider,
        "provider_call_id": provider_call_id,
        "shadow_mode": shadow_mode,
        "estimated_cost_eur": estimated_cost_eur,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO calls (tenant_id, agent_id, contact_id, direction, status,
                 phone_number, started_at, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
               RETURNING id""",
            (tenant_id, agent_id, contact_id, direction, status, phone_number,
             datetime.now(timezone.utc), json.dumps(metadata)),
        )
        call_id = cur.fetchone()[0]
        conn.commit()
        return call_id


def update_call_result(
    call_id: int,
    status: str,
    duration_seconds: Optional[int] = None,
    transcript: Optional[list] = None,
    summary: Optional[str] = None,
    recording_url: Optional[str] = None,
    cost_eur: Optional[float] = None,
    outcome: Optional[str] = None,
    ended: bool = False,
) -> None:
    """Met à jour un call après la fin (ou un événement webhook)."""
    sets = ["status = %s"]
    args: list = [status]
    if duration_seconds is not None:
        sets.append("duration_seconds = %s")
        args.append(duration_seconds)
    if transcript is not None:
        sets.append("transcript = %s::jsonb")
        args.append(json.dumps(transcript))
    if summary is not None:
        sets.append("summary = %s")
        args.append(summary)
    if recording_url is not None:
        sets.append("recording_url = %s")
        args.append(recording_url)
    if ended:
        sets.append("ended_at = %s")
        args.append(datetime.now(timezone.utc))
    if cost_eur is not None or outcome is not None:
        meta_patch = {}
        if cost_eur is not None:
            meta_patch["cost_eur"] = cost_eur
        if outcome is not None:
            meta_patch["outcome"] = outcome
        sets.append("metadata = metadata || %s::jsonb")
        args.append(json.dumps(meta_patch))
    args.append(call_id)
    sql = f"UPDATE calls SET {', '.join(sets)} WHERE id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def get_call(call_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM calls WHERE id = %s", (call_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_call_logs(tenant_id: int, limit: int = 50, status: Optional[str] = None) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        if status:
            cur.execute(
                """SELECT id, agent_id, contact_id, direction, status, phone_number,
                          started_at, ended_at, duration_seconds, summary, metadata
                   FROM calls WHERE tenant_id = %s AND status = %s
                   ORDER BY started_at DESC LIMIT %s""",
                (tenant_id, status, limit),
            )
        else:
            cur.execute(
                """SELECT id, agent_id, contact_id, direction, status, phone_number,
                          started_at, ended_at, duration_seconds, summary, metadata
                   FROM calls WHERE tenant_id = %s
                   ORDER BY started_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
        return [dict(r) for r in cur.fetchall()]


def list_active(tenant_id: int) -> list[dict]:
    return get_call_logs(tenant_id, limit=20, status="initiated") + \
           get_call_logs(tenant_id, limit=20, status="ringing") + \
           get_call_logs(tenant_id, limit=20, status="answered")


def list_pending_validation(tenant_id: int) -> list[dict]:
    """Appels en mode shadow en attente de validation Pascal."""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, contact_id, phone_number, started_at, metadata
               FROM calls
               WHERE tenant_id = %s
                 AND status = 'initiated'
                 AND metadata->>'shadow_mode' = 'true'
                 AND (metadata->>'validation' IS NULL OR metadata->>'validation' = 'pending')
               ORDER BY started_at DESC""",
            (tenant_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def search_transcripts(tenant_id: int, query: str, limit: int = 20) -> list[dict]:
    pattern = f"%{query}%"
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, contact_id, started_at, summary, transcript
               FROM calls
               WHERE tenant_id = %s
                 AND (summary ILIKE %s OR transcript::text ILIKE %s)
               ORDER BY started_at DESC LIMIT %s""",
            (tenant_id, pattern, pattern, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_month_spent_eur(tenant_id: int) -> float:
    """Somme des cost_eur stockés en metadata pour le mois en cours."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM((metadata->>'cost_eur')::float), 0)
               FROM calls
               WHERE tenant_id = %s
                 AND date_trunc('month', started_at) = date_trunc('month', now())""",
            (tenant_id,),
        )
        return float(cur.fetchone()[0] or 0.0)
