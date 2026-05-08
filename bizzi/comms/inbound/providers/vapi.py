"""Provider Vapi inbound — webhook parser.

Vapi gère lui-même le décrochage / IVR / TTS via un assistant configuré côté
Vapi. Bizzi reçoit ses événements via webhook (server URL Vapi) :

- type='status-update'      : changement de statut (ringing, in-progress, …)
- type='end-of-call-report' : transcript + summary + recording + cost
- type='assistant-request'  : Vapi demande quel assistant servir (Phase 2)

Doc : https://docs.vapi.ai/server-url/events

Coexiste avec bizzi.phone.providers.vapi (outbound). Cred chargés via env
VAPI_API_KEY ou /home/ubuntu/.dashboard_vapi_creds.json (pattern phone).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

VAPI_BASE = "https://api.vapi.ai"
CREDS_PATH = "/home/ubuntu/.dashboard_vapi_creds.json"


def _load_creds() -> dict:
    if os.path.exists(CREDS_PATH):
        try:
            with open(CREDS_PATH) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


class VapiInboundProvider:
    name = "vapi"

    def __init__(self, api_key: Optional[str] = None):
        creds = _load_creds()
        self.api_key = api_key or os.environ.get("VAPI_API_KEY") or creds.get("private_key")

    def health_check(self) -> dict:
        if not self.api_key:
            return {"ok": False, "provider": self.name, "error": "VAPI_API_KEY manquante"}
        try:
            r = httpx.get(
                f"{VAPI_BASE}/assistant",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            return {
                "ok": r.status_code == 200,
                "provider": self.name,
                "status_code": r.status_code,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": str(e)}

    @staticmethod
    def _iso_to_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            # Vapi envoie un ISO 8601 UTC
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def parse_webhook(payload: dict) -> dict:
        """Normalise un événement webhook Vapi en dict standard.

        Retour :
            {
              "event_type": "status_update" | "end_of_call" | "assistant_request" | "ignored",
              "provider_call_id": "...",
              "from_phone": "...",
              "to_phone": "...",
              "status": "ringing" | "in_progress" | "completed" | "failed" | ...,
              "transcript": [...],
              "summary": "...",
              "recording_url": "...",
              "duration_seconds": int,
              "cost_eur": float,
              "ended_at": datetime,
              "answered_at": datetime,
              "error": str?,
              "raw_type": str (type Vapi original)
            }
        """
        msg = (payload or {}).get("message") or {}
        msg_type = (msg.get("type") or "").lower()
        call = msg.get("call") or {}

        out: dict = {
            "event_type": "ignored",
            "provider_call_id": str(call.get("id") or ""),
            "raw_type": msg_type,
        }
        # Numéros : customer = appelant ; phoneNumber = numéro tenant
        customer = call.get("customer") or {}
        out["from_phone"] = str(customer.get("number") or "")
        phone_number = call.get("phoneNumber") or {}
        out["to_phone"] = str(phone_number.get("number") or "")

        if msg_type == "status-update":
            out["event_type"] = "status_update"
            raw_status = (msg.get("status") or "").lower()
            mapped = {
                "queued":       "received",
                "ringing":      "received",
                "in-progress":  "in_progress",
                "answered":     "answered",
                "forwarding":   "answered",
                "ended":        "completed",
            }.get(raw_status, raw_status)
            out["status"] = mapped
            return out

        if msg_type == "end-of-call-report":
            out["event_type"] = "end_of_call"
            out["status"] = "completed"
            out["transcript"] = msg.get("messages") or []
            out["summary"] = msg.get("summary") or ""
            out["recording_url"] = msg.get("recordingUrl") or msg.get("stereoRecordingUrl")
            out["duration_seconds"] = int(msg.get("durationSeconds") or 0)
            out["cost_eur"] = float(msg.get("cost") or 0.0)
            out["ended_at"] = VapiInboundProvider._iso_to_dt(msg.get("endedAt")) or datetime.now(timezone.utc)
            out["answered_at"] = VapiInboundProvider._iso_to_dt(msg.get("startedAt"))
            ended_reason = msg.get("endedReason") or ""
            # NB: l'ordre compte — certains codes de "missed" contiennent "fail"
            # ("twilio-failed-to-connect-call"). On filtre d'abord les missed.
            missed_reasons = {
                "customer-did-not-give-microphone-permission",
                "twilio-failed-to-connect-call",
                "no-answer",
            }
            if ended_reason in missed_reasons:
                out["status"] = "missed"
            elif "error" in ended_reason.lower() or "fail" in ended_reason.lower():
                out["status"] = "failed"
                out["error"] = ended_reason
            return out

        if msg_type == "assistant-request":
            out["event_type"] = "assistant_request"
            return out

        return out
