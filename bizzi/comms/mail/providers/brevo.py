"""Provider mail Brevo (ex-Sendinblue).

Doc API : https://developers.brevo.com/reference/sendtransacemail
Endpoint : POST https://api.brevo.com/v3/smtp/email
Header   : api-key

Body :
  sender:    {email, name?}
  to:        [{email, name?}]
  cc/bcc:    pareil
  subject:   "..."
  htmlContent / textContent
  replyTo:   {email, name?}
  attachment: [{name, content (b64)}]   # optionnel
  tags:      [...]
  scheduledAt: ISO

Webhook events : https://developers.brevo.com/docs/transactional-webhooks
  request, sent, delivered, opened, click, hardBounce, softBounce,
  blocked, error, unsubscribed, deferred, complaint, listAddition
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..base import MailProvider, MailRequest, MailResult

BREVO_API = "https://api.brevo.com/v3/smtp/email"
BREVO_ACCOUNT_API = "https://api.brevo.com/v3/account"


class BrevoMailProvider(MailProvider):
    name = "brevo"

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_from_email: Optional[str] = None,
        default_from_name: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("BREVO_API_KEY") or os.environ.get("BREVO_MAIL_API_KEY")
        self.default_from_email = default_from_email or os.environ.get("BREVO_FROM_EMAIL")
        self.default_from_name = default_from_name or os.environ.get("BREVO_FROM_NAME")
        if not self.api_key:
            raise RuntimeError("BREVO_API_KEY manquante (env ou paramètre)")
        self._headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _addr(email: str, name: Optional[str] = None) -> dict:
        d = {"email": email}
        if name:
            d["name"] = name
        return d

    async def send(self, req: MailRequest) -> MailResult:
        from_email = req.from_email or self.default_from_email
        if not from_email:
            return MailResult(provider_message_id="", status="failed", error="no from_email")

        body: dict = {
            "sender": self._addr(from_email, req.from_name or self.default_from_name),
            "to": [self._addr(e) for e in req.to],
            "subject": req.subject,
        }
        if req.html:
            body["htmlContent"] = req.html
        if req.text:
            body["textContent"] = req.text
        if req.cc:
            body["cc"] = [self._addr(e) for e in req.cc]
        if req.bcc:
            body["bcc"] = [self._addr(e) for e in req.bcc]
        if req.reply_to:
            body["replyTo"] = self._addr(req.reply_to)
        if req.attachments:
            body["attachment"] = []
            for a in req.attachments:
                if a.content_b64:
                    body["attachment"].append({"name": a.filename, "content": a.content_b64})
                elif a.url:
                    body["attachment"].append({"name": a.filename, "url": a.url})
        if req.metadata:
            tag = req.metadata.get("tag") or req.metadata.get("use_case")
            if tag:
                body["tags"] = [str(tag)[:50]]
        if req.scheduled_at:
            body["scheduledAt"] = req.scheduled_at.isoformat()

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(BREVO_API, json=body, headers=self._headers)
        except httpx.HTTPError as e:
            return MailResult(provider_message_id="", status="failed", error=f"HTTP error: {e}")

        if r.status_code >= 400:
            return MailResult(
                provider_message_id="",
                status="failed",
                error=f"HTTP {r.status_code}: {r.text[:500]}",
            )
        try:
            data = r.json()
        except json.JSONDecodeError:
            data = {}
        return MailResult(
            provider_message_id=str(data.get("messageId") or ""),
            status="sent",
            raw=data,
        )

    async def fetch_status(self, provider_message_id: str) -> dict:
        # Brevo : pas d'endpoint simple "get message status" ; on s'appuie sur les webhooks.
        return {"provider": self.name, "provider_message_id": provider_message_id, "info": "use webhooks"}

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
        """Normalise un événement webhook Brevo en
        {provider_message_id, status, opened?, clicked?, error?, delivered_at?, bounced_at?}.

        Brevo envoie un event JSON dont la clé `event` peut être :
          request, sent, delivered, opened, click, hardBounce, softBounce,
          blocked, error, unsubscribed, deferred, complaint, listAddition
        """
        ev = (payload.get("event") or "").lower()
        # 'click' (pas 'clicked'), 'opened', 'hardbounce', etc.
        mapping = {
            "request":      "queued",
            "sent":         "sent",
            "delivered":    "delivered",
            "opened":       "opened",        # marker — on incrémente opens, pas changement de status
            "click":        "clicked",       # marker — on incrémente clicks
            "hardbounce":   "bounced",
            "softbounce":   "bounced",
            "deferred":     "queued",
            "blocked":      "failed",
            "error":        "failed",
            "complaint":    "complained",
            "unsubscribed": "unsubscribed",
        }
        status = mapping.get(ev, "")
        out: dict = {
            "provider_message_id": str(
                payload.get("message-id") or payload.get("messageId") or ""
            ),
            "status": status,
            "event_raw": ev,
        }
        if status == "delivered":
            out["delivered_at"] = datetime.now(timezone.utc)
        if status == "bounced":
            out["bounced_at"] = datetime.now(timezone.utc)
            out["error"] = payload.get("reason") or ev
        if status in ("failed", "complained", "unsubscribed"):
            out["error"] = payload.get("reason") or ev
        if status == "opened":
            out["opened"] = True
        if status == "clicked":
            out["clicked"] = True
        return out
