"""Provider Doctolib — Phase 0 stub.

Doctolib n'expose pas d'API publique généralisée. Phase 1 : intégration
spécifique (partner program ou scraping autorisé sous conditions client).
"""
from __future__ import annotations

from datetime import datetime

from ..base import (
    AvailabilitySlot,
    CalendarProvider,
    EventRequest,
    EventResult,
)


class DoctolibCalendarProvider(CalendarProvider):
    name = "doctolib"

    async def create_event(self, req: EventRequest) -> EventResult:
        raise NotImplementedError("DoctolibCalendarProvider.create_event — Phase 1")

    async def update_event(self, provider_event_id: str, req: EventRequest) -> EventResult:
        raise NotImplementedError("DoctolibCalendarProvider.update_event — Phase 1")

    async def cancel_event(self, provider_event_id: str) -> bool:
        raise NotImplementedError("DoctolibCalendarProvider.cancel_event — Phase 1")

    async def list_availability(
        self, calendar_id: str, from_at: datetime, to_at: datetime
    ) -> list[AvailabilitySlot]:
        raise NotImplementedError("DoctolibCalendarProvider.list_availability — Phase 1")

    def health_check(self) -> dict:
        return {"ok": False, "provider": self.name, "status": "stub"}
