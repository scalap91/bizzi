"""Tests handler inbound (mock log + qualifier + sms/mail orchestrators)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from comms.inbound import handler
from comms.inbound import qualifier as qmod
from comms.inbound.qualifier import Qualification


class _FakeLog:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._id = 0
        self._by_pid: dict[tuple, dict] = {}

    def log_call(self, **kw):
        self._id += 1
        row = {"id": self._id, "actions": [], **kw}
        self.rows[self._id] = row
        if kw.get("provider") and kw.get("provider_call_id"):
            self._by_pid[(kw["provider"], kw["provider_call_id"])] = row
        return self._id

    def update_call(self, call_id, **kw):
        if call_id in self.rows:
            for k, v in kw.items():
                if v is not None:
                    self.rows[call_id][k] = v

    def update_qualification(self, call_id, **kw):
        if call_id in self.rows:
            self.rows[call_id].update({k: v for k, v in kw.items() if v is not None})

    def append_action(self, call_id, action):
        if call_id in self.rows:
            self.rows[call_id].setdefault("actions", []).append(action)

    def get(self, call_id):
        return self.rows.get(call_id)

    def get_by_provider_id(self, provider, pid):
        return self._by_pid.get((provider, pid))


@pytest.fixture
def patched(monkeypatch):
    fake_log = _FakeLog()
    monkeypatch.setattr(handler, "inbound_log", fake_log)
    monkeypatch.setattr(handler, "_tenant_id_from_to_phone", lambda p: 4 if p == "+33186" else None)
    monkeypatch.setattr(handler, "_tenant_slug_from_id", lambda tid: "fake_tenant" if tid == 4 else None)
    monkeypatch.setattr(handler, "load_inbound_config", lambda slug: {
        "auto_sms_confirm": True, "auto_mail_summary": True,
        "admin_email": "admin@org.fr",
        "qualifier": {"enabled": True},
    })
    # Stub qualifier_mod.qualify pour ne pas appeler Ollama
    async def fake_qualify(transcript, **kw):
        return Qualification(
            intent="renseignement", urgency=0, suggested_action="sms_confirm",
            extracted={"nom": "Alice"}, confidence=0.8, requires_human=False,
            summary="Alice demande les horaires.",
        )
    monkeypatch.setattr(handler.qualifier_mod, "qualify", fake_qualify)
    return {"log": fake_log}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Routing dispatch ──────────────────────────────────────────────

def test_ignored_event_noop(patched):
    out = _run(handler.handle_event("vapi", {"event_type": "ignored", "raw_type": "speech-update"}))
    assert out["ok"] is True and out["noop"] is True


def test_assistant_request(patched):
    out = _run(handler.handle_event("vapi", {"event_type": "assistant_request"}))
    assert out["ok"] is True
    assert out["action"] == "use_static_assistant"


def test_status_update_creates_row(patched):
    out = _run(handler.handle_event("vapi", {
        "event_type": "status_update", "provider_call_id": "c1",
        "from_phone": "+33611", "to_phone": "+33186",
        "status": "received",
    }))
    assert out["ok"] is True and out["action"] == "logged"
    cid = out["call_id"]
    row = patched["log"].rows[cid]
    assert row["tenant_id"] == 4
    assert row["status"] == "received"
    assert row["provider_call_id"] == "c1"


def test_status_update_updates_existing(patched):
    cid = patched["log"].log_call(
        tenant_id=4, provider="vapi", provider_call_id="c1",
        from_phone="+33611", to_phone="+33186", status="received",
    )
    out = _run(handler.handle_event("vapi", {
        "event_type": "status_update", "provider_call_id": "c1",
        "from_phone": "+33611", "to_phone": "+33186", "status": "in_progress",
    }))
    assert out["call_id"] == cid
    assert out["action"] == "status_updated"
    assert patched["log"].rows[cid]["status"] == "in_progress"


def test_status_update_unknown_tenant(patched):
    out = _run(handler.handle_event("vapi", {
        "event_type": "status_update", "provider_call_id": "c1",
        "from_phone": "+33611", "to_phone": "+33000",  # n'est pas mappé
        "status": "received",
    }))
    assert out["ok"] is False
    assert "tenant" in out["error"]


# ── End-of-call ───────────────────────────────────────────────────

def test_end_of_call_full_flow_sms_confirm(patched, monkeypatch):
    """sms_confirm + mail_summary tous les deux exécutés."""
    sms_calls = []
    mail_calls = []

    async def fake_sms(**kw):
        sms_calls.append(kw)
        return {"sms_id": 1, "status": "sent"}

    async def fake_mail(**kw):
        mail_calls.append(kw)
        return {"mail_id": 1, "status": "sent"}

    # Patch les imports lazy dans _send_caller_sms / _send_admin_summary
    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(handler.handle_event("vapi", {
        "event_type": "end_of_call",
        "provider_call_id": "c2",
        "from_phone": "+33611111111", "to_phone": "+33186",
        "status": "completed",
        "transcript": [{"role": "user", "text": "horaires d'ouverture ?"}],
        "summary": "horaires", "duration_seconds": 30, "cost_eur": 0.05,
        "ended_at": datetime.now(timezone.utc),
    }))
    assert out["ok"] is True and out["action"] == "finalized"
    cid = out["call_id"]
    row = patched["log"].rows[cid]
    assert row["status"] == "completed"
    assert row["intent"] == "renseignement"
    assert row["suggested_action"] == "sms_confirm"
    assert row["confidence"] == 0.8
    assert any(a["type"] == "sms_sent" and a["ok"] for a in row["actions"])
    assert any(a["type"] == "mail_summary" and a["ok"] for a in row["actions"])
    assert len(sms_calls) == 1
    assert sms_calls[0]["to_phone"] == "+33611111111"
    assert len(mail_calls) == 1
    assert mail_calls[0]["to"] == ["admin@org.fr"]


def test_end_of_call_sms_fails_falls_back_to_body(patched, monkeypatch):
    """Si template SMS inconnu, le handler retente avec body fixe."""
    calls = []

    async def fake_sms(**kw):
        calls.append(kw)
        if kw.get("template_id"):
            return {"error": "template SMS inconnu"}
        return {"sms_id": 1, "status": "sent"}

    async def fake_mail(**kw):
        return {"mail_id": 1, "status": "sent"}

    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(handler.handle_event("vapi", {
        "event_type": "end_of_call", "provider_call_id": "c3",
        "from_phone": "+33611111111", "to_phone": "+33186",
        "status": "completed", "transcript": [{"role": "user", "text": "x"}],
        "summary": "...", "ended_at": datetime.now(timezone.utc),
    }))
    cid = out["call_id"]
    row = patched["log"].rows[cid]
    assert any(a["type"] == "sms_sent" and a["ok"] for a in row["actions"])
    assert len(calls) == 2  # 1er=template, 2e=body fallback
    assert calls[0]["template_id"] == "post_call_confirm"
    assert "body" in calls[1]


def test_end_of_call_invalid_phone_skips_sms(patched, monkeypatch):
    sms_calls = []

    async def fake_sms(**kw):
        sms_calls.append(kw)
        return {"sms_id": 1, "status": "sent"}

    async def fake_mail(**kw):
        return {"mail_id": 1, "status": "sent"}

    import comms.sms.orchestrator as sms_orch
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(sms_orch, "send_sms", fake_sms)
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(handler.handle_event("vapi", {
        "event_type": "end_of_call", "provider_call_id": "c4",
        "from_phone": "0612", "to_phone": "+33186",  # invalide E.164
        "status": "completed", "transcript": [{"role": "user", "text": "x"}],
        "ended_at": datetime.now(timezone.utc),
    }))
    cid = out["call_id"]
    row = patched["log"].rows[cid]
    assert sms_calls == []
    sms_skipped = [a for a in row["actions"] if a["type"] == "sms_sent" and not a["ok"]]
    # rien d'envoyé, mais aussi rien d'append (skipped sans append) → routing mentionne juste skip
    # On vérifie via le retour
    assert any("invalide" in (s.get("reason") or "") for s in out["routing"]["skipped"])


def test_end_of_call_action_transfer_flagged_phase2(patched, monkeypatch):
    async def fake_qualify(transcript, **kw):
        return Qualification(intent="urgence", urgency=3, suggested_action="transfer",
                             requires_human=True, confidence=0.95, summary="urgence vitale")
    monkeypatch.setattr(handler.qualifier_mod, "qualify", fake_qualify)

    async def fake_mail(**kw): return {"mail_id": 1, "status": "sent"}
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(handler.handle_event("vapi", {
        "event_type": "end_of_call", "provider_call_id": "c5",
        "from_phone": "+33611111111", "to_phone": "+33186",
        "status": "completed", "transcript": [{"role": "user", "text": "URGENCE"}],
        "ended_at": datetime.now(timezone.utc),
    }))
    cid = out["call_id"]
    row = patched["log"].rows[cid]
    assert row["intent"] == "urgence"
    assert row["requires_human"] is True
    transfer_actions = [a for a in row["actions"] if a["type"] == "transfer"]
    assert transfer_actions and not transfer_actions[0]["ok"]
    assert "Phase 2" in transfer_actions[0]["reason"]


def test_end_of_call_action_ticket(patched, monkeypatch):
    async def fake_qualify(transcript, **kw):
        return Qualification(intent="autre", suggested_action="ticket",
                             requires_human=False, summary="rien d'urgent")
    monkeypatch.setattr(handler.qualifier_mod, "qualify", fake_qualify)

    async def fake_mail(**kw): return {"mail_id": 1, "status": "sent"}
    import comms.mail.orchestrator as mail_orch
    monkeypatch.setattr(mail_orch, "send_mail", fake_mail)

    out = _run(handler.handle_event("vapi", {
        "event_type": "end_of_call", "provider_call_id": "c6",
        "from_phone": "+33611", "to_phone": "+33186",
        "status": "completed", "transcript": [{"role": "user", "text": "info"}],
        "ended_at": datetime.now(timezone.utc),
    }))
    row = patched["log"].rows[out["call_id"]]
    assert any(a["type"] == "ticket" and a["ok"] for a in row["actions"])


def test_end_of_call_no_provider_id(patched):
    out = _run(handler.handle_event("vapi", {
        "event_type": "end_of_call", "provider_call_id": "",
    }))
    assert out["ok"] is False


def test_handle_event_unknown_event_type(patched):
    out = _run(handler.handle_event("vapi", {"event_type": "weird"}))
    assert out["ok"] is False
