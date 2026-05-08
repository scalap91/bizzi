"""Orchestrator mail : glue entre yaml tenant, rate limit, provider et mail_logs.

Pattern miroir de bizzi.comms.sms.orchestrator.

Différences SMS → mail :
- Rendu template = (subject, html, text)
- Rate limit par destinataire (1er to_addr) — couvre 95% des cas (broadcast = bcc)
- Webhooks : opens/clicks → increment compteur, ne change pas le status
"""
from __future__ import annotations

from typing import Optional

from .. import _db, _template
from . import mail_log, rate_limit, templates as templates_mod
from .base import MailAttachment, MailProvider, MailRequest, MailResult


# ── Tenant resolution ─────────────────────────────────────────────

def _tenant_slug_from_id(tenant_id: int) -> Optional[str]:
    with _db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _load_tenant_yaml(tenant_slug: str) -> dict:
    try:
        return _template.load_tenant_yaml(tenant_slug)
    except FileNotFoundError:
        return {}


# ── Provider factory ──────────────────────────────────────────────

def build_provider(mail_cfg: dict) -> MailProvider:
    name = (mail_cfg.get("provider") or "brevo").lower()
    if name == "brevo":
        from .providers.brevo import BrevoMailProvider
        return BrevoMailProvider(
            api_key=mail_cfg.get("brevo_api_key"),
            default_from_email=mail_cfg.get("from_email"),
            default_from_name=mail_cfg.get("from_name"),
        )
    if name == "sendgrid":
        from .providers.sendgrid import SendgridMailProvider
        return SendgridMailProvider(
            api_key=mail_cfg.get("sendgrid_api_key"),
            default_from_email=mail_cfg.get("from_email"),
            default_from_name=mail_cfg.get("from_name"),
        )
    raise ValueError(f"provider mail inconnu : {name}")


# ── Validation utils ──────────────────────────────────────────────

_EMAIL_BASIC = lambda e: isinstance(e, str) and "@" in e and "." in e.split("@")[-1]


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


