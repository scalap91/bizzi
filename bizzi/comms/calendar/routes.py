"""Routes FastAPI /api/comms/calendar/* — Phase 1.

Endpoints :
  POST   /api/comms/calendar/events             → créer un RDV (shadow par défaut)
  GET    /api/comms/calendar/events?tenant=…    → liste
  GET    /api/comms/calendar/events/{id}        → détail
  GET    /api/comms/calendar/pending            → file de validation
  POST   /api/comms/calendar/events/{id}/validate  → approve | reject
  DELETE /api/comms/calendar/events/{id}        → cancel
  GET    /api/comms/calendar/availability       → freebusy (interne ou provider)
  POST   /api/comms/calendar/reminders/run      → déclenche le scan reminders
  GET    /api/comms/calendar/health             → health provider

Auth : Bearer token tenant (réutilise TENANT_TOKENS).
Wiring dans api/main.py : PAS encore (validation Pascal requise = action prod).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import _db
from . import event_log, orchestrator, reminders

router = APIRouter()


# ── Tenant auth ──────────────────────────────────────────────────

async def get_tenant_slug(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer ", "").strip()
    from api.main import TENANT_TOKENS
    slug = TENANT_TOKENS.get(token)
    if not slug:
        raise HTTPException(status_code=401, detail="Token invalide")
    return slug


def _tenant_id_from_slug(slug: str) -> int:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"tenant {slug} introuvable")
        return row[0]


# ── Schemas ──────────────────────────────────────────────────────

class CreateEventBody(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_at: datetime
    end_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    timezone: str = "Europe/Paris"
    organizer_email: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    template_id: Optional[str] = None
    template_context: dict = Field(default_factory=dict)
    reminders_minutes: Optional[list[int]] = None
    calendar_id: Optional[str] = None
    send_invites: bool = True
    agent_id: Optional[int] = None
    use_case: Optional[str] = None
    force_live: bool = False
    check_external_conflicts: bool = False


class ValidateBody(BaseModel):
    decision: str  # approve | reject
    approved_by: str


class CancelBody(BaseModel):
    cancelled_by: str
    reason: str = ""


class RunRemindersBody(BaseModel):
    lookahead_minutes: int = 5
    channels: Optional[list[str]] = None  # ['sms','mail']
    all_tenants: bool = False             # si True, scope global (à éviter sur API publique)


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/events")
async def post_create_event(body: CreateEventBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    result = await orchestrator.create_event(
        tenant_id=tenant_id,
        title=body.title,
        description=body.description,
        location=body.location,
        start_at=body.start_at,
        end_at=body.end_at,
        duration_minutes=body.duration_minutes,
        timezone_name=body.timezone,
        organizer_email=body.organizer_email,
        attendees=body.attendees,
        template_id=body.template_id,
        template_context=body.template_context,
        reminders_minutes=body.reminders_minutes,
        calendar_id=body.calendar_id,
        send_invites=body.send_invites,
        agent_id=body.agent_id,
        use_case=body.use_case,
        force_live=body.force_live,
        check_external_conflicts=body.check_external_conflicts,
        created_by=slug,
    )
    if "error" in result and "event_id" not in result:
        raise HTTPException(400, detail=result)
    return result


@router.get("/events")
async def list_events(
    slug: str = Depends(get_tenant_slug),
    status: Optional[str] = None,
    from_at: Optional[datetime] = None,
    to_at: Optional[datetime] = None,
    limit: int = 100,
):
    tenant_id = _tenant_id_from_slug(slug)
    return {
        "tenant": slug,
        "events": event_log.list_events(
            tenant_id, status=status, from_at=from_at, to_at=to_at, limit=limit,
        ),
    }


@router.get("/pending")
async def list_pending(slug: str = Depends(get_tenant_slug), limit: int = 50):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "pending": event_log.list_pending(tenant_id, limit=limit)}


@router.get("/events/{event_id}")
async def get_event(event_id: int, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    rec = event_log.get(event_id)
    if not rec or rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "event introuvable pour ce tenant")
    return rec


@router.post("/events/{event_id}/validate")
async def validate_event(event_id: int, body: ValidateBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    rec = event_log.get(event_id)
    if not rec or rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "event introuvable pour ce tenant")
    result = await orchestrator.validate_pending(event_id, body.decision, approved_by=body.approved_by)
    if "error" in result and "event_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, body: CancelBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    rec = event_log.get(event_id)
    if not rec or rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "event introuvable pour ce tenant")
    result = await orchestrator.cancel_event(event_id, cancelled_by=body.cancelled_by, reason=body.reason)
    if "error" in result and "event_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/availability")
async def availability(
    slug: str = Depends(get_tenant_slug),
    from_at: datetime = ...,           # type: ignore[assignment]
    to_at: datetime = ...,              # type: ignore[assignment]
    calendar_id: Optional[str] = None,
    use_external: bool = False,
):
    tenant_id = _tenant_id_from_slug(slug)
    return await orchestrator.list_availability(
        tenant_id=tenant_id, from_at=from_at, to_at=to_at,
        calendar_id=calendar_id, use_external=use_external,
    )


@router.post("/reminders/run")
async def run_reminders(body: RunRemindersBody, slug: str = Depends(get_tenant_slug)):
    """Déclenche un scan reminders. Par défaut scope tenant courant.

    Pour scope global, demander un token admin (Phase 2). Phase 1 : on bloque.
    """
    if body.all_tenants:
        raise HTTPException(403, "all_tenants=True nécessite un token admin (Phase 2)")
    tenant_id = _tenant_id_from_slug(slug)
    return await reminders.run_due_reminders(
        tenant_id=tenant_id,
        lookahead_minutes=body.lookahead_minutes,
        channels=body.channels,
    )


@router.get("/health")
async def calendar_health(provider: Optional[str] = None):
    if not provider:
        return {
            "module": "comms.calendar",
            "phase": 1,
            "providers_supported": ["google", "outlook", "doctolib"],
            "providers_implemented": ["google", "outlook"],
        }
    p = provider.lower()
    try:
        if p == "google":
            from .providers.google import GoogleCalendarProvider
            return GoogleCalendarProvider().health_check()
        if p == "outlook":
            from .providers.outlook import OutlookCalendarProvider
            return OutlookCalendarProvider().health_check()
        if p == "doctolib":
            return {"ok": False, "provider": "doctolib", "status": "stub"}
        raise HTTPException(400, f"provider inconnu : {provider}")
    except RuntimeError as e:
        return {"ok": False, "provider": p, "error": str(e)}
