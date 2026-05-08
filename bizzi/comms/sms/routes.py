"""Routes FastAPI /api/comms/sms/* — Phase 1.

Endpoints :
  POST   /api/comms/sms/send                 → envoi (shadow par défaut)
  GET    /api/comms/sms/logs?tenant=…        → liste sms_logs
  GET    /api/comms/sms/pending?tenant=…     → file de validation
  POST   /api/comms/sms/{id}/validate        → approve | reject (Pascal)
  POST   /api/comms/sms/webhook/{provider}   → DLR delivery callbacks
  GET    /api/comms/sms/health               → health provider (selon ?provider=)

Auth : Bearer token tenant (réutilise TENANT_TOKENS de api/main.py).
Wiring dans api/main.py : PAS encore (validation Pascal requise = action prod).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import _db
from . import orchestrator, sms_log
from .providers.brevo import BrevoSmsProvider
from .providers.twilio import TwilioSmsProvider

router = APIRouter()


# ── Tenant auth (mirror phone/routes.py) ─────────────────────────

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

class SmsSendBody(BaseModel):
    to_phone: str = Field(..., description="E.164 ex: +33612345678")
    body: Optional[str] = None
    template_id: Optional[str] = None
    template_context: dict = Field(default_factory=dict)
    sender_id: Optional[str] = None
    agent_id: Optional[int] = None
    use_case: Optional[str] = None
    force_live: bool = False


class ValidateBody(BaseModel):
    decision: str  # approve | reject
    approved_by: str


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/send")
async def sms_send(body: SmsSendBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    result = await orchestrator.send_sms(
        tenant_id=tenant_id,
        to_phone=body.to_phone,
        body=body.body,
        template_id=body.template_id,
        template_context=body.template_context,
        sender_id=body.sender_id,
        agent_id=body.agent_id,
        use_case=body.use_case,
        force_live=body.force_live,
        created_by=slug,
    )
    if "error" in result and "sms_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/logs")
async def sms_logs_endpoint(
    slug: str = Depends(get_tenant_slug),
    limit: int = 50,
    status: Optional[str] = None,
):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "logs": sms_log.list_logs(tenant_id, limit=limit, status=status)}


@router.get("/pending")
async def sms_pending(slug: str = Depends(get_tenant_slug), limit: int = 50):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "pending": sms_log.list_pending(tenant_id, limit=limit)}


@router.post("/{sms_id}/validate")
async def sms_validate(sms_id: int, body: ValidateBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    rec = sms_log.get(sms_id)
    if not rec or rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "sms introuvable pour ce tenant")
    result = await orchestrator.validate_pending(
        sms_id, body.decision, approved_by=body.approved_by,
    )
    if "error" in result and "sms_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/webhook/{provider}")
async def sms_webhook(
    provider: str,
    request: Request,
):
    """Webhook delivery (DLR) — pas de Bearer (le provider n'a pas notre token).
    Valide la source en Phase 2 via signature provider (Twilio X-Twilio-Signature, Brevo).
    """
    provider = provider.lower()
    # Twilio envoie x-www-form-urlencoded ; Brevo envoie JSON.
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)

    if provider == "twilio":
        normalized = TwilioSmsProvider.parse_webhook(payload)
    elif provider == "brevo":
        normalized = BrevoSmsProvider.parse_webhook(payload)
    else:
        raise HTTPException(400, f"provider webhook inconnu : {provider}")

    return orchestrator.apply_webhook_event(provider, normalized)


@router.get("/health")
async def sms_health(provider: Optional[str] = None):
    """Health du provider demandé (par défaut : info statique)."""
    if not provider:
        return {
            "module": "comms.sms",
            "phase": 1,
            "providers_supported": ["brevo", "twilio", "ovh"],
            "providers_implemented": ["brevo", "twilio"],
        }
    p = provider.lower()
    try:
        if p == "brevo":
            return BrevoSmsProvider().health_check()
        if p == "twilio":
            return TwilioSmsProvider().health_check()
        raise HTTPException(400, f"provider inconnu : {provider}")
    except RuntimeError as e:
        return {"ok": False, "provider": p, "error": str(e)}
