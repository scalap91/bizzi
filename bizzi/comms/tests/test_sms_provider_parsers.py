"""Tests des parsers webhook pour Brevo et Twilio (pas de HTTP, pas de DB)."""
from __future__ import annotations

from comms.sms.providers.brevo import BrevoSmsProvider
from comms.sms.providers.twilio import TwilioSmsProvider


def test_brevo_parse_delivered():
    out = BrevoSmsProvider.parse_webhook({"event": "delivered", "messageId": "abc"})
    assert out["provider_message_id"] == "abc"
    assert out["status"] == "delivered"
    assert "delivered_at" in out


def test_brevo_parse_hardbounce():
    out = BrevoSmsProvider.parse_webhook(
        {"event": "hardBounce", "messageId": "abc", "reason": "no number"}
    )
    assert out["status"] == "failed"
    assert out["error"] == "no number"


def test_brevo_parse_unknown_event():
    out = BrevoSmsProvider.parse_webhook({"event": "weird", "messageId": "abc"})
    assert out["status"] == ""


def test_twilio_parse_delivered():
    out = TwilioSmsProvider.parse_webhook(
        {"MessageSid": "SM123", "MessageStatus": "delivered"}
    )
    assert out["provider_message_id"] == "SM123"
    assert out["status"] == "delivered"
    assert "delivered_at" in out


def test_twilio_parse_failed():
    out = TwilioSmsProvider.parse_webhook(
        {
            "MessageSid": "SM123",
            "MessageStatus": "failed",
            "ErrorCode": "30007",
            "ErrorMessage": "Carrier blocked",
        }
    )
    assert out["status"] == "failed"
    assert out["error"] == "Carrier blocked"


def test_twilio_parse_queued():
    out = TwilioSmsProvider.parse_webhook(
        {"MessageSid": "SM123", "MessageStatus": "sending"}
    )
    assert out["status"] == "queued"
