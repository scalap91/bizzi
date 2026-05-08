"""Tests orchestrator SMS — pas de DB, pas de HTTP réel.
On stub _db, sms_log, rate_limit, et le provider via build_provider.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from comms.sms import orchestrator
from comms.sms import rate_limit as rl
from comms.sms.base import SmsProvider, SmsRequest, SmsResult


# ── Fakes ─────────────────────────────────────────────────────────

class FakeProvider(SmsProvider):
    name = "fake"

    def __init__(self, *, send_status: str = "sent", raise_on_send: bool = False):
        self.calls: list[SmsRequest] = []
        self._send_status = send_status
        self._raise = raise_on_send

    async def send(self, req: SmsRequest) -> SmsResult:
        self.calls.append(req)
        if self._raise:
            raise RuntimeError("boom")
        return SmsResult(
            provider_message_id="fake-msg-1",
            status=self._send_status,
            cost_eur=0.05,
            segments=1,
            raw={"ok": True},
        )

    def estimate_cost(self, req: SmsRequest) -> float:
        return 0.05

    def health_check(self) -> dict:
        return {"ok": True, "provider": self.name}


class FakeSmsLog:
    """In-memory replacement de comms.sms.sms_log.* pour les tests."""
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._id = 0
        self._spent = 0.0

    def log_sms(self, **kw) -> int:
        self._id += 1
        self.rows[self._id] = {"id": self._id, **kw}
        return self._id

    def update_status(self, sms_id: int, status: str, **kw) -> None:
        if sms_id not in self.rows:
            return
        row = self.rows[sms_id]
        row["status"] = status
        for k, v in kw.items():
            row[k] = v

    def approve(self, sms_id: int, approved_by: str) -> None:
        if sms_id in self.rows:
            self.rows[sms_id]["status"] = "approved"
            self.rows[sms_id]["approved_by"] = approved_by

    def reject(self, sms_id: int, approved_by: str, reason: str = "") -> None:
        if sms_id in self.rows:
            self.rows[sms_id]["status"] = "rejected"
            self.rows[sms_id]["approved_by"] = approved_by
            self.rows[sms_id]["error"] = reason

    def get(self, sms_id: int):
        return self.rows.get(sms_id)

    def get_by_provider_id(self, provider: str, pmid: str):
        for r in self.rows.values():
            if r.get("provider") == provider and r.get("provider_message_id") == pmid:
                return r
        return None

    def get_month_spent_eur(self, tenant_id: int) -> float:
        return self._spent


# ── Setup ─────────────────────────────────────────────────────────

@pytest.fixture
def patched(monkeypatch):
    fake_log = FakeSmsLog()
    fake_provider_holder = {"p": FakeProvider()}

    monkeypatch.setattr(orchestrator, "sms_log", fake_log)
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id", lambda tid: "fake_tenant" if tid == 4 else None)
    monkeypatch.setattr(
        orchestrator,
        "_load_tenant_yaml",
        lambda slug: {
            "comms": {
                "sms": {
                    "enabled": True,
                    "provider": "fake",
                    "shadow_mode": False,           # tests live par défaut
                    "monthly_budget_eur": 10,
                    "sender_id": "BizziTest",
                    "rate_limit": {"per_tenant_per_hour": 100, "per_phone_per_day": 5},
                }
            }
        },
    )
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fake_provider_holder["p"])
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (0, 0))

    return {"log": fake_log, "provider": fake_provider_holder}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Tests ─────────────────────────────────────────────────────────

def test_send_unknown_tenant(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id", lambda tid: None)
    out = _run(orchestrator.send_sms(tenant_id=999, to_phone="+33611111111", body="x"))
    assert "error" in out and "introuvable" in out["error"]


def test_send_disabled_module(patched, monkeypatch):
    monkeypatch.setattr(
        orchestrator, "_load_tenant_yaml",
        lambda slug: {"comms": {"sms": {"enabled": False}}},
    )
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111", body="x"))
    assert "non activé" in out["error"]


def test_send_requires_body_or_template(patched):
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111"))
    assert "body ou template_id requis" in out["error"]


def test_send_invalid_phone(patched):
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="0612345678", body="hi"))
    assert "E.164" in out["error"]


def test_send_live_ok(patched):
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111", body="Bonjour"))
    assert out["status"] == "sent"
    assert out["mode"] == "live"
    assert out["provider_message_id"] == "fake-msg-1"
    # log row a bien été inséré + mis à jour
    row = patched["log"].rows[out["sms_id"]]
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "fake-msg-1"


def test_send_shadow_mode(patched, monkeypatch):
    # active shadow_mode dans la config
    cfg = {"comms": {"sms": {"enabled": True, "provider": "fake", "shadow_mode": True}}}
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: cfg)
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111", body="Bonjour"))
    assert out["mode"] == "shadow"
    assert out["status"] == "pending"
    assert "preview_body" in out
    # provider NE doit PAS avoir été appelé
    assert patched["provider"]["p"].calls == []


def test_send_shadow_force_live(patched, monkeypatch):
    cfg = {"comms": {"sms": {"enabled": True, "provider": "fake", "shadow_mode": True}}}
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: cfg)
    out = _run(orchestrator.send_sms(
        tenant_id=4, to_phone="+33611111111", body="Bonjour", force_live=True,
    ))
    assert out["mode"] == "live"
    assert out["status"] == "sent"


def test_send_budget_exceeded(patched, monkeypatch):
    patched["log"]._spent = 999.0  # dépasse le budget de 10€
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111", body="x"))
    assert "budget" in out["error"]


def test_send_rate_limited(patched, monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (200, 0))
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111", body="x"))
    assert "rate_limit" in out["error"]


def test_send_provider_raises(patched, monkeypatch):
    patched["provider"]["p"] = FakeProvider(raise_on_send=True)
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: patched["provider"]["p"])
    out = _run(orchestrator.send_sms(tenant_id=4, to_phone="+33611111111", body="x"))
    assert out["status"] == "failed"
    assert "boom" in out["error"]
    # row marquée failed
    assert patched["log"].rows[out["sms_id"]]["status"] == "failed"


def test_send_with_template(patched, monkeypatch):
    monkeypatch.setattr(
        orchestrator.templates_mod, "render",
        lambda slug, tpl, ctx: f"Hi {ctx['name']}",
    )
    out = _run(orchestrator.send_sms(
        tenant_id=4, to_phone="+33611111111",
        template_id="hello", template_context={"name": "Alice"},
    ))
    assert out["status"] == "sent"
    # le body envoyé au provider vient du rendu
    assert patched["provider"]["p"].calls[0].body == "Hi Alice"


def test_validate_approve_relaunches_live(patched, monkeypatch):
    # 1) crée une row pending manuellement
    sms_id = patched["log"].log_sms(
        tenant_id=4, to_phone="+33611111111", body="x",
        provider="fake", status="pending", shadow=True, segments=1,
    )
    out = _run(orchestrator.validate_pending(sms_id, "approve", approved_by="pascal"))
    # validate_pending relance send_sms en force_live → on attend status sent
    assert out["status"] == "sent"
    assert out["mode"] == "live"


def test_validate_reject(patched):
    sms_id = patched["log"].log_sms(
        tenant_id=4, to_phone="+33611111111", body="x",
        provider="fake", status="pending", shadow=True, segments=1,
    )
    out = _run(orchestrator.validate_pending(sms_id, "reject", approved_by="pascal"))
    assert out["status"] == "rejected"


def test_validate_unknown_decision(patched):
    sms_id = patched["log"].log_sms(
        tenant_id=4, to_phone="+33611111111", body="x",
        provider="fake", status="pending", shadow=True, segments=1,
    )
    out = _run(orchestrator.validate_pending(sms_id, "lol", approved_by="pascal"))
    assert "décision invalide" in out["error"]


def test_validate_not_pending(patched):
    sms_id = patched["log"].log_sms(
        tenant_id=4, to_phone="+33611111111", body="x",
        provider="fake", status="sent", shadow=False, segments=1,
    )
    out = _run(orchestrator.validate_pending(sms_id, "approve", approved_by="pascal"))
    assert "pas pending" in out["error"]


def test_apply_webhook_event_updates_status(patched):
    sms_id = patched["log"].log_sms(
        tenant_id=4, to_phone="+33611111111", body="x",
        provider="brevo", status="sent", shadow=False,
        segments=1,
    )
    patched["log"].rows[sms_id]["provider_message_id"] = "BREVO-1"
    out = orchestrator.apply_webhook_event(
        "brevo",
        {"provider_message_id": "BREVO-1", "status": "delivered"},
    )
    assert out["ok"] is True
    assert patched["log"].rows[sms_id]["status"] == "delivered"


def test_apply_webhook_unknown_msg_id(patched):
    out = orchestrator.apply_webhook_event(
        "brevo", {"provider_message_id": "NOPE", "status": "delivered"},
    )
    assert out["ok"] is False
