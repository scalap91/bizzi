"""Routes FastAPI /api/comms/inbound/* — Phase 1.

Endpoints :
  POST   /api/comms/inbound/webhook            → Vapi server URL (JSON)
  POST   /api/comms/inbound/twiml              → Twilio Voice URL (form → XML response)
  POST   /api/comms/inbound/twilio/status      → Twilio status callback (form)
  GET    /api/comms/inbound/logs?…             → liste inbound_call_logs
  GET    /api/comms/inbound/{id}               → détail
  GET    /api/comms/inbound/health             → health providers

Auth : Bearer token tenant pour les GET. Webhooks externes pas d'auth (les
providers n'ont pas notre token — signature à valider en Phase 2).

Wiring dans api/main.py : PAS encore (validation Pascal requise).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response

from .. import _db, _template
from . import handler, inbound_log
from .providers.twilio import TwilioInboundProvider
from .providers.vapi import VapiInboundProvider

router = APIRouter()


# ── Tenant auth (pour GETs) ──────────────────────────────────────

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


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/webhook")
async def vapi_webhook(payload: dict = Body(...)):
    """Webhook Vapi (server URL). Body JSON. Pas de Bearer (provider ne l'a pas)."""
    normalized = VapiInboundProvider.parse_webhook(payload)
    return await handler.handle_event("vapi", normalized)


@router.post("/twiml")
async def twiml_response(request: Request):
    """Twilio Voice URL : reçoit form-urlencoded, renvoie un TwiML XML.

    Détecte le tenant via le numéro `To` (numéro tenant appelé). Si tenant
    inconnu → TwiML générique 'voicemail'.
    """
    form = await request.form()
    payload = dict(form)
    to_phone = str(payload.get("To") or "")

    inbound_cfg: dict = {}
    if to_phone:
        tenant_id = handler._tenant_id_from_to_phone(to_phone)
        if tenant_id:
            slug = handler._tenant_slug_from_id(tenant_id)
            if slug:
                inbound_cfg = handler.load_inbound_config(slug)

    twiml = TwilioInboundProvider.generate_twiml(
        mode=inbound_cfg.get("twilio_mode") or "voicemail",
        greeting=inbound_cfg.get("greeting") or "Bonjour, vous avez bien joint notre serveur. Veuillez laisser votre message après le bip.",
        forward_to=inbound_cfg.get("transfer_to"),
        record_max_length=int(inbound_cfg.get("record_max_length", 120)),
        record_callback_url=inbound_cfg.get("record_callback_url"),
        language=inbound_cfg.get("language") or "fr-FR",
        voice=inbound_cfg.get("twilio_voice") or "Polly.Lea",
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request):
    """Status callback Twilio Voice. Form-urlencoded → log + finalize si completed."""
    form = await request.form()
    payload = dict(form)
    normalized = TwilioInboundProvider.parse_voice_status_callback(payload)
    return await handler.handle_event("twilio", normalized)


@router.get("/logs")
async def list_logs(
    slug: str = Depends(get_tenant_slug),
    limit: int = 50,
    status: Optional[str] = None,
    intent: Optional[str] = None,
    requires_human: Optional[bool] = None,
):
    tenant_id = _tenant_id_from_slug(slug)
    return {
        "tenant": slug,
        "logs": inbound_log.list_logs(
            tenant_id, limit=limit, status=status,
            intent=intent, requires_human=requires_human,
        ),
    }


@router.get("/{call_id}")
async def get_call(call_id: int, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    rec = inbound_log.get(call_id)
    if not rec or rec.get("tenant_id") != tenant_id:
        raise HTTPException(404, "call introuvable pour ce tenant")
    return rec


@router.get("/health")
async def inbound_health(provider: Optional[str] = None):
    if not provider:
        return {
            "module": "comms.inbound",
            "phase": 1,
            "providers_supported": ["vapi", "twilio"],
            "providers_implemented": ["vapi", "twilio"],
        }
    p = provider.lower()
    if p == "vapi":
        return VapiInboundProvider().health_check()
    if p == "twilio":
        return TwilioInboundProvider.health_check()
    raise HTTPException(400, f"provider inconnu : {provider}")
