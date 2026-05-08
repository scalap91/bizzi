"""Provider mail SendGrid.

Doc API : https://docs.sendgrid.com/api-reference/mail-send/mail-send
Endpoint : POST https://api.sendgrid.com/v3/mail/send
Auth     : Bearer <SENDGRID_API_KEY>

Body :
  personalizations: [{to: [{email}], cc, bcc, custom_args}]
  from: {email, name?}
  reply_to: {email, name?}
  subject: "..."
  content: [{type: "text/plain"|"text/html", value: "..."}]
  attachments: [{content (b64), filename, type}]

Réponse : 202 Accepted, header X-Message-Id contient l'ID.

Event webhook : https://docs.sendgrid.com/for-developers/tracking-events/event
  Array d'events : processed, dropped, delivered, deferred, bounce, blocked,
  open, click, spamreport, unsubscribe, group_unsubscribe, group_resubscribe.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..base import MailProvider, MailRequest, MailResult

SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"
SENDGRID_USER_API = "https://api.sendgrid.com/v3/user/profile"


class SendgridMailProvider(MailProvider):
    name = "sendgrid"

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_from_email: Optional[str] = None,
        default_from_name: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("SENDGRID_API_KEY")
        self.default_from_email = default_from_email or os.environ.get("SENDGRID_FROM_EMAIL")
        self.default_from_name = default_from_name or os.environ.get("SENDGRID_FROM_NAME")
        if not self.api_key:
            raise RuntimeError("SENDGRID_API_KEY manquante (env ou paramètre)")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def send(self, req: MailRequest) -> MailResult:
        from_email = req.from_email or self.default_from_email
        if not from_email:
            return MailResult(provider_message_id="", status="failed", error="no from_email")

        personalization: dict = {"to": [{"email": e} for e in req.to]}
        if req.cc:
            personalization["cc"] = [{"email": e} for e in req.cc]
        if req.bcc:
            personalization["bcc"] = [{"email": e} for e in req.bcc]

        content = []
        if req.text:
            content.append({"type": "text/plain", "value": req.text})
        if req.html:
            content.append({"type": "text/html", "value": req.html})
        if not content:
            return MailResult(provider_message_id="", status="failed", error="no content (html/text)")

        sender = {"email": from_email}
        if req.from_name or self.default_from_name:
            sender["name"] = req.from_name or self.default_from_name

        body: dict = {
            "personalizations": [personalization],
            "from": sender,
            "subject": req.subject,
            "content": content,
        }
        if req.reply_to:
            body["reply_to"] = {"email": req.reply_to}
        if req.attachments:
            body["attachments"] = []
            for a in req.attachments:
                if a.content_b64:
                    body["attachments"].append({
                        "content": a.content_b64,
                        "filename": a.filename,
                        "type": a.content_type,
                    })
        if req.metadata:
            tag = req.metadata.get("tag") or req.metadata.get("use_case")
            if tag:
                body["categories"] = [str(tag)[:50]]

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(SENDGRID_API, json=body, headers=self._headers)
        except httpx.HTTPError as e:
            return MailResult(provider_message_id="", status="failed", error=f"HTTP error: {e}")

        if r.status_code >= 400:
            return MailResult(
                provider_message_id="",
                status="failed",
                error=f"HTTP {r.status_code}: {r.text[:500]}",
            )
        # 202 Accepted, message id dans le header
        msg_id = r.headers.get("X-Message-Id") or r.headers.get("x-message-id") or ""
        return MailResult(provider_message_id=str(msg_id), status="sent", raw={"status_code": r.status_code})

    async def fetch_status(self, provider_message_id: str) -> dict:
        # SendGrid Activity API requiert un compte payant ; webhooks suffisent en Phase 1.
        return {"provider": self.name, "provider_message_id": provider_message_id, "info": "use webhooks"}

    def health_check(self) -> dict:
        try:
            r = httpx.get(SENDGRID_USER_API, headers=self._headers, timeout=5)
            return {
                "ok": r.status_code == 200,
                "provider": self.name,
                "status_code": r.status_code,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": str(e)}

    # ── Parser webhook SendGrid ─────────────────────────────────
    @staticmethod
    def parse_webhook(event: dict) -> dict:
        """Normalise UN événement SendGrid (le webhook envoie un tableau,
        l'appelant itère et passe chaque élément ici).

        Champs : event, sg_message_id, email, timestamp, reason, url, ...
        """
        ev = (event.get("event") or "").lower()
        mapping = {
            "processed":  "queued",
            "deferred":   "queued",
            "delivered":  "delivered",
            "open":       "opened",
            "click":      "clicked",
            "bounce":     "bounced",
            "dropped":    "failed",
            "blocked":    "failed",
            "spamreport": "complained",
            "unsubscribe": "unsubscribed",
            "group_unsubscribe": "unsubscribed",
        }
        status = mapping.get(ev, "")
        # SendGrid : sg_message_id ressemble à "abc.filterdrecv-...".
        # Le X-Message-Id retourné par mail/send correspond au début (avant le '.').
        sg_id = str(event.get("sg_message_id") or "")
        normalized_id = sg_id.split(".")[0] if sg_id else ""
        out: dict = {
            "provider_message_id": normalized_id,
            "sg_message_id_full": sg_id,
            "status": status,
            "event_raw": ev,
        }
        if status == "delivered":
            out["delivered_at"] = datetime.now(timezone.utc)
        if status == "bounced":
            out["bounced_at"] = datetime.now(timezone.utc)
            out["error"] = event.get("reason") or ev
        if status in ("failed", "complained", "unsubscribed"):
            out["error"] = event.get("reason") or ev
        if status == "opened":
            out["opened"] = True
        if status == "clicked":
            out["clicked"] = True
        return out
