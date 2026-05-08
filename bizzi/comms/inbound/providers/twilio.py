"""Provider Twilio Voice inbound — TwiML generator + status callback parser.

Phase 1 : pas d'IVR IA temps réel (nécessiterait Twilio Media Streams +
WebSocket bidirectionnel). On expose deux modes basiques :

- 'voicemail' : <Say> greeting + <Record> + raccroche
- 'forward'   : <Say> greeting + <Dial> vers un numéro humain

L'IA temps-réel sera ajoutée en Phase 2 (ou simplement, on bascule l'inbound
sur Vapi qui le gère nativement).

Doc TwiML : https://www.twilio.com/docs/voice/twiml
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape


class TwilioInboundProvider:
    name = "twilio"

    @staticmethod
    def health_check() -> dict:
        # Pas d'auth ici (les creds Twilio sont côté SMS provider). Health = static.
        return {"ok": True, "provider": "twilio", "mode": "twiml-only"}

    @staticmethod
    def generate_twiml(
        *,
        mode: str = "voicemail",
        greeting: str = "Bonjour, vous avez bien joint notre serveur. Veuillez laisser votre message après le bip.",
        forward_to: Optional[str] = None,
        record_max_length: int = 120,
        record_callback_url: Optional[str] = None,
        language: str = "fr-FR",
        voice: str = "Polly.Lea",
    ) -> str:
        """Génère un TwiML pour l'inbound Twilio.

        mode='voicemail' : <Say>+<Record>
        mode='forward'   : <Say>+<Dial> vers `forward_to`
        """
        greeting_xml = escape(greeting)
        say = f'<Say language="{escape(language)}" voice="{escape(voice)}">{greeting_xml}</Say>'

        if mode == "forward":
            if not forward_to:
                # Fallback voicemail si pas de numéro
                mode = "voicemail"
            else:
                dial = (
                    f'<Dial timeout="20" callerId="{escape(forward_to)}">'
                    f'<Number>{escape(forward_to)}</Number>'
                    f'</Dial>'
                )
                return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>{say}{dial}</Response>'

        # mode == 'voicemail'
        record_attrs = [
            f'maxLength="{int(record_max_length)}"',
            'finishOnKey="*"',
            'playBeep="true"',
            'trim="trim-silence"',
        ]
        if record_callback_url:
            record_attrs.append(f'recordingStatusCallback="{escape(record_callback_url)}"')
            record_attrs.append('recordingStatusCallbackMethod="POST"')
        record = f'<Record {" ".join(record_attrs)} />'
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>{say}{record}</Response>'

    @staticmethod
    def parse_voice_status_callback(payload: dict) -> dict:
        """Normalise un Twilio Voice StatusCallback (form-urlencoded → dict).

        Champs : CallSid, From, To, CallStatus, Direction, Duration,
                 RecordingUrl (si Record), RecordingDuration.
        """
        raw_status = (payload.get("CallStatus") or "").lower()
        mapped = {
            "queued":      "received",
            "ringing":     "received",
            "in-progress": "answered",
            "completed":   "completed",
            "busy":        "missed",
            "failed":      "failed",
            "no-answer":   "missed",
            "canceled":    "missed",
        }.get(raw_status, raw_status)

        out: dict = {
            "event_type": "status_update",
            "provider_call_id": str(payload.get("CallSid") or ""),
            "from_phone": str(payload.get("From") or ""),
            "to_phone": str(payload.get("To") or ""),
            "status": mapped,
            "raw_type": "voice_status_callback",
        }
        if payload.get("Duration"):
            try:
                out["duration_seconds"] = int(payload["Duration"])
            except (TypeError, ValueError):
                pass
        if payload.get("RecordingUrl"):
            out["recording_url"] = str(payload["RecordingUrl"])
        if mapped == "completed":
            out["event_type"] = "end_of_call"
            out["ended_at"] = datetime.now(timezone.utc)
        if mapped == "failed":
            out["error"] = payload.get("ErrorCode") or "twilio_failed"
        return out
