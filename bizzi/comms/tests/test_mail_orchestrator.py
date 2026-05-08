"""Tests orchestrator mail — pas de DB, pas de HTTP réel."""
from __future__ import annotations

import asyncio

import pytest

from comms.mail import orchestrator
from comms.mail import rate_limit as rl
from comms.mail.base import MailAttachment, MailProvider, MailRequest, MailResult


class FakeProvider(MailProvider):
    name = "brevo"  # nom mappé pour build_provider lookup

    def __init__(self, *, status: str = "sent", raise_on_send: bool = False):
        self.calls: list[MailRequest] = []
        self._status = status
        self._raise = raise_on_send

    async def send(self, req: MailRequest) -> MailResult:
        self.calls.append(req)
        if self._raise:
            raise RuntimeError("kaboom")
        return MailResult(provider_message_id="brevo-1", status=self._status, raw={"ok": 1})

    async def fetch_status(self, pmid: str) -> dict:
        return {"info": "stub"}

    def health_check(self) -> dict:
        return {"ok": True}


class FakeMailLog:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._id = 0
        self._spent = 0.0

    def log_mail(self, **kw) -> int:
        self._id += 1
        self.rows[self._id] = {"id": self._id, **kw}
        return self._id

    def update_status(self, mail_id, status, **kw):
        if mail_id in self.rows:
            self.rows[mail_id]["status"] = status
            self.rows[mail_id].update(kw)

    def increment_open(self, mail_id):
        if mail_id in self.rows:
            self.rows[mail_id]["opens"] = self.rows[mail_id].get("opens", 0) + 1

    def increment_click(self, mail_id):
        if mail_id in self.rows:
            self.rows[mail_id]["clicks"] = self.rows[mail_id].get("clicks", 0) + 1

    def approve(self, mail_id, approved_by):
        if mail_id in self.rows:
            self.rows[mail_id]["status"] = "approved"

    def reject(self, mail_id, approved_by, reason=""):
        if mail_id in self.rows:
            self.rows[mail_id]["status"] = "rejected"

    def get(self, mail_id):
        return self.rows.get(mail_id)

    def get_by_provider_id(self, provider, pmid):
        for r in self.rows.values():
            if r.get("provider") == provider and r.get("provider_message_id") == pmid:
                return r
        return None

    def get_month_spent_eur(self, tenant_id):
        return self._spent

    # ces deux fonctions sont appelées par rate_limit._counts mais on patche _counts
    def count_recent_for_tenant(self, *a, **k): return 0
    def count_recent_for_email(self, *a, **k): return 0


@pytest.fixture
def patched(monkeypatch):
    fake_log = FakeMailLog()
    fake_provider_holder = {"p": FakeProvider()}
    monkeypatch.setattr(orchestrator, "mail_log", fake_log)
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id",
                        lambda tid: "fake_tenant" if tid == 4 else None)
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: {
        "comms": {"mail": {
            "enabled": True, "provider": "brevo",
            "shadow_mode": False, "monthly_budget_eur": 10,
            "from_email": "noreply@example.com", "from_name": "Bizzi",
            "rate_limit": {"per_tenant_per_hour": 1000, "per_email_per_day": 5},
        }}
    })
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fake_provider_holder["p"])
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (0, 0))
    return {"log": fake_log, "provider": fake_provider_holder}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_unknown_tenant(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id", lambda tid: None)
    out = _run(orchestrator.send_mail(tenant_id=999, to=["x@y.fr"], subject="s", text="t"))
    assert "introuvable" in out["error"]


def test_disabled(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml",
                        lambda slug: {"comms": {"mail": {"enabled": False}}})
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], subject="s", text="t"))
    assert "non activé" in out["error"]


def test_invalid_email(patched):
    out = _run(orchestrator.send_mail(tenant_id=4, to=["nope"], subject="s", text="t"))
    assert "adresse invalide" in out["error"]


def test_requires_subject(patched):
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], text="t"))
    assert "subject requis" in out["error"]


def test_requires_body(patched):
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], subject="s"))
    assert "html ou text requis" in out["error"]


def test_live_ok(patched):
    out = _run(orchestrator.send_mail(
        tenant_id=4, to=["x@y.fr"], subject="Hello", text="Hi",
    ))
    assert out["status"] == "sent" and out["mode"] == "live"
    row = patched["log"].rows[out["mail_id"]]
    assert row["provider_message_id"] == "brevo-1"
    assert row["status"] == "sent"


