"""Interface abstraite des providers calendar. Pattern miroir de phone/provider.py."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class EventRequest:
    tenant_id: int
    calendar_id: str          # provider-side (mailbox, calendarId Google, etc.)
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str = "Europe/Paris"
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: list[str] = field(default_factory=list)   # emails
    organizer_email: Optional[str] = None
    send_invites: bool = True
    reminders_minutes: list[int] = field(default_factory=lambda: [1440, 60])  # J-1 + 1h
    metadata: dict = field(default_factory=dict)


@dataclass
class EventResult:
    provider_event_id: str
    status: str              # confirmed | tentative | cancelled | failed
    html_link: Optional[str] = None
    ical_uid: Optional[str] = None
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class AvailabilitySlot:
    start_at: datetime
    end_at: datetime
    busy: bool = False


class CalendarProvider(ABC):
    name: str  # 'google' | 'outlook' | 'doctolib' | 'calendly'

    @abstractmethod
    async def create_event(self, req: EventRequest) -> EventResult: ...

    @abstractmethod
    async def update_event(self, provider_event_id: str, req: EventRequest) -> EventResult: ...

    @abstractmethod
    async def cancel_event(self, provider_event_id: str) -> bool: ...

    @abstractmethod
    async def list_availability(
        self,
        calendar_id: str,
        from_at: datetime,
        to_at: datetime,
    ) -> list[AvailabilitySlot]: ...

    @abstractmethod
    def health_check(self) -> dict: ...
