"""Tests rate_limit SMS (mock _counts, pas de DB)."""
from __future__ import annotations

from comms.sms import rate_limit as rl


def test_allow_when_under_limits(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (0, 0))
    d = rl.check(1, "+33611111111")
    assert d.allowed is True
    assert d.tenant_count_last_hour == 0
    assert d.phone_count_last_day == 0


def test_block_when_tenant_quota_reached(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (200, 0))
    d = rl.check(1, "+33611111111")
    assert d.allowed is False
    assert "rate_limit tenant" in d.reason


def test_block_when_phone_quota_reached(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (1, 3))
    d = rl.check(1, "+33611111111")
    assert d.allowed is False
    assert "rate_limit numéro" in d.reason


def test_custom_limits(monkeypatch):
    monkeypatch.setattr(rl, "_counts", lambda *a, **k: (5, 1))
    d = rl.check(
        1, "+33611111111",
        per_tenant_per_hour=10, per_phone_per_day=2,
    )
    assert d.allowed is True

    d2 = rl.check(
        1, "+33611111111",
        per_tenant_per_hour=5, per_phone_per_day=2,
    )
    assert d2.allowed is False
    assert "rate_limit tenant" in d2.reason
