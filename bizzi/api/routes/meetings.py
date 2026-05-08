import os
"""api/routes/meetings.py
==========================
Salle de réunion virtuelle — endpoints.

POST /api/meetings/run     : lance une réunion (transcript persisté)
GET  /api/meetings/last    : dernière réunion du tenant
GET  /api/meetings/list    : historique (métadonnées)
GET  /api/meetings/{id}    : détail d'une réunion (isolation tenant)
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from moteur.team_loader import load_team
from moteur.meeting_room import MeetingRoom

router = APIRouter()
logger = logging.getLogger("api.meetings")

_db = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))


def require_tenant(request: Request) -> tuple[int, str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token requis")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token vide")
    with _db.connect() as conn:
        row = conn.execute(
            text("SELECT id, slug FROM tenants WHERE token_hash = :t"),
            {"t": token},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Token invalide")
    return row[0], row[1]


class RunRequest(BaseModel):
    agenda: list[str] = []


def _msg_to_dict(m) -> dict:
    return {"speaker": m.speaker, "role": m.role, "content": m.content, "time": m.time}


@router.post("/run")
async def run_meeting(data: RunRequest, request: Request):
    """Lance une réunion. Charge l'équipe DB, fait parler les agents, persiste."""
    tenant_id, tenant_slug = require_tenant(request)

    try:
        config, team = load_team(tenant_slug)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not team:
        raise HTTPException(
            status_code=400,
            detail=f"Aucun agent pour le tenant '{tenant_slug}'. Lance d'abord sync_agents.",
        )

    started = datetime.utcnow()
    room = MeetingRoom(domain=config, agents=team)
    report = await room.run(agenda=data.agenda or None)
    finished = datetime.utcnow()

    messages_json = [_msg_to_dict(m) for m in report.messages]

    with _db.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO meetings
                (tenant_id, agenda, participants, absent, messages,
                 decisions, assignments, absence_summaries,
                 duration_seconds, started_at, finished_at)
            VALUES
                (:tid,
                 CAST(:ag   AS jsonb),
                 CAST(:par  AS jsonb),
                 CAST(:abs  AS jsonb),
                 CAST(:msg  AS jsonb),
                 CAST(:dec  AS jsonb),
                 CAST(:asg  AS jsonb),
                 CAST(:asum AS jsonb),
                 :dur, :st, :fin)
            RETURNING id
        """), {
            "tid":  tenant_id,
            "ag":   json.dumps(data.agenda or []),
            "par":  json.dumps(report.participants),
            "abs":  json.dumps(report.absent),
            "msg":  json.dumps(messages_json),
            "dec":  json.dumps(report.decisions),
            "asg":  json.dumps(report.assignments),
            "asum": json.dumps(report.absence_summaries),
            "dur":  report.duration_seconds,
            "st":   started,
            "fin":  finished,
        }).fetchone()

    meeting_id = row[0]
    logger.info(
        f"[MEETING] tenant={tenant_slug} id={meeting_id} "
        f"{len(messages_json)} interventions · {len(report.decisions)} décisions · "
        f"{report.duration_seconds}s"
    )

    return {
        "id":               meeting_id,
        "tenant":           tenant_slug,
        "started_at":       started.isoformat(),
        "finished_at":      finished.isoformat(),
        "duration_seconds": report.duration_seconds,
        "agenda":           data.agenda or [],
        "participants":     report.participants,
        "absent":           report.absent,
        "messages":         messages_json,
        "decisions":        report.decisions,
        "assignments":      report.assignments,
        "absence_summaries": report.absence_summaries,
    }


@router.get("/last")
async def last_meeting(request: Request):
    tenant_id, tenant_slug = require_tenant(request)
    with _db.connect() as conn:
        row = conn.execute(text("""
            SELECT id, agenda, participants, absent, messages, decisions,
                   assignments, absence_summaries, duration_seconds,
                   started_at, finished_at
            FROM meetings
            WHERE tenant_id = :tid
            ORDER BY started_at DESC
            LIMIT 1
        """), {"tid": tenant_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Aucune réunion pour ce tenant")
    return {
        "id":                row[0],
        "tenant":            tenant_slug,
        "agenda":            row[1] or [],
        "participants":      row[2] or [],
        "absent":            row[3] or [],
        "messages":          row[4] or [],
        "decisions":         row[5] or [],
        "assignments":       row[6] or {},
        "absence_summaries": row[7] or {},
        "duration_seconds":  row[8],
        "started_at":        row[9].isoformat() if row[9] else None,
        "finished_at":       row[10].isoformat() if row[10] else None,
    }


@router.get("/list")
async def list_meetings(request: Request, limit: int = 20):
    tenant_id, tenant_slug = require_tenant(request)
    with _db.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, agenda,
                   jsonb_array_length(messages)  AS msg_count,
                   jsonb_array_length(decisions) AS dec_count,
                   duration_seconds, started_at
            FROM meetings
            WHERE tenant_id = :tid
            ORDER BY started_at DESC
            LIMIT :lim
        """), {"tid": tenant_id, "lim": min(limit, 100)}).fetchall()
    return {
        "tenant":   tenant_slug,
        "count":    len(rows),
        "meetings": [{
            "id":               r[0],
            "agenda":           r[1] or [],
            "messages_count":   r[2] or 0,
            "decisions_count":  r[3] or 0,
            "duration_seconds": r[4],
            "started_at":       r[5].isoformat() if r[5] else None,
        } for r in rows],
    }


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: int, request: Request):
    tenant_id, tenant_slug = require_tenant(request)
    with _db.connect() as conn:
        row = conn.execute(text("""
            SELECT id, agenda, participants, absent, messages, decisions,
                   assignments, absence_summaries, duration_seconds,
                   started_at, finished_at
            FROM meetings
            WHERE tenant_id = :tid AND id = :mid
        """), {"tid": tenant_id, "mid": meeting_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Réunion {meeting_id} introuvable")
    return {
        "id":                row[0],
        "tenant":            tenant_slug,
        "agenda":            row[1] or [],
        "participants":      row[2] or [],
        "absent":            row[3] or [],
        "messages":          row[4] or [],
        "decisions":         row[5] or [],
        "assignments":       row[6] or {},
        "absence_summaries": row[7] or {},
        "duration_seconds":  row[8],
        "started_at":        row[9].isoformat() if row[9] else None,
        "finished_at":       row[10].isoformat() if row[10] else None,
    }
