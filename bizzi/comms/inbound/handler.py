"""Handler des appels téléphoniques entrants.

Flow Vapi :
  1) provider envoie status-update events (ringing / in-progress) → on (re)log
  2) à la fin, end-of-call-report avec transcript+summary+recording
  3) on qualifie le transcript (LLM Ollama)
  4) on route : sms_confirm → comms.sms ; mail_summary → comms.mail ;
     transfer/rdv/ticket → flag requires_human (Phase 2 = transfer Vapi/calendar)

Flow Twilio Voice :
  1) /twiml renvoie un XML basique (voicemail/forward)
  2) status callbacks → log + finalize si completed

Le handler ne lève **jamais** : un appel entrant qui plante = donnée perdue.
On capte tout, on log les actions ratées en metadata.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .. import _db, _template
from . import inbound_log, qualifier as qualifier_mod

logger = logging.getLogger("comms.inbound.handler")


# ── Tenant resolution ─────────────────────────────────────────────

def _tenant_id_from_to_phone(to_phone: str) -> Optional[int]:
    """Cherche le tenant dont le yaml a `comms.inbound.to_phone == to_phone`.

    Phase 1 simplifiée : on scanne tous les yaml de domains/. Pour un volume
    important, indexer en DB. Cache géré par _template.load_tenant_yaml.
    """
    import os
    if not to_phone:
        return None
    try:
        files = [f for f in os.listdir(_template.YAML_DIR) if f.endswith(".yaml")]
    except FileNotFoundError:
        return None
    matches: list[str] = []
    for f in files:
        slug = f[:-5]
        try:
            cfg = _template.load_tenant_yaml(slug)
        except (OSError, Exception):  # noqa: BLE001
            continue
        inbound_cfg = ((cfg.get("comms") or {}).get("inbound") or {})
        if inbound_cfg.get("to_phone") == to_phone:
            matches.append(slug)
    if not matches:
        return None
    # Premier match — on assume unicité numéro tenant
    slug = matches[0]
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return row[0] if row else None


def _tenant_slug_from_id(tenant_id: int) -> Optional[str]:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else None


def load_inbound_config(tenant_slug: str) -> dict:
    cfg = _template.load_tenant_yaml(tenant_slug) or {}
    return ((cfg.get("comms") or {}).get("inbound") or {})


# ── Handler core ──────────────────────────────────────────────────

async def handle_event(provider_name: str, normalized: dict) -> dict:
    """Aiguillage selon event_type. Retour {ok, action, call_id?, …}.

    `normalized` est issu de `<Provider>.parse_webhook(...)`.
    """
    event_type = normalized.get("event_type")
    if event_type == "ignored":
        return {"ok": True, "noop": True, "raw_type": normalized.get("raw_type")}

    if event_type == "assistant_request":
        # Phase 2 : retourner dynamiquement un assistant. Phase 1 : on laisse
        # Vapi utiliser celui configuré côté tenant (vapi_assistant_id_inbound).
        return {"ok": True, "action": "use_static_assistant"}

    if event_type == "status_update":
        return await _handle_status_update(provider_name, normalized)

    if event_type == "end_of_call":
        return await _handle_end_of_call(provider_name, normalized)

    return {"ok": False, "error": f"event_type inconnu: {event_type}"}


async def _resolve_tenant(normalized: dict) -> Optional[int]:
    """Tenant via metadata (Vapi metadata) puis fallback to_phone match yaml."""
    md = normalized.get("metadata") or {}
    if "tenant_id" in md:
        try:
            return int(md["tenant_id"])
        except (TypeError, ValueError):
            pass
    return _tenant_id_from_to_phone(normalized.get("to_phone") or "")


async def _handle_status_update(provider_name: str, n: dict) -> dict:
    pmid = n.get("provider_call_id")
    if not pmid:
        return {"ok": False, "error": "no provider_call_id"}

    existing = inbound_log.get_by_provider_id(provider_name, pmid)
    if existing:
        inbound_log.update_call(existing["id"], status=n.get("status") or existing.get("status"))
        return {"ok": True, "call_id": existing["id"], "action": "status_updated"}

    tenant_id = await _resolve_tenant(n)
    if not tenant_id:
        logger.warning(
            "inbound: appel status-update sans tenant résolu (to=%s, provider_call_id=%s)",
            n.get("to_phone"), pmid,
        )
        return {"ok": False, "error": "tenant non résolu"}

    call_id = inbound_log.log_call(
        tenant_id=tenant_id,
        provider=provider_name,
        provider_call_id=pmid,
        from_phone=n.get("from_phone"),
        to_phone=n.get("to_phone"),
        status=n.get("status") or "received",
        started_at=datetime.now(timezone.utc),
        metadata={"first_event": "status_update"},
    )
    return {"ok": True, "call_id": call_id, "action": "logged"}


async def _handle_end_of_call(provider_name: str, n: dict) -> dict:
    pmid = n.get("provider_call_id")
    if not pmid:
        return {"ok": False, "error": "no provider_call_id"}

    # Trouve ou crée la row
    existing = inbound_log.get_by_provider_id(provider_name, pmid)
    if existing:
        call_id = existing["id"]
        tenant_id = existing["tenant_id"]
    else:
        tenant_id = await _resolve_tenant(n)
        if not tenant_id:
            logger.warning(
                "inbound: end-of-call sans tenant résolu (to=%s, provider_call_id=%s)",
                n.get("to_phone"), pmid,
            )
            return {"ok": False, "error": "tenant non résolu"}
        call_id = inbound_log.log_call(
            tenant_id=tenant_id,
            provider=provider_name,
            provider_call_id=pmid,
            from_phone=n.get("from_phone"),
            to_phone=n.get("to_phone"),
            status="received",
            started_at=n.get("answered_at") or datetime.now(timezone.utc),
            metadata={"first_event": "end_of_call"},
        )

    # Update transcript / summary / recording / cost
    inbound_log.update_call(
        call_id,
        status=n.get("status") or "completed",
        answered_at=n.get("answered_at"),
        ended_at=n.get("ended_at") or datetime.now(timezone.utc),
        duration_seconds=n.get("duration_seconds"),
        recording_url=n.get("recording_url"),
        transcript=n.get("transcript"),
        summary=n.get("summary"),
        cost_eur=n.get("cost_eur"),
        error=n.get("error"),
    )

    # Qualify (LLM)
    slug = _tenant_slug_from_id(tenant_id)
    inbound_cfg = load_inbound_config(slug) if slug else {}
    qualifier_cfg = inbound_cfg.get("qualifier") or {}
    qualification = await qualifier_mod.qualify(
        n.get("transcript") or [],
        tenant_persona={"name": (inbound_cfg.get("greeting") or "")[:80]},
        model=qualifier_cfg.get("model") or qualifier_mod.DEFAULT_MODEL,
        ollama_url=qualifier_cfg.get("ollama_url") or qualifier_mod.DEFAULT_OLLAMA_URL,
        timeout_sec=int(qualifier_cfg.get("timeout_sec") or qualifier_mod.DEFAULT_TIMEOUT),
        enabled=bool(qualifier_cfg.get("enabled", True)),
    )

    inbound_log.update_qualification(
        call_id,
        intent=qualification.intent,
        urgency=qualification.urgency,
        suggested_action=qualification.suggested_action,
        extracted=qualification.extracted,
        confidence=qualification.confidence,
        requires_human=qualification.requires_human,
    )
    # On stocke aussi le summary qualifier dans la colonne summary si vide
    if not n.get("summary") and qualification.summary:
        inbound_log.update_call(call_id, summary=qualification.summary)

    # Route action
    action_result = await _route_action(
        call_id=call_id,
        tenant_id=tenant_id,
        from_phone=n.get("from_phone"),
        qualification=qualification,
        inbound_cfg=inbound_cfg,
    )
    return {
        "ok": True,
        "call_id": call_id,
        "action": "finalized",
        "qualification": qualification.to_dict(),
        "routing": action_result,
    }


# ── Routing ───────────────────────────────────────────────────────

async def _route_action(
    *,
    call_id: int,
    tenant_id: int,
    from_phone: Optional[str],
    qualification: qualifier_mod.Qualification,
    inbound_cfg: dict,
) -> dict:
    action = qualification.suggested_action
    routed: dict = {"action": action, "executed": [], "skipped": []}

    auto_sms = bool(inbound_cfg.get("auto_sms_confirm", False))
    auto_mail = bool(inbound_cfg.get("auto_mail_summary", False))
    admin_email = inbound_cfg.get("admin_email")

    # Toujours envoyer un summary admin si configuré (utile pour l'oncall)
    if auto_mail and admin_email:
        ok = await _send_admin_summary(
            tenant_id=tenant_id,
            admin_email=admin_email,
            from_phone=from_phone,
            qualification=qualification,
            call_id=call_id,
        )
        ev = {"type": "mail_summary", "to": admin_email, "ok": ok}
        inbound_log.append_action(call_id, ev)
        (routed["executed"] if ok else routed["skipped"]).append(ev)

    # SMS confirmation au caller — uniquement si suggéré ET autorisé ET numéro valide
    if action == "sms_confirm" and auto_sms and _is_valid_e164(from_phone):
        ok = await _send_caller_sms(
            tenant_id=tenant_id,
            to_phone=from_phone,  # type: ignore[arg-type]
            qualification=qualification,
            inbound_cfg=inbound_cfg,
            call_id=call_id,
        )
        ev = {"type": "sms_sent", "to": from_phone, "ok": ok}
        inbound_log.append_action(call_id, ev)
        (routed["executed"] if ok else routed["skipped"]).append(ev)
    elif action == "sms_confirm":
        ev = {"type": "sms_sent", "ok": False, "reason": "auto_sms_confirm=false ou numéro invalide"}
        routed["skipped"].append(ev)

    # transfer / rdv → Phase 2 (calendar + transfer Vapi). Phase 1 : flag requires_human.
    if action in ("transfer", "rdv"):
        ev = {"type": action, "ok": False, "reason": "Phase 2 — flagged requires_human"}
        inbound_log.append_action(call_id, ev)
        routed["skipped"].append(ev)

    if action == "ticket":
        ev = {"type": "ticket", "ok": True}
        inbound_log.append_action(call_id, ev)
        routed["executed"].append(ev)

    return routed


def _is_valid_e164(phone: Optional[str]) -> bool:
    return bool(phone) and phone.startswith("+") and phone[1:].isdigit() and len(phone) >= 8  # type: ignore[union-attr]


async def _send_caller_sms(
    *,
    tenant_id: int,
    to_phone: str,
    qualification: qualifier_mod.Qualification,
    inbound_cfg: dict,
    call_id: int,
) -> bool:
    """Envoie un SMS de suivi au caller via comms.sms.

    Utilise template `post_call_confirm` du tenant si dispo, sinon fallback fixe.
    """
    try:
        from ..sms import orchestrator as sms_orch
    except Exception as e:  # noqa: BLE001
        logger.error("inbound→sms: import comms.sms KO: %s", e)
        return False

    template_id = inbound_cfg.get("sms_template_id") or "post_call_confirm"
    fallback_body = (
        f"Bonjour, suite à votre appel : {qualification.summary or 'message bien reçu'}. "
        f"Nous reviendrons vers vous rapidement."
    )[:320]

    # Tente d'abord le template, sinon body direct
    result = await sms_orch.send_sms(
        tenant_id=tenant_id,
        to_phone=to_phone,
        template_id=template_id,
        template_context={
            "summary": qualification.summary,
            "intent": qualification.intent,
        },
        use_case=f"inbound_followup_{qualification.intent}",
    )
    if "error" in result and "sms_id" not in result:
        # fallback body si le template n'existe pas
        result = await sms_orch.send_sms(
            tenant_id=tenant_id,
            to_phone=to_phone,
            body=fallback_body,
            use_case=f"inbound_followup_{qualification.intent}",
        )
    if "error" in result and "sms_id" not in result:
        logger.warning("inbound→sms: échec envoi (call_id=%s): %s", call_id, result.get("error"))
        return False
    return True


async def _send_admin_summary(
    *,
    tenant_id: int,
    admin_email: str,
    from_phone: Optional[str],
    qualification: qualifier_mod.Qualification,
    call_id: int,
) -> bool:
    """Envoie un mail de résumé d'appel à l'admin via comms.mail."""
    try:
        from ..mail import orchestrator as mail_orch
    except Exception as e:  # noqa: BLE001
        logger.error("inbound→mail: import comms.mail KO: %s", e)
        return False

    subject = f"[Appel entrant] {qualification.intent.upper()} — urgence {qualification.urgency}"
    text = (
        f"Appel entrant traité par Bizzi (call_id={call_id})\n\n"
        f"De : {from_phone or 'inconnu'}\n"
        f"Intent : {qualification.intent}\n"
        f"Urgence : {qualification.urgency}/3\n"
        f"Action suggérée : {qualification.suggested_action}\n"
        f"Confidence : {qualification.confidence:.2f}\n"
        f"Requires human : {qualification.requires_human}\n\n"
        f"Résumé :\n{qualification.summary or '(aucun)'}\n\n"
        f"Faits extraits : {qualification.extracted}\n"
    )
    result = await mail_orch.send_mail(
        tenant_id=tenant_id,
        to=[admin_email],
        subject=subject,
        text=text,
        use_case=f"inbound_admin_summary_{qualification.intent}",
        force_live=True,
    )
    if "error" in result and "mail_id" not in result:
        logger.warning("inbound→mail: échec envoi admin (call_id=%s): %s", call_id, result.get("error"))
        return False
    return True
