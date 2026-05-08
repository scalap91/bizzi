"""Provider Microsoft Graph (Outlook / O365) — REST direct, pas de MSAL.

Phase 1 : on accepte un access_token déjà obtenu (env MICROSOFT_GRAPH_ACCESS_TOKEN).
Phase 2 : intégrer MSAL client_credentials pour auto-refresh.

Doc API : https://learn.microsoft.com/en-us/graph/api/resources/event
Endpoints :
- POST   /me/events ou /users/{userId}/events
- PATCH  /me/events/{id}
- DELETE /me/events/{id}
- POST   /me/calendar/getSchedule          (free/busy)

`calendar_id` = identifiant utilisateur (UPN ou ID) : si "me" → /me/events,
sinon → /users/{calendar_id}/events.
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

GRAPH_API = "https://graph.microsoft.com/v1.0"


class OutlookCalendarProvider(CalendarProvider):
    name = "outlook"

    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or os.environ.get("MICROSOFT_GRAPH_ACCESS_TOKEN")
        if not self.access_token:
            raise RuntimeError("MICROSOFT_GRAPH_ACCESS_TOKEN manquant (env ou paramètre)")
        self._headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _to_iso(dt: datetime) -> str:
        # Graph attend ISO 8601 sans suffixe Z (timeZone séparé)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")

    @staticmethod
    def _events_path(calendar_id: str) -> str:
        if not calendar_id or calendar_id.lower() == "me":
            return "/me/events"
        return f"/users/{calendar_id}/events"

    def _event_body(self, req: EventRequest) -> dict:
        body: dict = {
            "subject": req.title,
            "start": {"dateTime": self._to_iso(req.start_at), "timeZone": req.timezone},
            "end":   {"dateTime": self._to_iso(req.end_at),   "timeZone": req.timezone},
        }
        if req.description:
            body["body"] = {"contentType": "HTML", "content": req.description}
        if req.location:
            body["location"] = {"displayName": req.location}
        if req.attendees:
            body["attendees"] = [
                {"emailAddress": {"address": e}, "type": "required"} for e in req.attendees
            ]
        if req.reminders_minutes:
            # Graph ne prend qu'un seul reminderMinutesBeforeStart : on prend le min.
            body["isReminderOn"] = True
            body["reminderMinutesBeforeStart"] = int(min(req.reminders_minutes))
        return body

    async def create_event(self, req: EventRequest) -> EventResult:
        url = GRAPH_API + self._events_path(req.calendar_id)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(url, json=self._event_body(req), headers=self._headers)
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
            status="confirmed",
            html_link=data.get("webLink"),
            ical_uid=data.get("iCalUId"),
            raw=data,
        )

    async def update_event(self, provider_event_id: str, req: EventRequest) -> EventResult:
        url = f"{GRAPH_API}{self._events_path(req.calendar_id)}/{provider_event_id}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.patch(url, json=self._event_body(req), headers=self._headers)
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
            status="confirmed",
            html_link=data.get("webLink"),
            raw=data,
        )

    async def cancel_event(
        self, provider_event_id: str, *, calendar_id: str = "me",
    ) -> bool:
        url = f"{GRAPH_API}{self._events_path(calendar_id)}/{provider_event_id}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.delete(url, headers=self._headers)
        except httpx.HTTPError:
            return False
        return r.status_code in (200, 204, 404)

    async def list_availability(
        self,
        calendar_id: str,
        from_at: datetime,
        to_at: datetime,
    ) -> list[AvailabilitySlot]:
        url = f"{GRAPH_API}/me/calendar/getSchedule"
        body = {
            "schedules": [calendar_id],
            "startTime": {"dateTime": self._to_iso(from_at), "timeZone": "UTC"},
            "endTime":   {"dateTime": self._to_iso(to_at),   "timeZone": "UTC"},
            "availabilityViewInterval": 30,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=body, headers=self._headers)
        except httpx.HTTPError:
            return []
        if r.status_code >= 400:
            return []
        data = r.json()
        out: list[AvailabilitySlot] = []
        for sched in data.get("value") or []:
            for item in sched.get("scheduleItems") or []:
                start = _parse_graph_dt(item.get("start"))
                end = _parse_graph_dt(item.get("end"))
                if start and end and item.get("status") in ("busy", "tentative", "oof"):
                    out.append(AvailabilitySlot(start_at=start, end_at=end, busy=True))
        return out

    def health_check(self) -> dict:
        try:
            r = httpx.get(
                f"{GRAPH_API}/me", headers=self._headers, timeout=5,
            )
            return {
                "ok": r.status_code == 200,
                "provider": self.name,
                "status_code": r.status_code,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": str(e)}


def _parse_graph_dt(blob: Optional[dict]) -> Optional[datetime]:
    if not blob:
        return None
    s = blob.get("dateTime")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
