"""Tests des body builders Google + Outlook (sans HTTP)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from comms.calendar.base import EventRequest


def _req(**overrides):
    base = dict(
        tenant_id=4, calendar_id="cal-1", title="RDV {{x}}",
        start_at=datetime(2026, 5, 8, 14, 0, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 5, 8, 14, 30, 0, tzinfo=timezone.utc),
        timezone="Europe/Paris",
        description="desc", location="loc",
        attendees=["a@x.fr", "b@x.fr"],
        send_invites=True,
        reminders_minutes=[1440, 60],
    )
    base.update(overrides)
    return EventRequest(**base)


def test_google_event_body(monkeypatch):
    os.environ["GOOGLE_CALENDAR_ACCESS_TOKEN"] = "test-token"
    from comms.calendar.providers.google import GoogleCalendarProvider
    p = GoogleCalendarProvider()
    body = p._event_body(_req())
    assert body["summary"] == "RDV {{x}}"  # pas de rendering — c'est le caller qui rend
    assert body["start"]["timeZone"] == "Europe/Paris"
    assert body["start"]["dateTime"].startswith("2026-05-08T14:00")
    assert body["description"] == "desc" and body["location"] == "loc"
    assert {a["email"] for a in body["attendees"]} == {"a@x.fr", "b@x.fr"}
    overrides = body["reminders"]["overrides"]
    assert {o["minutes"] for o in overrides} == {1440, 60}
    assert body["reminders"]["useDefault"] is False


def test_google_event_body_no_optional():
    os.environ["GOOGLE_CALENDAR_ACCESS_TOKEN"] = "test"
    from comms.calendar.providers.google import GoogleCalendarProvider
    p = GoogleCalendarProvider()
    body = p._event_body(_req(description=None, location=None, attendees=[], reminders_minutes=[]))
    assert "description" not in body and "location" not in body
    assert "attendees" not in body and "reminders" not in body


def test_outlook_event_body():
    os.environ["MICROSOFT_GRAPH_ACCESS_TOKEN"] = "test"
    from comms.calendar.providers.outlook import OutlookCalendarProvider
    p = OutlookCalendarProvider()
    body = p._event_body(_req())
    assert body["subject"] == "RDV {{x}}"
    assert body["start"]["timeZone"] == "Europe/Paris"
    # Graph attend dateTime SANS Z
    assert "Z" not in body["start"]["dateTime"]
    assert body["body"]["contentType"] == "HTML"
    assert body["location"]["displayName"] == "loc"
    assert {a["emailAddress"]["address"] for a in body["attendees"]} == {"a@x.fr", "b@x.fr"}
    # Graph ne prend qu'un seul reminder → min des minutes
    assert body["reminderMinutesBeforeStart"] == 60
    assert body["isReminderOn"] is True


def test_outlook_events_path():
    os.environ["MICROSOFT_GRAPH_ACCESS_TOKEN"] = "test"
    from comms.calendar.providers.outlook import OutlookCalendarProvider
    assert OutlookCalendarProvider._events_path("me") == "/me/events"
    assert OutlookCalendarProvider._events_path("MeT") == "/users/MeT/events"
    assert OutlookCalendarProvider._events_path("user@org.fr") == "/users/user@org.fr/events"


def test_google_health_no_token_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDAR_ACCESS_TOKEN", raising=False)
    from comms.calendar.providers.google import GoogleCalendarProvider
    import pytest
    with pytest.raises(RuntimeError):
        GoogleCalendarProvider()
