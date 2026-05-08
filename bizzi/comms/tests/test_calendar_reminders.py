"""Tests reminders calendar (mock event_log + comms.sms + comms.mail)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from comms.calendar import reminders


class FakeLog:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.appended: list[tuple[int, dict]] = []

    def list_due_reminders(self, **k):
        # On retourne un snapshot des events futurs avec reminders_minutes non-vide
        out = []
        for ev in self.rows.values():
            if (
                ev.get("status") in ("created", "confirmed")
                and ev.get("reminders_minutes")
            ):
                out.append(ev)
        return out

    def append_reminder_sent(self, eid, entry):
        self.appended.append((eid, entry))
        if eid in self.rows:
            self.rows[eid].setdefault("reminders_sent", []).append(entry)


@pytest.fixture
def patched(monkeypatch):
    fake_log = FakeLog()
    monkeypatch.setattr(reminders, "event_log", fake_log)
    monkeypatch.setattr(reminders, "_tenant_slug_from_id",
                        lambda tid: "fake" if tid == 4 else None)
    monkeypatch.setattr(reminders._template, "load_tenant_yaml",
                        lambda slug, **kw: {"comms": {"calendar": {
                            "reminder_channels": ["mail", "sms"],
                        }}})
    return {"log": fake_log}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _add_event(log, **kw):
    eid = len(log.rows) + 1
    base = {
        "id": eid, "tenant_id": 4, "status": "confirmed",
        "title": "RDV test", "location": "Cabinet",
        "attendees": ["client@x.fr"],
        "metadata": {"attendee_phones": ["+33611111111"]},
        "reminders_minutes": [60],
        "reminders_sent": [],
    }
    base.update(kw)
    log.rows[eid] = base
    return eid


def test_due_in_window_sends_both(patched, monkeypatch):
    """Event H-1 dans la fenêtre [55..60] min → envoi SMS + mail."""
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    eid = _add_event(patched["log"], start_at=now + timedelta(minutes=58))

    sms_calls, mail_calls = [], []

    async def fake_sms(**kw):
        sms_calls.append(kw); return {"sms_id": 100, "status": "sent"}

    async def fake_mail(**kw):
        mail_calls.append(kw); return {"mail_id": 200, "status": "sent"}

    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(reminders.run_due_reminders(tenant_id=4, lookahead_minutes=5, now=now))
    assert out["scanned"] == 1
    assert len(out["sent"]) == 2  # mail + sms
    assert {e["channel"] for e in out["sent"]} == {"sms", "mail"}
    # ref_id récupéré
    refs = {e["channel"]: e["ref_id"] for e in out["sent"]}
    assert refs["sms"] == 100 and refs["mail"] == 200
    # Trace écrite
    assert len(patched["log"].appended) == 2


def test_already_sent_not_resent(patched, monkeypatch):
    """Si reminders_sent contient déjà l'entrée, on ne renvoie pas."""
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    eid = _add_event(patched["log"],
                     start_at=now + timedelta(minutes=58),
                     reminders_sent=[
                         {"minutes_before": 60, "channel": "sms", "ok": True},
                         {"minutes_before": 60, "channel": "mail", "ok": True},
                     ])

    async def fake_sms(**kw): raise AssertionError("should not be called")
    async def fake_mail(**kw): raise AssertionError("should not be called")
    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(reminders.run_due_reminders(tenant_id=4, lookahead_minutes=5, now=now))
    assert out["sent"] == [] and out["skipped"] == []


def test_outside_window_not_sent(patched, monkeypatch):
    """start_at est dans 2h, reminder de 60min n'est pas dû."""
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    _add_event(patched["log"], start_at=now + timedelta(hours=2))

    async def fake_sms(**kw): raise AssertionError("not due")
    async def fake_mail(**kw): raise AssertionError("not due")
    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(reminders.run_due_reminders(tenant_id=4, lookahead_minutes=5, now=now))
    assert out["sent"] == []


def test_sms_no_phone_marks_skipped(patched, monkeypatch):
    """Pas de numéro E.164 → skip SMS, mail OK."""
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    _add_event(patched["log"],
               start_at=now + timedelta(minutes=58),
               metadata={},
               attendees=["client@x.fr"])  # pas de numéro E.164

    async def fake_sms(**kw): return {"sms_id": 1, "status": "sent"}

    async def fake_mail(**kw): return {"mail_id": 1, "status": "sent"}

    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(reminders.run_due_reminders(tenant_id=4, lookahead_minutes=5, now=now))
    sms_skipped = [s for s in out["skipped"] if s["channel"] == "sms"]
    sent_mail = [s for s in out["sent"] if s["channel"] == "mail"]
    assert sms_skipped and "no SMS recipient" in sms_skipped[0]["error"]
    assert sent_mail


def test_mail_no_email_marks_skipped(patched, monkeypatch):
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    _add_event(patched["log"],
               start_at=now + timedelta(minutes=58),
               attendees=[],
               metadata={"attendee_phones": ["+33611"]})

    async def fake_sms(**kw): return {"sms_id": 1, "status": "sent"}
    async def fake_mail(**kw): raise AssertionError("not called")
    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(reminders.run_due_reminders(tenant_id=4, lookahead_minutes=5, now=now))
    skipped_mail = [s for s in out["skipped"] if s["channel"] == "mail"]
    assert skipped_mail


def test_sms_orchestrator_error_marks_skipped(patched, monkeypatch):
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    _add_event(patched["log"], start_at=now + timedelta(minutes=58))

    async def fake_sms(**kw): return {"error": "rate_limit"}
    async def fake_mail(**kw): return {"mail_id": 1, "status": "sent"}
    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(reminders.run_due_reminders(tenant_id=4, lookahead_minutes=5, now=now))
    sms_skipped = [s for s in out["skipped"] if s["channel"] == "sms"]
    assert sms_skipped and "rate_limit" in sms_skipped[0]["error"]
