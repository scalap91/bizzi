"""Routes FastAPI /api/phone/* — Phase 0.

Endpoints :
  POST   /api/phone/calls               → make_call (shadow par défaut)
  GET    /api/phone/calls?tenant=...    → liste des call logs
  GET    /api/phone/pending?tenant=...  → file de validation Pascal
  POST   /api/phone/calls/{id}/validate → approve/refuse/edit
  GET    /api/phone/health              → health Vapi
  POST   /api/phone/webhook/vapi        → webhook fin d'appel Vapi
  GET    /api/phone/contacts?tenant=... → liste contacts
  POST   /api/phone/contacts            → créer/maj contact

Auth : header Authorization: Bearer <token tenant>. Lookup via api/main.py TENANT_TOKENS.
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Request
from pydantic import BaseModel, Field
from typing import Optional
from . import orchestrator, contacts as contacts_mod, call_log as call_log_mod
from ._db import get_conn

router = APIRouter()


def _tenant_id_from_slug(slug: str) -> Optional[int]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return row[0] if row else None


# ── Schemas ──────────────────────────────────────────────────────

class MakeCallBody(BaseModel):
    agent_id: int
    contact_id: Optional[int] = None
    use_case: str
    to_phone: Optional[str] = None
    custom_prompt: Optional[str] = None
    voice_id: Optional[str] = None
    force_live: bool = False


class ContactBody(BaseModel):
    full_name: str
    phone: Optional[str] = None
    role: Optional[str] = None
    organization: Optional[str] = None
    email: Optional[str] = None
    trust_level: int = Field(50, ge=0, le=100)
    consent_call: bool = False
    consent_recording: bool = False
    notes: Optional[str] = None
    tags: Optional[list] = None


class ValidateBody(BaseModel):
    decision: str  # approve | refuse | edit
    edited_prompt: Optional[str] = None


# ── Auth dependency (réutilise TENANT_TOKENS de main.py) ─────────

async def get_tenant_slug(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer ", "").strip()
    from api.main import TENANT_TOKENS
    slug = TENANT_TOKENS.get(token)
    if not slug:
        raise HTTPException(status_code=401, detail="Token invalide")
    return slug


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/calls")
async def post_make_call(body: MakeCallBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    if not tenant_id:
        raise HTTPException(404, f"tenant {slug} introuvable")
    result = await orchestrator.make_call(
        tenant_id=tenant_id,
        agent_id=body.agent_id,
        contact_id=body.contact_id,
        use_case=body.use_case,
        custom_prompt=body.custom_prompt,
        to_phone=body.to_phone,
        voice_id=body.voice_id,
        force_live=body.force_live,
    )
    if "error" in result and "call_id" not in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/calls")
async def list_calls(slug: str = Depends(get_tenant_slug), limit: int = 50, status: Optional[str] = None):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "calls": call_log_mod.get_call_logs(tenant_id, limit=limit, status=status)}


@router.get("/calls/{call_id}")
async def get_call(call_id: int, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    call = call_log_mod.get_call(call_id)
    if not call or call.get("tenant_id") != tenant_id:
        raise HTTPException(404, "call introuvable pour ce tenant")
    return call


@router.get("/pending")
async def pending_validation(slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    return {"tenant": slug, "pending": await orchestrator.list_pending_validation(tenant_id)}


@router.post("/calls/{call_id}/validate")
async def validate_call(call_id: int, body: ValidateBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    call = call_log_mod.get_call(call_id)
    if not call or call.get("tenant_id") != tenant_id:
        raise HTTPException(404, "call introuvable pour ce tenant")
    return await orchestrator.validate_pending(call_id, body.decision, body.edited_prompt)


@router.get("/contacts")
async def list_contacts(slug: str = Depends(get_tenant_slug), q: Optional[str] = None, limit: int = 50):
    tenant_id = _tenant_id_from_slug(slug)
    if q:
        return {"tenant": slug, "contacts": contacts_mod.search_contacts(tenant_id, q, limit)}
    return {"tenant": slug, "contacts": contacts_mod.get_contacts(tenant_id, limit)}


@router.post("/contacts")
async def create_contact(body: ContactBody, slug: str = Depends(get_tenant_slug)):
    tenant_id = _tenant_id_from_slug(slug)
    cid = contacts_mod.upsert_contact(
        tenant_id=tenant_id,
        full_name=body.full_name, phone=body.phone, role=body.role,
        organization=body.organization, email=body.email,
        trust_level=body.trust_level, tags=body.tags,
        consent_call=body.consent_call, consent_recording=body.consent_recording,
        notes=body.notes,
    )
    return {"contact_id": cid}


@router.get("/health")
async def health():
    try:
        from .providers.vapi import VapiProvider
        return VapiProvider().health_check()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/calls/incoming")
async def incoming_call(request: Request):
    """Endpoint inbound unifié : appelé soit par Vapi (webhook nouveau flux),
    soit en redirect 307 depuis legacy `/api/tools/phone/incoming` (Twilio TwiML).

    Phase 0 : log un call inbound minimal en DB et retourne un ack.
    Phase 1+ : router vers logique inbound use-case (support, qualification, etc.).
    """
    payload: dict = {}
    ctype = request.headers.get("content-type", "")
    try:
        if "application/json" in ctype:
            payload = await request.json()
        elif "form" in ctype or "x-www-form-urlencoded" in ctype:
            form = await request.form()
            payload = {k: v for k, v in form.items()}
    except Exception:
        payload = {}

    # Détection format : Twilio legacy (From, CallSid) vs Vapi (call.phoneNumber.number, call.id)
    from_phone: Optional[str] = payload.get("From") or payload.get("from")
    provider_call_id: Optional[str] = payload.get("CallSid")
    provider = "twilio-legacy"
    if not from_phone:
        call_obj = (payload.get("message") or {}).get("call") or payload.get("call") or {}
        from_phone = (call_obj.get("customer") or {}).get("number") or call_obj.get("phoneNumber")
        provider_call_id = call_obj.get("id")
        if call_obj:
            provider = "vapi"

    # Tenant : pour l'instant, lookup unique par caller_id Vapi → tenant lesdemocrates par défaut.
    # Phase 1 : mapping multi-tenant via numéros entrants (table phone_numbers).
    domain = request.query_params.get("domain", "lesdemocrates")
    tenant_id = _tenant_id_from_slug(domain) or 0

    if tenant_id:
        # Pas d'agent_id pour l'instant (inbound non assigné). Phase 0 : on log avec agent_id=0
        # mais la table calls.agent_id est NOT NULL. Donc on prend le 1er agent du tenant.
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM agents WHERE tenant_id = %s ORDER BY id LIMIT 1",
                (tenant_id,),
            )
            row = cur.fetchone()
            agent_id = row[0] if row else None
        if agent_id:
            call_id = call_log_mod.log_call(
                tenant_id=tenant_id, agent_id=agent_id, contact_id=None,
                direction="inbound", status="ringing", phone_number=from_phone,
                use_case="inbound_support", provider=provider,
                provider_call_id=provider_call_id, shadow_mode=False,
                estimated_cost_eur=0.0,
                extra_metadata={"raw_legacy_payload_keys": list(payload.keys())[:20]},
            )
            return {"ok": True, "call_id": call_id, "tenant": domain, "provider": provider}

    return {"ok": False, "error": f"tenant '{domain}' introuvable ou sans agent"}


@router.post("/webhook/vapi")
async def webhook_vapi(payload: dict = Body(...)):
    """Webhook Vapi : appelé en fin d'appel avec le résumé/transcript.
    Doc : https://docs.vapi.ai/server-url
    Phase 0 : on cherche le call par metadata.provider_call_id et on update.
    """
    msg = payload.get("message") or {}
    msg_type = msg.get("type")
    if msg_type not in ("end-of-call-report", "status-update"):
        return {"ok": True, "ignored": msg_type}

    call_obj = msg.get("call") or {}
    provider_call_id = call_obj.get("id")
    if not provider_call_id:
        return {"ok": False, "error": "no call.id in payload"}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM calls WHERE metadata->>'provider_call_id' = %s LIMIT 1",
            (provider_call_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": f"call provider_call_id={provider_call_id} introuvable"}
        call_id = row[0]

    if msg_type == "end-of-call-report":
        call_log_mod.update_call_result(
            call_id,
            status="completed",
            duration_seconds=int(msg.get("durationSeconds") or 0),
            transcript=msg.get("messages") or [],
            summary=msg.get("summary") or "",
            recording_url=msg.get("recordingUrl"),
            cost_eur=float(msg.get("cost") or 0.0),
            outcome=msg.get("endedReason") or "completed",
            ended=True,
        )
    else:
        call_log_mod.update_call_result(call_id, status=msg.get("status") or "in_progress")

    return {"ok": True, "call_id": call_id}
