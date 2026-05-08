"""Orchestrator calendar : create / update / cancel / availability.

Pattern miroir de bizzi.comms.{sms,mail}.orchestrator.

Responsabilités :
- Charger config tenant (yaml `comms.calendar`)
- Vérifier `enabled`
- Rendre template (title/description/location)
- Vérifier conflits internes (DB)
- Optionnel : conflits externes (provider freebusy) si `check_external=True`
- Shadow mode → log status='pending'
- Live      → provider.create_event + log update

Pas de budget mensuel (event volumineux peu probable, et coût provider = 0).
Pas de rate-limit dédié (les conflits couvrent l'essentiel).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import _db, _template
from . import conflicts, event_log, templates as templates_mod
from .base import (
    AvailabilitySlot,
    CalendarProvider,
    EventRequest,
    EventResult,
)

logger = logging.getLogger("comms.calendar.orchestrator")


# ── Tenant resolution ─────────────────────────────────────────────

def _tenant_slug_from_id(tenant_id: int) -> Optional[str]:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _load_tenant_yaml(tenant_slug: str) -> dict:
    try:
        return _template.load_tenant_yaml(tenant_slug)
    except FileNotFoundError:
        return {}


# ── Provider factory ──────────────────────────────────────────────

def build_provider(cal_cfg: dict) -> CalendarProvider:
    name = (cal_cfg.get("provider") or "google").lower()
    if name == "google":
        from .providers.google import GoogleCalendarProvider
        return GoogleCalendarProvider(access_token=cal_cfg.get("google_access_token"))
    if name == "outlook":
        from .providers.outlook import OutlookCalendarProvider
        return OutlookCalendarProvider(access_token=cal_cfg.get("microsoft_graph_access_token"))
    if name == "doctolib":
        from .providers.doctolib import DoctolibCalendarProvider
        return DoctolibCalendarProvider()  # stub Phase 1
    raise ValueError(f"provider calendar inconnu : {name}")


# ── Orchestration ─────────────────────────────────────────────────

async def create_event(
    *,
    tenant_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    start_at: datetime,
    end_at: Optional[datetime] = None,
    duration_minutes: Optional[int] = None,
    timezone_name: str = "Europe/Paris",
    organizer_email: Optional[str] = None,
    attendees: Optional[list[str]] = None,
    template_id: Optional[str] = None,
    template_context: Optional[dict] = None,
    reminders_minutes: Optional[list[int]] = None,
    calendar_id: Optional[str] = None,
    send_invites: bool = True,
    agent_id: Optional[int] = None,
    use_case: Optional[str] = None,
    force_live: bool = False,
    check_external_conflicts: bool = False,
    created_by: Optional[str] = None,
) -> dict:
    """Crée un événement calendrier (shadow par défaut).

    Soit (title, start_at, end_at|duration_minutes), soit (template_id, start_at).
    Retour : {event_id, status, mode, error?, ...}.
    """
    slug = _tenant_slug_from_id(tenant_id)
    if not slug:
        return {"error": f"tenant_id {tenant_id} introuvable"}

    tenant_cfg = _load_tenant_yaml(slug)
    cal_cfg = ((tenant_cfg.get("comms") or {}).get("calendar") or {})
    if not cal_cfg.get("enabled"):
        return {"error": "comms.calendar non activé pour ce tenant (yaml: comms.calendar.enabled)"}

    # Render template (title/desc/loc/duration/reminders)
    if template_id:
        try:
            rendered = templates_mod.render(slug, template_id, template_context or {})
        except KeyError as e:
            return {"error": str(e)}
        except ValueError as e:
            return {"error": f"template render: {e}"}
        title = title or rendered.title
        description = description if description is not None else rendered.description
        location = location if location is not None else rendered.location
        if duration_minutes is None:
            duration_minutes = rendered.duration_minutes
        if reminders_minutes is None and rendered.reminders_minutes:
            reminders_minutes = rendered.reminders_minutes

    if not title:
        return {"error": "title requis (ou via template)"}

    # Compute end_at
    if end_at is None:
        if duration_minutes is None:
            duration_minutes = int(cal_cfg.get("default_duration_minutes", 30))
        end_at = start_at + timedelta(minutes=int(duration_minutes))
    if end_at <= start_at:
        return {"error": "end_at doit être > start_at"}

    # Defaults yaml
    organizer_email = organizer_email or cal_cfg.get("organizer_email")
    if calendar_id is None:
        calendar_id = cal_cfg.get("calendar_id") or organizer_email or "primary"
    if reminders_minutes is None:
        reminders_minutes = list(cal_cfg.get("default_reminders_minutes") or [1440, 60])

    # Build provider (peut échouer si access_token absent)
    try:
        provider = build_provider(cal_cfg)
    except Exception as e:  # noqa: BLE001
        return {"error": f"provider init: {e}"}

    # Vérifier conflits internes
    rep = conflicts.check_internal(
        tenant_id, start_at, end_at, organizer_email=organizer_email,
    )
    if rep.has_conflict:
        return {
            "error": f"conflit avec {len(rep.overlapping_events)} événement(s)",
            "conflicts": rep.overlapping_events,
        }

    # Conflits externes (provider freebusy) — opt-in (HTTP call coûteuse)
    if check_external_conflicts:
        try:
            busy = await provider.list_availability(calendar_id, start_at, end_at)
        except Exception as e:  # noqa: BLE001
            logger.warning("calendar: freebusy provider erreur: %s", e)
            busy = []
        if busy:
            return {
                "error": f"conflit externe ({provider.name}): {len(busy)} créneau(x) occupé(s)",
                "external_busy": [
                    {"start_at": s.start_at.isoformat(), "end_at": s.end_at.isoformat()}
                    for s in busy
                ],
            }

    shadow_mode = bool(cal_cfg.get("shadow_mode", True)) and not force_live

    # Log d'abord
    event_id = event_log.log_event(
        tenant_id=tenant_id,
        agent_id=agent_id,
        provider=provider.name,
        provider_calendar_id=calendar_id,
        title=title,
        description=description,
        location=location,
        start_at=start_at,
        end_at=end_at,
        timezone_name=timezone_name,
        organizer_email=organizer_email,
        attendees=attendees or [],
        template_id=template_id,
        template_context=template_context or {},
        reminders_minutes=reminders_minutes or [],
        status="pending" if shadow_mode else "approved",
        shadow=shadow_mode,
        metadata={"use_case": use_case} if use_case else {},
        created_by=created_by,
    )

    if shadow_mode:
        return {
            "event_id": event_id,
            "status": "pending",
            "mode": "shadow",
            "preview": {
                "title": title,
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "attendees": attendees or [],
                "calendar_id": calendar_id,
                "provider": provider.name,
            },
        }

    # Live → create_event provider
    return await _go_live(
        event_id=event_id, provider=provider,
        req=_build_event_request(
            tenant_id=tenant_id, calendar_id=calendar_id, title=title,
            start_at=start_at, end_at=end_at, timezone=timezone_name,
            description=description, location=location,
            attendees=attendees or [], organizer_email=organizer_email,
            send_invites=send_invites,
            reminders_minutes=reminders_minutes or [],
            metadata={"use_case": use_case} if use_case else {},
        ),
    )


def _build_event_request(**kw) -> EventRequest:
    return EventRequest(**kw)


async def _go_live(*, event_id: int, provider: CalendarProvider, req: EventRequest) -> dict:
    try:
        result: EventResult = await provider.create_event(req)
    except Exception as e:  # noqa: BLE001
        event_log.update_event(event_id, status="failed", error=str(e))
        return {"event_id": event_id, "status": "failed", "error": str(e)}

    if result.status == "failed":
        event_log.update_event(event_id, status="failed", error=result.error or "create failed")
        return {"event_id": event_id, "status": "failed", "error": result.error}

    event_log.update_event(
        event_id,
        status="created" if result.status != "confirmed" else "confirmed",
        provider_event_id=result.provider_event_id,
        html_link=result.html_link,
        ical_uid=result.ical_uid,
        metadata_patch={"provider_raw": _trim_dict(result.raw)},
    )
    return {
        "event_id": event_id,
        "status": result.status,
        "mode": "live",
        "provider_event_id": result.provider_event_id,
        "html_link": result.html_link,
    }


def _trim_dict(d: Optional[dict], max_keys: int = 30, max_str: int = 500) -> dict:
    if not d:
        return {}
    out: dict = {}
    for i, (k, v) in enumerate(d.items()):
        if i >= max_keys:
            break
        if isinstance(v, str) and len(v) > max_str:
            v = v[:max_str] + "…"
        out[str(k)] = v
    return out


# ── Validation shadow → live ──────────────────────────────────────

async def validate_pending(event_id: int, decision: str, approved_by: str) -> dict:
    if decision not in ("approve", "reject"):
        return {"error": f"décision invalide : {decision}"}
    rec = event_log.get(event_id)
    if not rec:
        return {"error": f"event_id {event_id} introuvable"}
    if rec.get("status") != "pending":
        return {"error": f"event_id {event_id} statut={rec.get('status')} (pas pending)"}

    if decision == "reject":
        event_log.reject(event_id, approved_by=approved_by, reason="rejected by reviewer")
        return {"event_id": event_id, "status": "rejected"}

    # approve → relance live
    event_log.approve(event_id, approved_by=approved_by)
    cal_cfg = ((_load_tenant_yaml(_tenant_slug_from_id(rec["tenant_id"]) or "") or {}).get("comms") or {}).get("calendar") or {}
    try:
        provider = build_provider(cal_cfg)
    except Exception as e:  # noqa: BLE001
        event_log.update_event(event_id, status="failed", error=f"provider init: {e}")
        return {"event_id": event_id, "status": "failed", "error": str(e)}

    req = EventRequest(
        tenant_id=rec["tenant_id"],
        calendar_id=rec["provider_calendar_id"] or "primary",
        title=rec["title"],
        start_at=rec["start_at"],
        end_at=rec["end_at"],
        timezone=rec.get("timezone") or "Europe/Paris",
        description=rec.get("description"),
        location=rec.get("location"),
        attendees=list(rec.get("attendees") or []),
        organizer_email=rec.get("organizer_email"),
        send_invites=True,
        reminders_minutes=list(rec.get("reminders_minutes") or []),
        metadata=rec.get("metadata") or {},
    )
    return await _go_live(event_id=event_id, provider=provider, req=req)


# ── Cancel ───────────────────────────────────────────────────────

async def cancel_event(event_id: int, cancelled_by: str, reason: str = "") -> dict:
    rec = event_log.get(event_id)
    if not rec:
        return {"error": f"event_id {event_id} introuvable"}
    if rec.get("status") in ("cancelled", "rejected", "failed"):
        return {"error": f"event_id {event_id} déjà au statut {rec.get('status')}"}

    # Si event créé chez le provider, on tente l'annulation
    provider_event_id = rec.get("provider_event_id")
    if provider_event_id:
        cal_cfg = ((_load_tenant_yaml(_tenant_slug_from_id(rec["tenant_id"]) or "") or {}).get("comms") or {}).get("calendar") or {}
        try:
            provider = build_provider(cal_cfg)
            ok = await provider.cancel_event(
                provider_event_id, calendar_id=rec.get("provider_calendar_id") or "primary",
            )
            if not ok:
                logger.warning("calendar: cancel provider KO pour %s/%s",
                               rec.get("provider"), provider_event_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("calendar: cancel provider exc: %s", e)

    event_log.cancel(event_id, cancelled_by=cancelled_by, reason=reason)
    return {"event_id": event_id, "status": "cancelled"}


# ── Availability ─────────────────────────────────────────────────

async def list_availability(
    *,
    tenant_id: int,
    from_at: datetime,
    to_at: datetime,
    calendar_id: Optional[str] = None,
    use_external: bool = False,
) -> dict:
    """Renvoie les créneaux occupés. Par défaut : DB interne. Sinon : provider freebusy."""
    if not use_external:
        rows = event_log.list_events(
            tenant_id, from_at=from_at, to_at=to_at, status=None, limit=500,
        )
        busy = [
            {"start_at": r["start_at"].isoformat(), "end_at": r["end_at"].isoformat(),
             "status": r["status"], "title": r["title"]}
            for r in rows
            if r["status"] in ("approved", "created", "confirmed")
        ]
        return {"source": "internal", "busy": busy}

    slug = _tenant_slug_from_id(tenant_id)
    if not slug:
        return {"error": f"tenant_id {tenant_id} introuvable"}
    cal_cfg = (((_load_tenant_yaml(slug)) or {}).get("comms") or {}).get("calendar") or {}
    try:
        provider = build_provider(cal_cfg)
    except Exception as e:  # noqa: BLE001
        return {"error": f"provider init: {e}"}
    cid = calendar_id or cal_cfg.get("calendar_id") or cal_cfg.get("organizer_email") or "primary"
    slots = await provider.list_availability(cid, from_at, to_at)
    return {
        "source": f"provider:{provider.name}",
        "calendar_id": cid,
        "busy": [
            {"start_at": s.start_at.isoformat(), "end_at": s.end_at.isoformat(), "busy": s.busy}
            for s in slots
        ],
    }
