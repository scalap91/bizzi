"""Orchestrator SMS : glue entre yaml tenant, rate limit, provider et sms_logs.

Responsabilités :
- Résoudre tenant_slug ↔ tenant_id
- Charger config `comms.sms` du tenant (yaml)
- Vérifier enabled, budget mensuel, rate limit
- Rendre template si fourni
- Shadow mode → log status='pending', return preview (Pascal valide)
- Live      → log status='queued', appel provider, log update

Pattern miroir de bizzi.phone.orchestrator.
"""
from __future__ import annotations

import os
from typing import Optional

import yaml

from .. import _db
from . import rate_limit, sms_log, templates as templates_mod
from .base import SmsProvider, SmsRequest, SmsResult

YAML_DIR = "/opt/bizzi/bizzi/domains"


# ── Tenant resolution ─────────────────────────────────────────────

def _tenant_slug_from_id(tenant_id: int) -> Optional[str]:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _tenant_id_from_slug(slug: str) -> Optional[int]:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return row[0] if row else None


def _load_tenant_yaml(tenant_slug: str) -> dict:
    path = os.path.join(YAML_DIR, f"{tenant_slug}.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Provider factory ──────────────────────────────────────────────

def build_provider(sms_cfg: dict) -> SmsProvider:
    name = (sms_cfg.get("provider") or "brevo").lower()
    if name == "brevo":
        from .providers.brevo import BrevoSmsProvider
        return BrevoSmsProvider(
            api_key=sms_cfg.get("brevo_api_key"),
            default_sender=sms_cfg.get("sender_id"),
        )
    if name == "twilio":
        from .providers.twilio import TwilioSmsProvider
        return TwilioSmsProvider(
            account_sid=sms_cfg.get("twilio_account_sid"),
            auth_token=sms_cfg.get("twilio_auth_token"),
            default_sender=sms_cfg.get("sender_id"),
            status_callback=sms_cfg.get("status_callback"),
        )
    if name == "ovh":
        from .providers.ovh import OvhSmsProvider
        return OvhSmsProvider()  # stub Phase 1 (signature OVH non implémentée)
    raise ValueError(f"provider SMS inconnu : {name}")


# ── Orchestration ─────────────────────────────────────────────────

async def send_sms(
    *,
    tenant_id: int,
    to_phone: str,
    body: Optional[str] = None,
    template_id: Optional[str] = None,
    template_context: Optional[dict] = None,
    sender_id: Optional[str] = None,
    agent_id: Optional[int] = None,
    use_case: Optional[str] = None,
    force_live: bool = False,
    created_by: Optional[str] = None,
) -> dict:
    """Point d'entrée principal pour envoyer un SMS.

    Retour : {sms_id, status, mode: 'shadow'|'live', error?, ...}
    """
    slug = _tenant_slug_from_id(tenant_id)
    if not slug:
        return {"error": f"tenant_id {tenant_id} introuvable"}

    tenant_cfg = _load_tenant_yaml(slug)
    sms_cfg = ((tenant_cfg.get("comms") or {}).get("sms") or {})
    if not sms_cfg.get("enabled"):
        return {"error": "comms.sms non activé pour ce tenant (yaml: comms.sms.enabled)"}

    # Body : direct ou template
    if not body:
        if not template_id:
            return {"error": "body ou template_id requis"}
        try:
            body = templates_mod.render(slug, template_id, template_context or {})
        except KeyError as e:
            return {"error": str(e)}
        except ValueError as e:
            return {"error": f"template render: {e}"}

    body = body.strip()
    if not body:
        return {"error": "body vide"}

    # E.164 minimal check
    to_phone = to_phone.strip()
    if not to_phone.startswith("+") or not to_phone[1:].isdigit():
        return {"error": f"to_phone doit être au format E.164 (+33…), reçu : {to_phone!r}"}

    # Provider
    try:
        provider = build_provider(sms_cfg)
    except Exception as e:  # noqa: BLE001
        return {"error": f"provider init: {e}"}

    # Estimation coût + segments
    req = SmsRequest(
        tenant_id=tenant_id,
        to_phone=to_phone,
        body=body,
        sender_id=sender_id or sms_cfg.get("sender_id"),
        template_id=template_id,
        template_context=template_context or {},
        agent_id=agent_id,
        metadata={"use_case": use_case} if use_case else {},
    )
    estimated_cost = provider.estimate_cost(req)
    segments = max(1, (len(req.body.encode("utf-8")) + 159) // 160)

    # Budget mensuel
    budget = float(sms_cfg.get("monthly_budget_eur") or 0)
    if budget > 0:
        spent = sms_log.get_month_spent_eur(tenant_id)
        if spent + estimated_cost > budget:
            return {"error": f"budget SMS dépassé : {spent:.2f}€ + {estimated_cost:.2f}€ > {budget:.2f}€"}

    # Rate limit
    rl_cfg = sms_cfg.get("rate_limit") or {}
    rl = rate_limit.check(
        tenant_id, to_phone,
        per_tenant_per_hour=rl_cfg.get("per_tenant_per_hour"),
        per_phone_per_day=rl_cfg.get("per_phone_per_day"),
    )
    if not rl.allowed:
        return {"error": rl.reason}

    shadow_mode = bool(sms_cfg.get("shadow_mode", True)) and not force_live

    # Log d'abord — l'id servira pour tracer le webhook après
    sms_id = sms_log.log_sms(
        tenant_id=tenant_id,
        agent_id=agent_id,
        to_phone=to_phone,
        body=body,
        provider=provider.name,
        sender_id=req.sender_id,
        template_id=template_id,
        template_context=template_context or {},
        status="pending" if shadow_mode else "queued",
        shadow=shadow_mode,
        estimated_cost_eur=estimated_cost,
        segments=segments,
        metadata={"use_case": use_case} if use_case else {},
        created_by=created_by,
    )

    if shadow_mode:
        return {
            "sms_id": sms_id,
            "status": "pending",
            "mode": "shadow",
            "estimated_cost_eur": estimated_cost,
            "segments": segments,
            "preview_body": body,
            "to_phone": to_phone,
        }

    # Live
    try:
        result: SmsResult = await provider.send(req)
    except Exception as e:  # noqa: BLE001
        sms_log.update_status(sms_id, "failed", error=str(e))
        return {"sms_id": sms_id, "status": "failed", "error": str(e)}

    if result.status == "failed":
        sms_log.update_status(sms_id, "failed", error=result.error or "send failed")
        return {"sms_id": sms_id, "status": "failed", "error": result.error}

    sms_log.update_status(
        sms_id,
        result.status,
        provider_message_id=result.provider_message_id,
        cost_eur=result.cost_eur or estimated_cost,
        segments=result.segments,
        sent=result.status in ("sent", "delivered"),
        delivered=result.status == "delivered",
        metadata_patch={"provider_raw": _trim_dict(result.raw)},
    )

    return {
        "sms_id": sms_id,
        "status": result.status,
        "mode": "live",
        "provider_message_id": result.provider_message_id,
        "cost_eur": result.cost_eur or estimated_cost,
        "segments": result.segments,
    }


def _trim_dict(d: Optional[dict], max_keys: int = 30, max_str: int = 500) -> dict:
    if not d:
        return {}
    out: dict = {}
    for i, (k, v) in enumerate(d.items()):
        if i >= max_keys:
            break
        if isinstance(v, str) and len(v) > max_str:
            v = v[:max_str] + "…"
        out[str(k)] = v
    return out


# ── Validation shadow → live ──────────────────────────────────────

async def validate_pending(sms_id: int, decision: str, approved_by: str) -> dict:
    """Pascal valide / refuse un SMS shadow.
    decision = 'approve' | 'reject'.
    """
    if decision not in ("approve", "reject"):
        return {"error": f"décision invalide : {decision}"}
    rec = sms_log.get(sms_id)
    if not rec:
        return {"error": f"sms_id {sms_id} introuvable"}
    if rec.get("status") != "pending":
        return {"error": f"sms_id {sms_id} statut={rec.get('status')} (pas pending)"}

    if decision == "reject":
        sms_log.reject(sms_id, approved_by=approved_by, reason="rejected by reviewer")
        return {"sms_id": sms_id, "status": "rejected"}

    # approve → relance en mode live, en réutilisant les paramètres du log
    sms_log.approve(sms_id, approved_by=approved_by)
    return await send_sms(
        tenant_id=rec["tenant_id"],
        to_phone=rec["to_phone"],
        body=rec["body"],
        sender_id=rec.get("sender_id"),
        agent_id=rec.get("agent_id"),
        use_case=(rec.get("metadata") or {}).get("use_case"),
        force_live=True,
        created_by=approved_by,
    )


# ── Webhook handler (DLR) ─────────────────────────────────────────

def apply_webhook_event(provider_name: str, payload: dict) -> dict:
    """Reçoit un payload normalisé (déjà parsé par routes.py via le provider),
    retrouve le sms_logs row par provider_message_id, met à jour le statut.
    """
    pmid = (payload or {}).get("provider_message_id")
    if not pmid:
        return {"ok": False, "error": "no provider_message_id"}
    row = sms_log.get_by_provider_id(provider_name, pmid)
    if not row:
        return {"ok": False, "error": f"no sms_logs row with provider={provider_name} message_id={pmid}"}
    new_status = payload.get("status")
    if not new_status:
        return {"ok": True, "sms_id": row["id"], "noop": True}

    sms_log.update_status(
        row["id"],
        new_status,
        delivered=(new_status == "delivered"),
        sent=(new_status == "sent"),
        error=payload.get("error"),
        metadata_patch={"dlr": _trim_dict(payload)},
    )
    return {"ok": True, "sms_id": row["id"], "status": new_status}
