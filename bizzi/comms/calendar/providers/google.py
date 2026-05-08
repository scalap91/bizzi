"""Provider Google Calendar (REST direct, pas de google-auth).

Phase 1 : on accepte un access_token déjà obtenu (env GOOGLE_CALENDAR_ACCESS_TOKEN)
ou fourni au constructeur. La gestion du flow OAuth2 service account / refresh
est laissée à l'opérateur (Phase 2 : intégrer google-auth pour auto-refresh).

Doc API : https://developers.google.com/calendar/api/v3/reference

Endpoints utilisés :
- POST   /calendar/v3/calendars/{calendarId}/events                     (create)
- PATCH  /calendar/v3/calendars/{calendarId}/events/{eventId}           (update)
- DELETE /calendar/v3/calendars/{calendarId}/events/{eventId}           (cancel)
- POST   /calendar/v3/freeBusy                                          (availability)
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import httpx

from ..base import (
    AvailabilitySlot,
    CalendarProvider,
    EventRequest,
    EventResult,
)

GOOGLE_API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarProvider(CalendarProvider):
    name = "google"

    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or os.environ.get("GOOGLE_CALENDAR_ACCESS_TOKEN")
        if not self.access_token:
            raise RuntimeError("GOOGLE_CALENDAR_ACCESS_TOKEN manquant (env ou paramètre)")
        self._headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _to_iso(dt: datetime) -> str:
        # Google attend RFC3339 ; isoformat suffit si tz-aware, sinon on assume UTC.
        if dt.tzinfo is None:
            return dt.isoformat() + "Z"
        return dt.isoformat()

    def _event_body(self, req: EventRequest) -> dict:
        body: dict = {
            "summary": req.title,
            "start": {"dateTime": self._to_iso(req.start_at), "timeZone": req.timezone},
            "end":   {"dateTime": self._to_iso(req.end_at),   "timeZone": req.timezone},
        }
        if req.description:
            body["description"] = req.description
        if req.location:
            body["location"] = req.location
        if req.attendees:
            body["attendees"] = [{"email": e} for e in req.attendees]
        if req.reminders_minutes:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": int(m)} for m in req.reminders_minutes
                ],
            }
        return body

    async def create_event(self, req: EventRequest) -> EventResult:
        url = f"{GOOGLE_API}/calendars/{req.calendar_id}/events"
        params = {"sendUpdates": "all" if req.send_invites else "none"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    url, json=self._event_body(req), headers=self._headers, params=params,
                )
        except httpx.HTTPError as e:
            return EventResult(provider_event_id="", status="failed", error=f"HTTP error: {e}")

        if r.status_code >= 400:
            return EventResult(
                provider_event_id="", status="failed",
                error=f"HTTP {r.status_code}: {r.text[:500]}",
            )
        data = r.json()
        return EventResult(
            provider_event_id=str(data.get("id") or ""),
            status="confirmed" if data.get("status") == "confirmed" else "tentative",
            html_link=data.get("htmlLink"),
            ical_uid=data.get("iCalUID"),
            raw=data,
        )

    async def update_event(self, provider_event_id: str, req: EventRequest) -> EventResult:
        url = f"{GOOGLE_API}/calendars/{req.calendar_id}/events/{provider_event_id}"
        params = {"sendUpdates": "all" if req.send_invites else "none"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.patch(
                    url, json=self._event_body(req), headers=self._headers, params=params,
                )
        except httpx.HTTPError as e:
            return EventResult(provider_event_id=provider_event_id, status="failed", error=str(e))
        if r.status_code >= 400:
            return EventResult(
                provider_event_id=provider_event_id, status="failed",
                error=f"HTTP {r.status_code}: {r.text[:500]}",
            )
        data = r.json()
        return EventResult(
            provider_event_id=str(data.get("id") or provider_event_id),
            status="confirmed" if data.get("status") == "confirmed" else "tentative",
            html_link=data.get("htmlLink"),
            raw=data,
        )

    async def cancel_event(self, provider_event_id: str, *, calendar_id: str = "primary", send_updates: bool = True) -> bool:
        url = f"{GOOGLE_API}/calendars/{calendar_id}/events/{provider_event_id}"
        params = {"sendUpdates": "all" if send_updates else "none"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.delete(url, headers=self._headers, params=params)
        except httpx.HTTPError:
            return False
        return r.status_code in (200, 204, 410)  # 410 = déjà supprimé

    async def list_availability(
        self,
        calendar_id: str,
        from_at: datetime,
        to_at: datetime,
    ) -> list[AvailabilitySlot]:
        body = {
            "timeMin": self._to_iso(from_at),
            "timeMax": self._to_iso(to_at),
            "items": [{"id": calendar_id}],
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{GOOGLE_API}/freeBusy", json=body, headers=self._headers,
                )
        except httpx.HTTPError:
            return []
        if r.status_code >= 400:
            return []
        data = r.json()
        busy_blocks = ((data.get("calendars") or {}).get(calendar_id) or {}).get("busy") or []
        out: list[AvailabilitySlot] = []
        for blk in busy_blocks:
            start = _parse_iso(blk.get("start"))
            end = _parse_iso(blk.get("end"))
            if start and end:
                out.append(AvailabilitySlot(start_at=start, end_at=end, busy=True))
        return out

    def health_check(self) -> dict:
        try:
            r = httpx.get(
                f"{GOOGLE_API}/users/me/calendarList",
                headers=self._headers, timeout=5, params={"maxResults": 1},
            )
            return {
                "ok": r.status_code == 200,
                "provider": self.name,
                "status_code": r.status_code,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": str(e)}


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
