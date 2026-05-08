"""Routes FastAPI /api/comms/mail/* — Phase 1.

Endpoints :
  POST   /api/comms/mail/send                → envoi (shadow par défaut)
  GET    /api/comms/mail/logs?…              → liste mail_logs
  GET    /api/comms/mail/pending?…           → file de validation
  POST   /api/comms/mail/{id}/validate       → approve | reject (Pascal)
  POST   /api/comms/mail/webhook/{provider}  → DLR + opens/clicks
  GET    /api/comms/mail/health              → health provider (selon ?provider=)

Auth : Bearer token tenant.
Wiring dans api/main.py : PAS encore (validation Pascal requise = action prod).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import _db
from . import mail_log, orchestrator
from .base import MailAttachment
from .providers.brevo import BrevoMailProvider
from .providers.sendgrid import SendgridMailProvider

router = APIRouter()


# ── Tenant auth ──────────────────────────────────────────────────

async def get_tenant_slug(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer ", "").strip()
    from api.main import TENANT_TOKENS
    slug = TENANT_TOKENS.get(token)
    if not slug:
        raise HTTPException(status_code=401, detail="Token invalide")
    return slug


def _tenant_id_from_slug(slug: str) -> int:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"tenant {slug} introuvable")
        return row[0]


# ── Schemas ──────────────────────────────────────────────────────

class AttachmentBody(BaseModel):
    filename: str
    content_b64: Optional[str] = None
    url: Optional[str] = None
    content_type: str = "application/octet-stream"


class MailSendBody(BaseModel):
    to: list[str]
    subject: Optional[str] = None
    html: Optional[str] = None
    text: Optional[str] = None
    template_id: Optional[str] = None
    template_context: dict = Field(default_factory=dict)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    attachments: list[AttachmentBody] = Field(default_factory=list)
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None
    agent_id: Optional[int] = None
    use_case: Optional[str] = None
    force_live: bool = False


class ValidateBody(BaseModel):
    decision: str  # approve | reject
    approved_by: str


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/send")
async def mail_send(body: MailSendBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    attachments = [
        MailAttachment(
            filename=a.filename,
            content_b64=a.content_b64,
            url=a.url,
            content_type=a.content_type,
        )
        for a in body.attachments
    ]
    result = await orchestrator.send_mail(
        tenant_id=tenant_id,
        to=body.to,
        subject=body.subject,
        html=body.html,
        text=body.text,
        template_id=body.template_id,
        template_context=body.template_context,
        cc=body.cc, bcc=body.bcc,
        from_email=body.from_email, from_name=body.from_name,
        reply_to=body.reply_to,
        attachments=attachments,
        track_opens=body.track_opens, track_clicks=body.track_clicks,
        agent_id=body.agent_id,
        use_case=body.use_case,
        force_live=body.force_live,
        created_by=slug,
    )
    if "error" in result and "mail_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/logs")
async def mail_logs_endpoint(
    slug: str = Depends(get_tenant_slug),
    limit: int = 50,
    status: Optional[str] = None,
):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "logs": mail_log.list_logs(tenant_id, limit=limit, status=status)}


@router.get("/pending")
async def mail_pending(slug: str = Depends(get_tenant_slug), limit: int = 50):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "pending": mail_log.list_pending(tenant_id, limit=limit)}


@router.post("/{mail_id}/validate")
async def mail_validate(mail_id: int, body: ValidateBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    rec = mail_log.get(mail_id)
    if not rec or rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "mail introuvable pour ce tenant")
    result = await orchestrator.validate_pending(mail_id, body.decision, approved_by=body.approved_by)
    if "error" in result and "mail_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/webhook/{provider}")
async def mail_webhook(provider: str, request: Request):
    """Webhook delivery + tracking (opens/clicks). Pas de Bearer (provider-side).

    - Brevo : envoie 1 event JSON par requête.
    - SendGrid : envoie un tableau d'events JSON.
    """
    provider = provider.lower()
    payload = await request.json()

    if provider == "brevo":
        normalized = BrevoMailProvider.parse_webhook(payload)
        return orchestrator.apply_webhook_event(provider, normalized)

    if provider == "sendgrid":
        events = payload if isinstance(payload, list) else [payload]
        results = []
        for ev in events:
            normalized = SendgridMailProvider.parse_webhook(ev)
            results.append(orchestrator.apply_webhook_event(provider, normalized))
        return {"events": len(events), "results": results}

    raise HTTPException(400, f"provider webhook inconnu : {provider}")


@router.get("/health")
async def mail_health(provider: Optional[str] = None):
    if not provider:
        return {
            "module": "comms.mail",
            "phase": 1,
            "providers_supported": ["brevo", "sendgrid", "mailgun", "ses"],
            "providers_implemented": ["brevo", "sendgrid"],
        }
    p = provider.lower()
    try:
        if p == "brevo":
            return BrevoMailProvider().health_check()
        if p == "sendgrid":
            return SendgridMailProvider().health_check()
        raise HTTPException(400, f"provider inconnu : {provider}")
    except RuntimeError as e:
        return {"ok": False, "provider": p, "error": str(e)}
