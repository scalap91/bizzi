"""Provider SMS Brevo (ex-Sendinblue).

Doc : https://developers.brevo.com/reference/sendtransacsms
Endpoint : POST https://api.brevo.com/v3/transactionalSMS/sms
Header   : api-key: <BREVO_API_KEY>
Body     : { sender, recipient (E.164 sans +), content, type:"transactional", tag }

Webhooks delivery (DLR) → /api/comms/sms/webhook/brevo.
Doc événements : https://developers.brevo.com/docs/transactional-sms-event-webhooks
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..base import SmsProvider, SmsRequest, SmsResult

BREVO_API = "https://api.brevo.com/v3/transactionalSMS/sms"
BREVO_ACCOUNT_API = "https://api.brevo.com/v3/account"


class BrevoSmsProvider(SmsProvider):
    name = "brevo"

    def __init__(self, api_key: Optional[str] = None, default_sender: Optional[str] = None):
        self.api_key = api_key or os.environ.get("BREVO_API_KEY") or os.environ.get("BREVO_SMS_API_KEY")
        self.default_sender = default_sender or os.environ.get("BREVO_SMS_SENDER", "Bizzi")
        if not self.api_key:
            raise RuntimeError("BREVO_API_KEY manquante (env ou paramètre)")
        self._headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _e164_no_plus(phone: str) -> str:
        # Brevo veut le numéro E.164 SANS le '+' en tête
        return phone.lstrip("+").strip()

    async def send(self, req: SmsRequest) -> SmsResult:
        body = {
            "sender": (req.sender_id or self.default_sender)[:11],  # alphanum max 11
            "recipient": self._e164_no_plus(req.to_phone),
            "content": req.body,
            "type": "transactional",
        }
        if req.metadata:
            tag = str(req.metadata.get("tag") or req.metadata.get("use_case") or "")
            if tag:
                body["tag"] = tag[:50]

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(BREVO_API, json=body, headers=self._headers)
        except httpx.HTTPError as e:
            return SmsResult(provider_message_id="", status="failed", error=f"HTTP error: {e}")

        if r.status_code >= 400:
            return SmsResult(
                provider_message_id="",
                status="failed",
                error=f"HTTP {r.status_code}: {r.text[:500]}",
            )
        try:
            data = r.json()
        except json.JSONDecodeError:
            data = {}

        # Brevo retourne {messageId, smsCount, usedCredits, remainingCredits, reference?}
        return SmsResult(
            provider_message_id=str(data.get("messageId") or ""),
            status="sent",
            cost_eur=float(data.get("usedCredits") or 0.0) * 0.045,  # 1 crédit ≈ 0.045€
            segments=int(data.get("smsCount") or 1),
            raw=data,
        )

    def estimate_cost(self, req: SmsRequest) -> float:
        segments = max(1, (len(req.body.encode("utf-8")) + 159) // 160)
        return 0.045 * segments

    def health_check(self) -> dict:
        try:
            r = httpx.get(BREVO_ACCOUNT_API, headers=self._headers, timeout=5)
            return {
                "ok": r.status_code == 200,
                "provider": self.name,
                "status_code": r.status_code,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": str(e)}

    # ── Parser webhook Brevo ────────────────────────────────────
    @staticmethod
    def parse_webhook(payload: dict) -> dict:
        """Normalise un événement webhook Brevo en {provider_message_id, status, delivered_at?, error?}.

        Événements Brevo SMS : sent, delivered, hardBounce, softBounce, blocked, error, unsubscribed.
        """
        ev = (payload.get("event") or "").lower()
        mapping = {
            "sent":         "sent",
            "delivered":    "delivered",
            "hardbounce":   "failed",
            "softbounce":   "failed",
            "blocked":      "failed",
            "error":        "failed",
            "unsubscribed": "failed",
        }
        status = mapping.get(ev, "")
        out = {
            "provider_message_id": str(payload.get("messageId") or payload.get("message-id") or ""),
            "status": status,
        }
        if status == "delivered":
            out["delivered_at"] = datetime.now(timezone.utc)
        if ev in ("hardbounce", "softbounce", "blocked", "error"):
            out["error"] = payload.get("reason") or ev
        return out
