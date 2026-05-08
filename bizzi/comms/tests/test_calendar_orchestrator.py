"""Tests orchestrator calendar (mock event_log + provider + conflicts)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from comms.calendar import conflicts, orchestrator
from comms.calendar.base import (
    AvailabilitySlot, CalendarProvider, EventRequest, EventResult,
)


class FakeProvider(CalendarProvider):
    name = "google"

    def __init__(self, *, status: str = "confirmed", raise_on_create: bool = False):
        self.creates: list[EventRequest] = []
        self.cancels: list[str] = []
        self.updates: list[tuple[str, EventRequest]] = []
        self.busy_slots: list[AvailabilitySlot] = []
        self._status = status
        self._raise = raise_on_create

    async def create_event(self, req):
        self.creates.append(req)
        if self._raise:
            raise RuntimeError("api boom")
        return EventResult(
            provider_event_id="goog-1", status=self._status,
            html_link="https://cal/x", ical_uid="ical-1", raw={"ok": 1},
        )

    async def update_event(self, pid, req):
        self.updates.append((pid, req))
        return EventResult(provider_event_id=pid, status="confirmed", html_link="https://cal/x")

    async def cancel_event(self, pid, *, calendar_id="primary", **kw):
        self.cancels.append(pid)
        return True

    async def list_availability(self, calendar_id, from_at, to_at):
        return list(self.busy_slots)

    def health_check(self):
        return {"ok": True, "provider": self.name}


class FakeLog:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._id = 0

    def log_event(self, **kw):
        self._id += 1
        row = {"id": self._id, **kw}
        self.rows[self._id] = row
        return self._id

    def update_event(self, eid, **kw):
        if eid in self.rows:
            for k, v in kw.items():
                if v is not None:
                    self.rows[eid][k] = v

    def approve(self, eid, approved_by):
        if eid in self.rows:
            self.rows[eid]["status"] = "approved"

    def reject(self, eid, approved_by, reason=""):
        if eid in self.rows:
            self.rows[eid]["status"] = "rejected"

    def cancel(self, eid, cancelled_by, reason=""):
        if eid in self.rows:
            self.rows[eid]["status"] = "cancelled"

    def append_reminder_sent(self, eid, entry):
        self.rows[eid].setdefault("reminders_sent", []).append(entry)

    def get(self, eid):
        return self.rows.get(eid)

    def get_by_provider_id(self, p, pid): return None
    def list_events(self, *a, **k): return []
    def list_pending(self, *a, **k): return []
    def list_due_reminders(self, **k): return []
    def overlaps(self, *a, **k): return []


@pytest.fixture
def patched(monkeypatch):
    fake_log = FakeLog()
    fp = FakeProvider()
    monkeypatch.setattr(orchestrator, "event_log", fake_log)
    monkeypatch.setattr(conflicts, "event_log", fake_log)
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id",
                        lambda tid: "fake" if tid == 4 else None)
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: {
        "comms": {"calendar": {
            "enabled": True, "provider": "google", "shadow_mode": False,
            "organizer_email": "rdv@org.fr", "calendar_id": "rdv@org.fr",
            "default_duration_minutes": 30,
            "default_reminders_minutes": [60],
        }}
    })
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fp)
    return {"log": fake_log, "provider": fp}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── create_event ──────────────────────────────────────────────────

def test_unknown_tenant(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id", lambda tid: None)
    out = _run(orchestrator.create_event(
        tenant_id=999, title="x", start_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    assert "introuvable" in out["error"]


def test_disabled(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml",
                        lambda slug: {"comms": {"calendar": {"enabled": False}}})
    out = _run(orchestrator.create_event(
        tenant_id=4, title="x", start_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    assert "non activé" in out["error"]


def test_requires_title(patched):
    out = _run(orchestrator.create_event(
        tenant_id=4, start_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    assert "title requis" in out["error"]


def test_end_at_before_start_at(patched):
    now = datetime.now(timezone.utc)
    out = _run(orchestrator.create_event(
        tenant_id=4, title="x",
        start_at=now + timedelta(days=1),
        end_at=now,  # before
    ))
    assert "end_at doit être > start_at" in out["error"]


def test_default_end_at_from_duration(patched):
    """Si end_at omis, on utilise duration_minutes (yaml default = 30)."""
    start = datetime.now(timezone.utc) + timedelta(days=1)
    out = _run(orchestrator.create_event(
        tenant_id=4, title="RDV", start_at=start, force_live=True,
    ))
    assert out["status"] == "confirmed"
    req = patched["provider"].creates[0]
    assert req.end_at - req.start_at == timedelta(minutes=30)


def test_live_creates_with_reminders(patched):
    start = datetime.now(timezone.utc) + timedelta(days=2)
    out = _run(orchestrator.create_event(
        tenant_id=4, title="RDV", start_at=start, end_at=start + timedelta(hours=1),
        attendees=["client@x.fr"],
        reminders_minutes=[1440, 60],
        force_live=True,
    ))
    assert out["status"] == "confirmed" and out["mode"] == "live"
    assert out["provider_event_id"] == "goog-1"
    req = patched["provider"].creates[0]
    assert req.reminders_minutes == [1440, 60]
    assert req.attendees == ["client@x.fr"]
    assert req.calendar_id == "rdv@org.fr"


def test_shadow_does_not_call_provider(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: {
        "comms": {"calendar": {
            "enabled": True, "provider": "google", "shadow_mode": True,
            "organizer_email": "rdv@org.fr",
        }}
    })
    start = datetime.now(timezone.utc) + timedelta(days=1)
    out = _run(orchestrator.create_event(
        tenant_id=4, title="RDV", start_at=start, end_at=start + timedelta(minutes=30),
    ))
    assert out["mode"] == "shadow" and out["status"] == "pending"
    assert "preview" in out
    assert patched["provider"].creates == []


def test_internal_conflict_blocks(patched, monkeypatch):
    """Un overlap dans event_log.overlaps doit bloquer la création."""
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)
    monkeypatch.setattr(patched["log"], "overlaps",
                        lambda *a, **k: [{"id": 99, "title": "Existant", "start_at": start, "end_at": end}])
    out = _run(orchestrator.create_event(
        tenant_id=4, title="Nouveau", start_at=start, end_at=end, force_live=True,
    ))
    assert "conflit" in out["error"]
    assert out["conflicts"][0]["id"] == 99


def test_external_conflict_blocks(patched):
    """Un busy slot du provider doit bloquer si check_external_conflicts=True."""
    start = datetime.now(timezone.utc) + timedelta(days=1)
    patched["provider"].busy_slots = [AvailabilitySlot(start_at=start, end_at=start+timedelta(hours=1), busy=True)]
    out = _run(orchestrator.create_event(
        tenant_id=4, title="X", start_at=start, end_at=start+timedelta(hours=1),
        force_live=True, check_external_conflicts=True,
    ))
    assert "conflit externe" in out["error"]
    assert "external_busy" in out


def test_provider_create_raises(patched, monkeypatch):
    fp = FakeProvider(raise_on_create=True)
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fp)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    out = _run(orchestrator.create_event(
        tenant_id=4, title="X", start_at=start, end_at=start+timedelta(minutes=30),
        force_live=True,
    ))
    assert out["status"] == "failed" and "boom" in out["error"]
    assert patched["log"].rows[out["event_id"]]["status"] == "failed"


def test_template_render(patched, monkeypatch):
    from comms.calendar.templates import RenderedEvent
    monkeypatch.setattr(orchestrator.templates_mod, "render",
                        lambda slug, tpl, ctx: RenderedEvent(
                            title=f"Consult {ctx['name']}", description="d", location="loc",
                            duration_minutes=45, reminders_minutes=[60],
                        ))
    start = datetime.now(timezone.utc) + timedelta(days=1)
    out = _run(orchestrator.create_event(
        tenant_id=4, start_at=start,
        template_id="consult", template_context={"name": "Alice"},
        force_live=True,
    ))
    assert out["status"] == "confirmed"
    req = patched["provider"].creates[0]
    assert req.title == "Consult Alice"
    assert req.end_at - req.start_at == timedelta(minutes=45)
    assert req.reminders_minutes == [60]


# ── validate_pending ──────────────────────────────────────────────

def test_validate_approve(patched, monkeypatch):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    eid = patched["log"].log_event(
        tenant_id=4, agent_id=None, provider="google", provider_calendar_id="rdv@org.fr",
        title="RDV", start_at=start, end_at=start+timedelta(minutes=30),
        timezone="Europe/Paris", status="pending", shadow=True,
        reminders_minutes=[60], attendees=[],
    )
    out = _run(orchestrator.validate_pending(eid, "approve", approved_by="pascal"))
    assert out["status"] == "confirmed" and out["mode"] == "live"
    assert patched["provider"].creates[-1].title == "RDV"


def test_validate_reject(patched):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    eid = patched["log"].log_event(
        tenant_id=4, agent_id=None, provider="google", provider_calendar_id="rdv@org.fr",
        title="RDV", start_at=start, end_at=start+timedelta(minutes=30),
        timezone="Europe/Paris", status="pending", shadow=True,
        reminders_minutes=[], attendees=[],
    )
    out = _run(orchestrator.validate_pending(eid, "reject", approved_by="pascal"))
    assert out["status"] == "rejected"
    assert patched["log"].rows[eid]["status"] == "rejected"


def test_validate_unknown_decision(patched):
    out = _run(orchestrator.validate_pending(1, "lol", approved_by="pascal"))
    assert "décision invalide" in out["error"]


def test_validate_not_pending(patched):
    eid = patched["log"].log_event(
        tenant_id=4, provider="google", title="x",
        start_at=datetime.now(timezone.utc), end_at=datetime.now(timezone.utc)+timedelta(hours=1),
        status="confirmed", shadow=False,
    )
    out = _run(orchestrator.validate_pending(eid, "approve", approved_by="pascal"))
    assert "pas pending" in out["error"]


# ── cancel ───────────────────────────────────────────────────────

def test_cancel_calls_provider_when_provider_id(patched):
    eid = patched["log"].log_event(
        tenant_id=4, provider="google", provider_event_id="goog-1",
        provider_calendar_id="rdv@org.fr",
        title="x", start_at=datetime.now(timezone.utc), end_at=datetime.now(timezone.utc)+timedelta(hours=1),
        status="confirmed", shadow=False,
    )
    out = _run(orchestrator.cancel_event(eid, cancelled_by="pascal"))
    assert out["status"] == "cancelled"
    assert "goog-1" in patched["provider"].cancels


def test_cancel_already_cancelled(patched):
    eid = patched["log"].log_event(
        tenant_id=4, provider="google", title="x",
        start_at=datetime.now(timezone.utc), end_at=datetime.now(timezone.utc)+timedelta(hours=1),
        status="cancelled", shadow=False,
    )
    out = _run(orchestrator.cancel_event(eid, cancelled_by="pascal"))
    assert "déjà au statut" in out["error"]


# ── availability ──────────────────────────────────────────────────

def test_availability_internal_returns_active_events(patched, monkeypatch):
    rows = [
        {"start_at": datetime.now(timezone.utc), "end_at": datetime.now(timezone.utc)+timedelta(hours=1),
         "status": "confirmed", "title": "X"},
        {"start_at": datetime.now(timezone.utc), "end_at": datetime.now(timezone.utc)+timedelta(hours=1),
         "status": "rejected", "title": "Y"},  # filtré
    ]
    monkeypatch.setattr(patched["log"], "list_events", lambda *a, **k: rows)
    out = _run(orchestrator.list_availability(
        tenant_id=4,
        from_at=datetime.now(timezone.utc),
        to_at=datetime.now(timezone.utc)+timedelta(days=7),
    ))
    assert out["source"] == "internal"
    titles = [b["title"] for b in out["busy"]]
    assert "X" in titles and "Y" not in titles


def test_availability_external_calls_provider(patched):
    start = datetime.now(timezone.utc)
    patched["provider"].busy_slots = [AvailabilitySlot(start_at=start, end_at=start+timedelta(hours=1), busy=True)]
    out = _run(orchestrator.list_availability(
        tenant_id=4, from_at=start, to_at=start+timedelta(days=7),
        use_external=True,
    ))
    assert out["source"] == "provider:google"
    assert len(out["busy"]) == 1
