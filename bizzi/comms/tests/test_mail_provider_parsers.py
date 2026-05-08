"""Tests parsers webhook Brevo + SendGrid mail."""
from __future__ import annotations

from comms.mail.providers.brevo import BrevoMailProvider
from comms.mail.providers.sendgrid import SendgridMailProvider


# ── Brevo ────────────────────────────────────────────────────────

def test_brevo_delivered():
    out = BrevoMailProvider.parse_webhook(
        {"event": "delivered", "message-id": "abc"},
    )
    assert out["status"] == "delivered"
    assert out["provider_message_id"] == "abc"
    assert "delivered_at" in out


def test_brevo_opened():
    out = BrevoMailProvider.parse_webhook({"event": "opened", "message-id": "abc"})
    assert out["status"] == "opened"
    assert out["opened"] is True


def test_brevo_click():
    out = BrevoMailProvider.parse_webhook({"event": "click", "message-id": "abc"})
    assert out["status"] == "clicked"
    assert out["clicked"] is True


def test_brevo_hardbounce():
    out = BrevoMailProvider.parse_webhook(
        {"event": "hardBounce", "message-id": "abc", "reason": "no mailbox"}
    )
    assert out["status"] == "bounced"
    assert out["error"] == "no mailbox"
    assert "bounced_at" in out


def test_brevo_complaint():
    out = BrevoMailProvider.parse_webhook(
        {"event": "complaint", "message-id": "abc", "reason": "user marked spam"}
    )
    assert out["status"] == "complained"
    assert out["error"] == "user marked spam"


def test_brevo_unknown_event():
    out = BrevoMailProvider.parse_webhook({"event": "weird", "message-id": "abc"})
    assert out["status"] == ""


# ── SendGrid ─────────────────────────────────────────────────────

def test_sg_delivered_strips_message_id():
    out = SendgridMailProvider.parse_webhook(
        {"event": "delivered", "sg_message_id": "abc.filterdrecv-foo.bar", "email": "x@y.fr"}
    )
    assert out["status"] == "delivered"
    assert out["provider_message_id"] == "abc"
    assert out["sg_message_id_full"] == "abc.filterdrecv-foo.bar"


def test_sg_open():
    out = SendgridMailProvider.parse_webhook(
        {"event": "open", "sg_message_id": "msgX", "email": "x@y.fr"}
    )
    assert out["status"] == "opened"
    assert out["opened"] is True


def test_sg_click():
    out = SendgridMailProvider.parse_webhook(
        {"event": "click", "sg_message_id": "msgX", "url": "https://x"}
    )
    assert out["status"] == "clicked"
    assert out["clicked"] is True


def test_sg_bounce():
    out = SendgridMailProvider.parse_webhook(
        {"event": "bounce", "sg_message_id": "msgX", "reason": "5xx error"}
    )
    assert out["status"] == "bounced"
    assert out["error"] == "5xx error"


def test_sg_dropped():
    out = SendgridMailProvider.parse_webhook(
        {"event": "dropped", "sg_message_id": "msgX", "reason": "invalid"}
    )
    assert out["status"] == "failed"
    assert out["error"] == "invalid"


def test_sg_spamreport():
    out = SendgridMailProvider.parse_webhook({"event": "spamreport", "sg_message_id": "msgX"})
    assert out["status"] == "complained"


def test_sg_unsubscribe():
    out = SendgridMailProvider.parse_webhook({"event": "unsubscribe", "sg_message_id": "msgX"})
    assert out["status"] == "unsubscribed"