def test_shadow(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: {
        "comms": {"mail": {
            "enabled": True, "provider": "brevo", "shadow_mode": True,
            "from_email": "x@y.fr",
        }}
    })
    fp = FakeProvider()
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fp)
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], subject="s", text="t"))
    assert out["mode"] == "shadow" and out["status"] == "pending"
    assert "preview" in out
    assert fp.calls == []


def test_shadow_force_live(patched, monkeypatch):
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: {
        "comms": {"mail": {
            "enabled": True, "provider": "brevo", "shadow_mode": True,
            "from_email": "x@y.fr",
        }}
    })
    out = _run(orchestrator.send_mail(
        tenant_id=4, to=["x@y.fr"], subject="s", text="t", force_live=True,
    ))
    assert out["mode"] == "live"


def test_budget_exceeded(patched):
    patched["log"]._spent = 1000
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], subject="s", text="t"))
    assert "budget" in out["error"]


def test_rate_limited(patched, monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (1000, 0))
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], subject="s", text="t"))
    assert "rate_limit" in out["error"]


def test_provider_raises(patched, monkeypatch):
    fp = FakeProvider(raise_on_send=True)
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fp)
    out = _run(orchestrator.send_mail(tenant_id=4, to=["x@y.fr"], subject="s", text="t"))
    assert out["status"] == "failed" and "kaboom" in out["error"]


def test_template_render(patched, monkeypatch):
    from comms.mail.templates import RenderedMail
    monkeypatch.setattr(orchestrator.templates_mod, "render",
                        lambda slug, tpl, ctx: RenderedMail(
                            subject=f"Hi {ctx['name']}",
                            html=f"<p>Hi {ctx['name']}</p>",
                            text=f"Hi {ctx['name']}",
                        ))
    out = _run(orchestrator.send_mail(
        tenant_id=4, to=["x@y.fr"],
        template_id="welcome", template_context={"name": "Alice"},
    ))
    assert out["status"] == "sent"
    req = patched["provider"]["p"].calls[0]
    assert req.subject == "Hi Alice"
    assert req.html == "<p>Hi Alice</p>"
    assert req.text == "Hi Alice"


def test_validate_approve(patched):
    mid = patched["log"].log_mail(
        tenant_id=4, to_addrs=["x@y.fr"], subject="s", text="t",
        provider="brevo", status="pending", shadow=True,
    )
    out = _run(orchestrator.validate_pending(mid, "approve", approved_by="pascal"))
    assert out["status"] == "sent"


def test_validate_reject(patched):
    mid = patched["log"].log_mail(
        tenant_id=4, to_addrs=["x@y.fr"], subject="s", text="t",
        provider="brevo", status="pending", shadow=True,
    )
    out = _run(orchestrator.validate_pending(mid, "reject", approved_by="pascal"))
    assert out["status"] == "rejected"


def test_apply_webhook_delivered(patched):
    mid = patched["log"].log_mail(
        tenant_id=4, to_addrs=["x@y.fr"], subject="s", text="t",
        provider="brevo", status="sent", shadow=False,
    )
    patched["log"].rows[mid]["provider_message_id"] = "BREVO-1"
    out = orchestrator.apply_webhook_event("brevo", {"provider_message_id": "BREVO-1", "status": "delivered"})
    assert out["ok"] is True
    assert patched["log"].rows[mid]["status"] == "delivered"


def test_apply_webhook_opened_increments(patched):
    mid = patched["log"].log_mail(
        tenant_id=4, to_addrs=["x@y.fr"], subject="s", text="t",
        provider="brevo", status="delivered", shadow=False,
    )
    patched["log"].rows[mid]["provider_message_id"] = "BREVO-2"
    out = orchestrator.apply_webhook_event(
        "brevo",
        {"provider_message_id": "BREVO-2", "status": "opened", "opened": True},
    )
    assert out["ok"] is True and out["event"] == "opened"
    assert patched["log"].rows[mid]["opens"] == 1
    # status doit rester delivered (pas écrasé par "opened")
    assert patched["log"].rows[mid]["status"] == "delivered"


def test_apply_webhook_clicked_increments(patched):
    mid = patched["log"].log_mail(
        tenant_id=4, to_addrs=["x@y.fr"], subject="s", text="t",
        provider="brevo", status="delivered", shadow=False,
    )
    patched["log"].rows[mid]["provider_message_id"] = "BREVO-3"
    out = orchestrator.apply_webhook_event(
        "brevo",
        {"provider_message_id": "BREVO-3", "status": "clicked", "clicked": True},
    )
    assert out["event"] == "clicked"
    assert patched["log"].rows[mid]["clicks"] == 1
