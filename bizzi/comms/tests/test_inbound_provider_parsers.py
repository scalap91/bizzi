"""Tests des parsers webhook Vapi inbound + Twilio Voice."""
from __future__ import annotations

from comms.inbound.providers.twilio import TwilioInboundProvider
from comms.inbound.providers.vapi import VapiInboundProvider


# ── Vapi ─────────────────────────────────────────────────────────

def test_vapi_status_update_ringing():
    out = VapiInboundProvider.parse_webhook({
        "message": {
            "type": "status-update",
            "status": "ringing",
            "call": {"id": "call-1", "customer": {"number": "+33611"}, "phoneNumber": {"number": "+33186"}},
        }
    })
    assert out["event_type"] == "status_update"
    assert out["status"] == "received"
    assert out["provider_call_id"] == "call-1"
    assert out["from_phone"] == "+33611"
    assert out["to_phone"] == "+33186"


def test_vapi_status_update_in_progress():
    out = VapiInboundProvider.parse_webhook({
        "message": {"type": "status-update", "status": "in-progress", "call": {"id": "c1"}}
    })
    assert out["status"] == "in_progress"


def test_vapi_end_of_call_basic():
    out = VapiInboundProvider.parse_webhook({
        "message": {
            "type": "end-of-call-report",
            "messages": [{"role": "user", "text": "Bonjour"}],
            "summary": "Appel reçu",
            "recordingUrl": "https://rec/abc.mp3",
            "durationSeconds": 42,
            "cost": 0.12,
            "endedReason": "customer-ended-call",
            "startedAt": "2026-05-08T10:00:00Z",
            "endedAt": "2026-05-08T10:00:42Z",
            "call": {"id": "call-2", "customer": {"number": "+33622"}, "phoneNumber": {"number": "+33186"}},
        }
    })
    assert out["event_type"] == "end_of_call"
    assert out["status"] == "completed"
    assert out["transcript"][0]["text"] == "Bonjour"
    assert out["summary"] == "Appel reçu"
    assert out["recording_url"] == "https://rec/abc.mp3"
    assert out["duration_seconds"] == 42
    assert out["cost_eur"] == 0.12
    assert out["ended_at"] is not None
    assert out["from_phone"] == "+33622"


def test_vapi_end_of_call_with_error_reason():
    out = VapiInboundProvider.parse_webhook({
        "message": {
            "type": "end-of-call-report", "messages": [],
            "endedReason": "twilio-failed-to-connect-call",
            "call": {"id": "c3"},
        }
    })
    # Cette raison spécifique → missed
    assert out["status"] == "missed"


def test_vapi_end_of_call_generic_error():
    out = VapiInboundProvider.parse_webhook({
        "message": {
            "type": "end-of-call-report", "messages": [],
            "endedReason": "assistant-error-system",
            "call": {"id": "c4"},
        }
    })
    assert out["status"] == "failed"
    assert "assistant-error" in out["error"]


def test_vapi_assistant_request():
    out = VapiInboundProvider.parse_webhook({
        "message": {"type": "assistant-request", "call": {"id": "c5"}}
    })
    assert out["event_type"] == "assistant_request"


def test_vapi_unknown_event_ignored():
    out = VapiInboundProvider.parse_webhook({
        "message": {"type": "speech-update", "call": {"id": "c6"}}
    })
    assert out["event_type"] == "ignored"


def test_vapi_missing_message():
    out = VapiInboundProvider.parse_webhook({})
    assert out["event_type"] == "ignored"
    assert out["provider_call_id"] == ""


# ── Twilio ───────────────────────────────────────────────────────

def test_twiml_voicemail_default():
    xml = TwilioInboundProvider.generate_twiml(mode="voicemail", greeting="Bonjour")
    assert xml.startswith('<?xml version="1.0"')
    assert "<Say" in xml
    assert "Bonjour" in xml
    assert "<Record" in xml
    assert "playBeep=\"true\"" in xml


def test_twiml_forward_with_number():
    xml = TwilioInboundProvider.generate_twiml(
        mode="forward", greeting="Hi", forward_to="+33611111111",
    )
    assert "<Dial" in xml
    assert "+33611111111" in xml


def test_twiml_forward_falls_back_to_voicemail_if_no_number():
    xml = TwilioInboundProvider.generate_twiml(mode="forward", greeting="Hi", forward_to=None)
    assert "<Record" in xml
    assert "<Dial" not in xml


def test_twiml_escapes_xml_in_greeting():
    xml = TwilioInboundProvider.generate_twiml(
        mode="voicemail", greeting="Hello & welcome <to>",
    )
    assert "&amp;" in xml
    assert "&lt;to&gt;" in xml


def test_twilio_status_callback_completed():
    out = TwilioInboundProvider.parse_voice_status_callback({
        "CallSid": "CA1", "From": "+33611", "To": "+33186",
        "CallStatus": "completed", "Duration": "30",
        "RecordingUrl": "https://rec/x.mp3",
    })
    assert out["event_type"] == "end_of_call"
    assert out["provider_call_id"] == "CA1"
    assert out["status"] == "completed"
    assert out["duration_seconds"] == 30
    assert out["recording_url"] == "https://rec/x.mp3"


def test_twilio_status_callback_busy():
    out = TwilioInboundProvider.parse_voice_status_callback({
        "CallSid": "CA2", "CallStatus": "busy",
    })
    assert out["status"] == "missed"


def test_twilio_status_callback_failed():
    out = TwilioInboundProvider.parse_voice_status_callback({
        "CallSid": "CA3", "CallStatus": "failed", "ErrorCode": "30005",
    })
    assert out["status"] == "failed"
    assert out["error"] == "30005"


def test_twilio_status_callback_ringing_is_received():
    out = TwilioInboundProvider.parse_voice_status_callback({
        "CallSid": "CA4", "CallStatus": "ringing",
    })
    assert out["status"] == "received"
    assert out["event_type"] == "status_update"
