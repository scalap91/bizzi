"""Orchestrator : glue entre Bizzi (yaml tenant + agents DB + memory) et le provider phone.

Responsabilités Phase 0 :
- Charger la config phone du tenant depuis son yaml
- Vérifier que phone est `enabled` pour ce tenant
- Vérifier le budget mensuel
- Recall mémoire pour enrichir le prompt agent
- Si shadow_mode → log call avec status='initiated', metadata.validation='pending'
- Sinon → provider.place_call() + log
- Webhook Vapi (en Phase 0 : update_call_result manuel)
"""
import os
import yaml
from typing import Optional

from .provider import CallRequest, CallResult
from .providers.vapi import VapiProvider
from . import call_log as call_log_mod
from . import memory as memory_mod
from . import contacts as contacts_mod
from ._db import get_conn

YAML_DIR = "/opt/bizzi/bizzi/domains"


def _tenant_slug_from_id(tenant_id: int) -> Optional[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _load_tenant_config(tenant_slug: str) -> dict:
    path = os.path.join(YAML_DIR, f"{tenant_slug}.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_provider(phone_cfg: dict):
    name = phone_cfg.get("provider", "vapi")
    if name == "vapi":
        return VapiProvider()
    raise ValueError(f"provider phone inconnu : {name}")


def _check_phone_enabled(tenant_cfg: dict) -> tuple[bool, str]:
    phone_cfg = tenant_cfg.get("phone") or {}
    if not phone_cfg.get("enabled"):
        return False, "phone non activé pour ce tenant (yaml: phone.enabled = false)"
    return True, ""


def _check_budget(tenant_cfg: dict, tenant_id: int, estimated_cost: float) -> tuple[bool, str]:
    phone_cfg = tenant_cfg.get("phone") or {}
    budget = float(phone_cfg.get("monthly_budget_eur", 0) or 0)
    if budget <= 0:
        return False, "budget mensuel non défini ou à 0 dans le yaml"
    spent = call_log_mod.get_month_spent_eur(tenant_id)
    if spent + estimated_cost > budget:
        return False, f"budget dépassé : {spent:.2f}€ + {estimated_cost:.2f}€ > {budget:.2f}€"
    return True, ""


def _build_prompt(base_prompt: str, agent_id: int, tenant_id: int, contact: Optional[dict]) -> str:
    """Enrichit le prompt avec recall mémoire + infos contact."""
    parts = [base_prompt]
    if contact:
        parts.append(
            f"\n\nContact : {contact.get('full_name', '?')}"
            f" ({contact.get('role') or 'inconnu'} chez {contact.get('organization') or '—'})."
            f" Niveau de confiance : {contact.get('trust_level', 50)}/100."
        )
    memos = memory_mod.recall_for_agent(tenant_id, agent_id, limit=5)
    if memos:
        parts.append("\nMémoire pertinente :")
        for m in memos:
            parts.append(f"- [{m['memory_type']}] {m.get('title') or ''} : {m['content'][:200]}")
    return "\n".join(parts)


async def make_call(
    tenant_id: int,
    agent_id: int,
    contact_id: Optional[int],
    use_case: str,
    custom_prompt: Optional[str] = None,
    to_phone: Optional[str] = None,
    voice_id: Optional[str] = None,
    force_live: bool = False,
) -> dict:
    """Point d'entrée principal pour passer un appel.

    Phase 0 : shadow_mode = config tenant OR force_live=False par défaut.
    Retour : {call_id, status, mode: 'shadow'|'live', error?}
    """
    slug = _tenant_slug_from_id(tenant_id)
    if not slug:
        return {"error": f"tenant_id {tenant_id} introuvable"}
    tenant_cfg = _load_tenant_config(slug)

    ok, reason = _check_phone_enabled(tenant_cfg)
    if not ok:
        return {"error": reason}

    phone_cfg = tenant_cfg.get("phone") or {}
    use_cases_allowed = phone_cfg.get("use_cases", [])
    if use_cases_allowed and use_case not in use_cases_allowed:
        return {"error": f"use_case '{use_case}' non autorisé (allowed={use_cases_allowed})"}

    contact = contacts_mod.get_contact(tenant_id, contact_id) if contact_id else None
    target_phone = to_phone or (contact.get("phone") if contact else None)
    if not target_phone:
        return {"error": "aucun numéro fourni (to_phone ou contact.phone)"}

    if contact and not contact.get("consent_call"):
        return {"error": f"contact {contact_id} n'a pas donné consentement (consent_call=false)"}

    provider = _build_provider(phone_cfg)
    voice_id = voice_id or phone_cfg.get("voice", "21m00Tcm4TlvDq8ikWAM")

    shadow_mode = phone_cfg.get("shadow_mode", True) and not force_live

    req = CallRequest(
        to_phone=target_phone,
        from_phone=phone_cfg.get("caller_id", ""),
        agent_prompt=_build_prompt(custom_prompt or "", agent_id, tenant_id, contact),
        voice_id=voice_id,
        language=tenant_cfg.get("identity", {}).get("language", "fr"),
        max_duration_sec=int(phone_cfg.get("max_duration_sec", 600)),
        legal_disclaimer=phone_cfg.get("legal_disclaimer"),
        metadata={
            "tenant_id": tenant_id, "agent_id": agent_id,
            "contact_id": contact_id, "use_case": use_case,
        },
        assistant_id=phone_cfg.get("vapi_assistant_id"),
    )

    estimated_cost = provider.estimate_cost(req)
    ok, reason = _check_budget(tenant_cfg, tenant_id, estimated_cost)
    if not ok:
        return {"error": reason}

    # Log d'abord (status=initiated) — l'id servira pour traçer le webhook après
    call_id = call_log_mod.log_call(
        tenant_id=tenant_id, agent_id=agent_id, contact_id=contact_id,
        direction="outbound", status="initiated", phone_number=target_phone,
        use_case=use_case, provider="vapi", shadow_mode=shadow_mode,
        estimated_cost_eur=estimated_cost,
        extra_metadata={"validation": "pending" if shadow_mode else "live"},
    )

    if shadow_mode:
        return {
            "call_id": call_id,
            "status": "queued_shadow",
            "mode": "shadow",
            "estimated_cost_eur": estimated_cost,
            "preview_prompt": req.agent_prompt[:500],
            "to_phone": target_phone,
        }

    # Mode live : appel réel
    try:
        result: CallResult = await provider.place_call(req)
    except Exception as e:
        call_log_mod.update_call_result(
            call_id, status="failed", ended=True,
            extra_metadata=None,
        )
        return {"call_id": call_id, "status": "failed", "error": str(e)}

    call_log_mod.update_call_result(
        call_id,
        status="ringing" if result.status == "queued" else result.status,
        cost_eur=None,
        outcome=None,
    )
    # On enregistre l'id provider en metadata
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE calls SET metadata = metadata || %s::jsonb WHERE id = %s",
            ('{"provider_call_id": "%s"}' % result.provider_call_id, call_id),
        )
        conn.commit()

    return {
        "call_id": call_id,
        "status": result.status,
        "mode": "live",
        "provider_call_id": result.provider_call_id,
        "error": result.error,
    }


async def get_active_calls(tenant_id: int) -> list[dict]:
    return call_log_mod.list_active(tenant_id)


async def list_pending_validation(tenant_id: int) -> list[dict]:
    return call_log_mod.list_pending_validation(tenant_id)


async def validate_pending(
    call_id: int,
    decision: str,
    edited_prompt: Optional[str] = None,
) -> dict:
    """Pascal valide ou refuse un appel shadow.
    decision = 'approve' | 'refuse' | 'edit'.
    Sur approve/edit → relance en mode live.
    """
    if decision not in ("approve", "refuse", "edit"):
        return {"error": f"décision invalide : {decision}"}

    call = call_log_mod.get_call(call_id)
    if not call:
        return {"error": f"call_id {call_id} introuvable"}
    if not (call.get("metadata") or {}).get("shadow_mode"):
        return {"error": "ce call n'est pas en mode shadow"}

    if decision == "refuse":
        call_log_mod.update_call_result(
            call_id, status="rejected", ended=True,
            extra_metadata={"validation": "refused"},
        )
        return {"call_id": call_id, "status": "refused"}

    # approve/edit → relance live
    return await make_call(
        tenant_id=call["tenant_id"],
        agent_id=call["agent_id"],
        contact_id=call.get("contact_id"),
        use_case=(call.get("metadata") or {}).get("use_case", "unknown"),
        custom_prompt=edited_prompt,
        to_phone=call.get("phone_number"),
        force_live=True,
    )
