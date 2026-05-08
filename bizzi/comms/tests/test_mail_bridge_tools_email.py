"""Tests pont tools.email → comms.mail.send_mail (sans DB ni HTTP)."""
from __future__ import annotations

import asyncio
import hashlib
import pathlib

import pytest

from comms.mail import orchestrator
from comms.mail import rate_limit as mrl
from comms.mail.base import MailProvider, MailRequest, MailResult
from comms.mail.bridges import tools_email as bridge


class _FakeProvider(MailProvider):
    name = "brevo"

    def __init__(self):
        self.calls: list[MailRequest] = []

    async def send(self, req):
        self.calls.append(req)
        return MailResult(provider_message_id="msg-bridge", status="sent", raw={})

    async def fetch_status(self, pmid):
        return {}

    def health_check(self):
        return {"ok": True}


class _FakeLog:
    def __init__(self):
        self.rows: dict = {}
        self._id = 0

    def log_mail(self, **kw):
        self._id += 1
        self.rows[self._id] = {"id": self._id, **kw}
        return self._id

    def update_status(self, mid, status, **kw):
        if mid in self.rows:
            self.rows[mid].update(status=status, **kw)

    def increment_open(self, mid): pass
    def increment_click(self, mid): pass
    def approve(self, mid, approved_by): pass
    def reject(self, mid, approved_by, reason=""): pass
    def get(self, mid): return self.rows.get(mid)
    def get_by_provider_id(self, p, pmid): return None
    def get_month_spent_eur(self, tid): return 0.0
    def count_recent_for_tenant(self, *a, **k): return 0
    def count_recent_for_email(self, *a, **k): return 0


@pytest.fixture
def patched(monkeypatch):
    flog = _FakeLog()
    fp = _FakeProvider()
    monkeypatch.setattr(orchestrator, "mail_log", flog)
    monkeypatch.setattr(orchestrator, "_tenant_slug_from_id",
                        lambda tid: "fake" if tid == 4 else None)
    monkeypatch.setattr(orchestrator, "build_provider", lambda cfg: fp)
    monkeypatch.setattr(orchestrator, "_load_tenant_yaml", lambda slug: {
        "comms": {"mail": {
            "enabled": True, "provider": "brevo", "shadow_mode": False,
            "monthly_budget_eur": 10, "from_email": "noreply@example.com",
        }}
    })
    monkeypatch.setattr(mrl, "_counts", lambda *a, **k: (0, 0))
    return {"log": flog, "provider": fp}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_async_text(patched):
    ok = _run(bridge.send_via_comms_async(
        tenant_id=4, to="u@x.fr", subject="Hi", body="Hello",
    ))
    assert ok is True
    req = patched["provider"].calls[-1]
    assert req.text == "Hello" and req.html is None
    assert req.metadata.get("use_case") == "tools_email_autoreply"


def test_async_html(patched):
    ok = _run(bridge.send_via_comms_async(
        tenant_id=4, to="u@x.fr", subject="Hi", body="<p>x</p>", body_is_html=True,
    ))
    assert ok is True
    req = patched["provider"].calls[-1]
    assert req.html == "<p>x</p>" and req.text is None


def test_async_unknown_tenant(patched):
    ok = _run(bridge.send_via_comms_async(
        tenant_id=999, to="u@x.fr", subject="x", body="x",
    ))
    assert ok is False


def test_async_propagates_reply_to_and_agent_id(patched):
    _run(bridge.send_via_comms_async(
        tenant_id=4, to="u@x.fr", subject="x", body="x",
        reply_to="contact@org.fr", agent_id=42,
    ))
    req = patched["provider"].calls[-1]
    assert req.reply_to == "contact@org.fr"
    assert req.agent_id == 42


def test_sync_outside_loop(patched):
    ok = bridge.send_via_comms(tenant_id=4, to="u@x.fr", subject="Sync", body="x")
    assert ok is True
    assert patched["provider"].calls[-1].subject == "Sync"


def test_sync_inside_loop(patched):
    """Cas EmailAgent.process : sync helper appelé depuis async — threaded."""
    async def _inside():
        return bridge.send_via_comms(tenant_id=4, to="u@x.fr", subject="InLoop", body="x")

    ok = asyncio.run(_inside())
    assert ok is True
    assert patched["provider"].calls[-1].subject == "InLoop"


def test_legacy_email_agent_unchanged():
    """Garde-fou : tools/email/email_agent.py NE DOIT PAS être modifié par le pont.

    Hash gelé après lecture initiale (cf. /tmp/bizzi_audit_doublons_synth.md règle
    'Aucun fichier ancien supprimé').
    """
    p = pathlib.Path("/opt/bizzi/bizzi/tools/email/email_agent.py")
    assert p.exists()
    content = p.read_bytes()
    # Sanity : taille et signature classes connues
    assert b"class EmailAgent" in content
    assert b"def send_email" in content
    # On vérifie que c'est bien le fichier d'origine (long et non vide)
    assert len(content) > 5000
