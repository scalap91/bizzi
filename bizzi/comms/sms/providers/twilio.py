"""Provider SMS Twilio.

Doc : https://www.twilio.com/docs/sms/send-messages
Endpoint : POST https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json
Auth     : HTTP Basic (account_sid / auth_token)
Body     : From, To, Body, StatusCallback (optionnel)

Webhooks delivery (status_callback) → /api/comms/sms/webhook/twilio.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..base import SmsProvider, SmsRequest, SmsResult


class TwilioSmsProvider(SmsProvider):
    name = "twilio"

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        default_sender: Optional[str] = None,
        status_callback: Optional[str] = None,
    ):
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN")
        self.default_sender = default_sender or os.environ.get("TWILIO_SMS_FROM")
        self.status_callback = status_callback or os.environ.get("TWILIO_STATUS_CALLBACK")
        if not (self.account_sid and self.auth_token):
            raise RuntimeError("TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN requis")

    def _url(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"

    async def send(self, req: SmsRequest) -> SmsResult:
        sender = req.sender_id or self.default_sender
        if not sender:
            return SmsResult(provider_message_id="", status="failed", error="no sender (TWILIO_SMS_FROM)")
        data = {"From": sender, "To": req.to_phone, "Body": req.body}
        if self.status_callback:
            data["StatusCallback"] = self.status_callback
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    self._url(),
                    data=data,
                    auth=(self.account_sid, self.auth_token),
                )
        except httpx.HTTPError as e:
            return SmsResult(provider_message_id="", status="failed", error=f"HTTP error: {e}")

        if r.status_code >= 400:
            return SmsResult(
                provider_message_id="",
                status="failed",
                error=f"HTTP {r.status_code}: {r.text[:500]}",
            )
        try:
            payload = r.json()
        except json.JSONDecodeError:
            payload = {}

        # Twilio: status initial = "queued" | "sending" | "sent". DLR via webhook.
        twilio_status = (payload.get("status") or "queued").lower()
        mapped = {
            "queued": "queued", "accepted": "queued", "sending": "queued",
            "sent": "sent", "delivered": "delivered",
            "failed": "failed", "undelivered": "failed",
        }.get(twilio_status, "queued")

        # Twilio price : NULL avant delivery, négatif (string) sinon. On laisse 0.
        return SmsResult(
            provider_message_id=str(payload.get("sid") or ""),
            status=mapped,
            cost_eur=0.0,
            segments=int(payload.get("num_segments") or 1),
            raw=payload,
        )

    def estimate_cost(self, req: SmsRequest) -> float:
        segments = max(1, (len(req.body.encode("utf-8")) + 159) // 160)
        return 0.075 * segments

    def health_check(self) -> dict:
        try:
            r = httpx.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}.json",
                auth=(self.account_sid, self.auth_token),
                timeout=5,
            )
            return {
                "ok": r.status_code == 200,
                "provider": self.name,
                "status_code": r.status_code,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": str(e)}

    # ── Parser webhook Twilio ───────────────────────────────────
    @staticmethod
    def parse_webhook(payload: dict) -> dict:
        """Normalise un callback StatusCallback Twilio.

        Champs attendus : MessageSid, MessageStatus (queued|sent|delivered|failed|undelivered),
        ErrorCode, ErrorMessage. Twilio envoie x-www-form-urlencoded → caller convertit en dict.
        """
        status_raw = (payload.get("MessageStatus") or "").lower()
        mapped = {
            "queued": "queued", "sending": "queued",
            "sent": "sent",
            "delivered": "delivered",
            "failed": "failed", "undelivered": "failed",
        }.get(status_raw, "")
        out = {
            "provider_message_id": str(payload.get("MessageSid") or ""),
            "status": mapped,
        }
        if mapped == "delivered":
            out["delivered_at"] = datetime.now(timezone.utc)
        if mapped == "failed":
            out["error"] = (
                payload.get("ErrorMessage")
                or f"Twilio error {payload.get('ErrorCode') or ''}".strip()
            )
        return out