def _attachments_meta(attachments: list[MailAttachment]) -> list[dict]:
    out = []
    for a in attachments:
        meta = {"filename": a.filename, "content_type": a.content_type}
        if a.content_b64:
            # rough estimate : len(b64) * 3/4 ≈ size décodée
            meta["size_bytes"] = max(0, (len(a.content_b64) * 3) // 4)
        if a.url:
            meta["url"] = a.url
        out.append(meta)
    return out


# ── Orchestration ─────────────────────────────────────────────────

async def send_mail(
    *,
    tenant_id: int,
    to: list[str],
    subject: Optional[str] = None,
    html: Optional[str] = None,
    text: Optional[str] = None,
    template_id: Optional[str] = None,
    template_context: Optional[dict] = None,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: Optional[list[MailAttachment]] = None,
    track_opens: Optional[bool] = None,
    track_clicks: Optional[bool] = None,
    agent_id: Optional[int] = None,
    use_case: Optional[str] = None,
    force_live: bool = False,
    created_by: Optional[str] = None,
) -> dict:
    """Point d'entrée principal pour envoyer un mail.

    Retour : {mail_id, status, mode: 'shadow'|'live', error?, ...}
    """
    slug = _tenant_slug_from_id(tenant_id)
    if not slug:
        return {"error": f"tenant_id {tenant_id} introuvable"}

    tenant_cfg = _load_tenant_yaml(slug)
    mail_cfg = ((tenant_cfg.get("comms") or {}).get("mail") or {})
    if not mail_cfg.get("enabled"):
        return {"error": "comms.mail non activé pour ce tenant (yaml: comms.mail.enabled)"}

    # Destinataires
    if not to or not isinstance(to, list):
        return {"error": "to (liste) requis"}
    for e in to + (cc or []) + (bcc or []):
        if not _EMAIL_BASIC(e):
            return {"error": f"adresse invalide : {e!r}"}

    # Subject + corps : direct ou template
    if template_id:
        try:
            rendered = templates_mod.render(slug, template_id, template_context or {})
        except KeyError as e:
            return {"error": str(e)}
        except ValueError as e:
            return {"error": f"template render: {e}"}
        subject = subject or rendered.subject
        html = html if html is not None else rendered.html
        text = text if text is not None else rendered.text

    if not subject:
        return {"error": "subject requis (ou via template)"}
    if not (html or text):
        return {"error": "html ou text requis (ou via template)"}

    attachments = attachments or []

    # Defaults from yaml
    from_email = from_email or mail_cfg.get("from_email")
    from_name = from_name or mail_cfg.get("from_name")
    reply_to = reply_to or mail_cfg.get("reply_to")
    if track_opens is None:
        track_opens = bool(mail_cfg.get("track_opens", True))
    if track_clicks is None:
        track_clicks = bool(mail_cfg.get("track_clicks", True))

    # Provider
    try:
        provider = build_provider(mail_cfg)
    except Exception as e:  # noqa: BLE001
        return {"error": f"provider init: {e}"}

    # Rate limit (premier destinataire)
    rl_cfg = mail_cfg.get("rate_limit") or {}
    rl = rate_limit.check(
        tenant_id, to[0],
        per_tenant_per_hour=rl_cfg.get("per_tenant_per_hour"),
        per_email_per_day=rl_cfg.get("per_email_per_day"),
    )
    if not rl.allowed:
        return {"error": rl.reason}

    # Coût estimé : Brevo ~0.0007€/mail, SendGrid ~0.001€/mail.
    estimated_cost = 0.001 if provider.name == "sendgrid" else 0.0007

    # Budget mensuel
    budget = float(mail_cfg.get("monthly_budget_eur") or 0)
    if budget > 0:
        spent = mail_log.get_month_spent_eur(tenant_id)
        if spent + estimated_cost > budget:
            return {"error": f"budget mail dépassé : {spent:.2f}€ + {estimated_cost:.4f}€ > {budget:.2f}€"}

    shadow_mode = bool(mail_cfg.get("shadow_mode", True)) and not force_live

    # Log d'abord (pending ou queued)
    mail_id = mail_log.log_mail(
        tenant_id=tenant_id,
        agent_id=agent_id,
        to_addrs=to, cc_addrs=cc, bcc_addrs=bcc,
        from_email=from_email, from_name=from_name, reply_to=reply_to,
        subject=subject, html=html, text=text,
        template_id=template_id, template_context=template_context or {},
        attachments_meta=_attachments_meta(attachments),
        track_opens=track_opens, track_clicks=track_clicks,
        provider=provider.name,
        status="pending" if shadow_mode else "queued",
        shadow=shadow_mode,
        estimated_cost_eur=estimated_cost,
        metadata={"use_case": use_case} if use_case else {},
        created_by=created_by,
    )

    if shadow_mode:
        return {
            "mail_id": mail_id,
            "status": "pending",
            "mode": "shadow",
            "estimated_cost_eur": estimated_cost,
            "preview": {
                "subject": subject,
                "to": to,
                "from_email": from_email,
                "html_len": len(html or ""),
                "text_len": len(text or ""),
                "attachments": len(attachments),
            },
        }

    # Live
    req = MailRequest(
        tenant_id=tenant_id,
        to=list(to), cc=list(cc or []), bcc=list(bcc or []),
        subject=subject, html=html, text=text,
        from_email=from_email, from_name=from_name, reply_to=reply_to,
        attachments=attachments,
        template_id=template_id, template_context=template_context or {},
        track_opens=track_opens, track_clicks=track_clicks,
        agent_id=agent_id,
        metadata={"use_case": use_case} if use_case else {},
    )
    try:
        result: MailResult = await provider.send(req)
    except Exception as e:  # noqa: BLE001
        mail_log.update_status(mail_id, "failed", error=str(e))
        return {"mail_id": mail_id, "status": "failed", "error": str(e)}

    if result.status == "failed":
        mail_log.update_status(mail_id, "failed", error=result.error or "send failed")
        return {"mail_id": mail_id, "status": "failed", "error": result.error}

    mail_log.update_status(
        mail_id,
        result.status,
        provider_message_id=result.provider_message_id,
        cost_eur=estimated_cost,
        sent=result.status in ("sent", "delivered"),
        delivered=result.status == "delivered",
        metadata_patch={"provider_raw": _trim_dict(result.raw)},
    )

    return {
        "mail_id": mail_id,
        "status": result.status,
        "mode": "live",
        "provider_message_id": result.provider_message_id,
        "cost_eur": estimated_cost,
    }


# ── Validation shadow → live ──────────────────────────────────────

async def validate_pending(mail_id: int, decision: str, approved_by: str) -> dict:
    if decision not in ("approve", "reject"):
        return {"error": f"décision invalide : {decision}"}
    rec = mail_log.get(mail_id)
    if not rec:
        return {"error": f"mail_id {mail_id} introuvable"}
    if rec.get("status") != "pending":
        return {"error": f"mail_id {mail_id} statut={rec.get('status')} (pas pending)"}

    if decision == "reject":
        mail_log.reject(mail_id, approved_by=approved_by, reason="rejected by reviewer")
        return {"mail_id": mail_id, "status": "rejected"}

    mail_log.approve(mail_id, approved_by=approved_by)
    return await send_mail(
        tenant_id=rec["tenant_id"],
        to=list(rec["to_addrs"]),
        cc=list(rec.get("cc_addrs") or []),
        bcc=list(rec.get("bcc_addrs") or []),
        subject=rec["subject"],
        html=rec.get("html"),
        text=rec.get("text"),
        from_email=rec.get("from_email"),
        from_name=rec.get("from_name"),
        reply_to=rec.get("reply_to"),
        agent_id=rec.get("agent_id"),
        use_case=(rec.get("metadata") or {}).get("use_case"),
        track_opens=rec.get("track_opens", True),
        track_clicks=rec.get("track_clicks", True),
        force_live=True,
        created_by=approved_by,
    )


# ── Webhook handler (DLR + opens/clicks) ──────────────────────────

def apply_webhook_event(provider_name: str, payload: dict) -> dict:
    """Reçoit un payload normalisé (parsé par routes.py via le provider).

    - opens/clicks → incrémente le compteur (status inchangé)
    - sent/delivered/bounced/failed/etc. → update_status
    """
    pmid = (payload or {}).get("provider_message_id")
    if not pmid:
        return {"ok": False, "error": "no provider_message_id"}
    row = mail_log.get_by_provider_id(provider_name, pmid)
    if not row:
        return {"ok": False, "error": f"no mail_logs row with provider={provider_name} message_id={pmid}"}

    if payload.get("opened"):
        mail_log.increment_open(row["id"])
        return {"ok": True, "mail_id": row["id"], "event": "opened"}
    if payload.get("clicked"):
        mail_log.increment_click(row["id"])
        return {"ok": True, "mail_id": row["id"], "event": "clicked"}

    new_status = payload.get("status")
    if not new_status or new_status in ("opened", "clicked"):
        return {"ok": True, "mail_id": row["id"], "noop": True}

    mail_log.update_status(
        row["id"],
        new_status,
        sent=(new_status == "sent"),
        delivered=(new_status == "delivered"),
        bounced=(new_status == "bounced"),
        error=payload.get("error"),
        metadata_patch={"dlr": _trim_dict(payload)},
    )
    return {"ok": True, "mail_id": row["id"], "status": new_status}
