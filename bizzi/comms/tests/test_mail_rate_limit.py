"""Tests rate_limit mail (mock _counts)."""
from __future__ import annotations

from comms.mail import rate_limit as rl


def test_allow_when_under_limits(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (0, 0))
    d = rl.check(1, "user@example.com")
    assert d.allowed is True


def test_block_tenant(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (1000, 0))
    d = rl.check(1, "user@example.com")
    assert d.allowed is False
    assert "rate_limit tenant" in d.reason


def test_block_email(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (1, 5))
    d = rl.check(1, "user@example.com")
    assert d.allowed is False
    assert "rate_limit destinataire" in d.reason


def test_custom_limits(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (50, 2))
    assert rl.check(1, "u@x.fr", per_tenant_per_hour=100, per_email_per_day=3).allowed is True
    assert rl.check(1, "u@x.fr", per_tenant_per_hour=50,  per_email_per_day=3).allowed is False
    assert rl.check(1, "u@x.fr", per_tenant_per_hour=100, per_email_per_day=2).allowed is False
