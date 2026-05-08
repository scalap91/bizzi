"""Pont `tools.email.email_agent` → `comms.mail.send_mail`.

Pourquoi ce pont :
- `tools/email/email_agent.py` (classification inbound) reste authoritative pour
  l'INBOUND. Pour ses auto-replies, il peut soit garder son SMTP brut, soit
  passer par `comms.mail` qui apporte :
    * traçabilité (table mail_logs : status, opens, clicks, bounces)
    * shadow-mode + budget + rate-limit + templates par tenant
    * unification provider (Brevo, SendGrid, …)

API exposée :
    send_via_comms_async(...)   # à utiliser depuis EmailAgent.process (async)
    send_via_comms(...)         # wrapper sync (compat EmailAgent.send_email)
    domain_name_to_tenant_id(name)  # lookup best-effort depuis tenants.name/slug

Le pont n'écrit RIEN dans tools/email — l'ancien code continue à exister tel quel.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from .. import orchestrator
from ..._db import get_conn

logger = logging.getLogger("comms.mail.bridges.tools_email")


# ── Tenant resolution depuis DomainConfig.name ───────────────────

def domain_name_to_tenant_id(domain_name: str) -> Optional[int]:
    """Best-effort : essaie name puis slug puis ILIKE. Retourne None si aucun match.

    DomainConfig de tools/email a un champ `name` (ex: "Les Démocrates") qui
    correspond souvent à `tenants.name` ou se simplifie en slug.
    """
    if not domain_name:
        return None
    candidates = [
        domain_name,
        domain_name.strip().lower().replace(" ", ""),
        domain_name.strip().lower().replace(" ", "-"),
        domain_name.strip().lower().replace(" ", "_"),
    ]
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM tenants WHERE slug = %s OR name = %s LIMIT 1",
                (c, c),
            )
            row = cur.fetchone()
            if row:
                return row[0]
    # ILIKE fallback
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM tenants WHERE name ILIKE %s OR slug ILIKE %s LIMIT 1",
            (f"%{domain_name}%", f"%{domain_name}%"),
        )
        row = cur.fetchone()
        return row[0] if row else None


# ── Send helpers ─────────────────────────────────────────────────

async def send_via_comms_async(
    *,
    tenant_id: int,
    to: str,
    subject: str,
    body: str,
    body_is_html: bool = False,
    use_case: str = "tools_email_autoreply",
    force_live: bool = True,
    agent_id: Optional[int] = None,
    reply_to: Optional[str] = None,
) -> bool:
    """Envoie un mail via comms.mail.send_mail (async).

    `force_live=True` par défaut : les auto-replies sortent immédiatement
    (l'EmailAgent décide déjà si une réponse doit partir). Mettre False pour
    passer en shadow-mode.
    """
    result = await orchestrator.send_mail(
        tenant_id=tenant_id,
        to=[to],
        subject=subject,
        text=None if body_is_html else body,
        html=body if body_is_html else None,
        reply_to=reply_to,
        use_case=use_case,
        agent_id=agent_id,
        force_live=force_live,
        created_by="tools.email.email_agent",
    )
    if "error" in result and "mail_id" not in result:
        logger.warning("[bridge tools_email] %s", result.get("error"))
        return False
    status = result.get("status")
    ok = status in ("sent", "delivered", "queued", "pending")
    logger.info(
        "[bridge tools_email] tenant=%s to=%s status=%s mail_id=%s",
        tenant_id, to, status, result.get("mail_id"),
    )
    return ok


def send_via_comms(
    *,
    tenant_id: int,
    to: str,
    subject: str,
    body: str,
    body_is_html: bool = False,
    use_case: str = "tools_email_autoreply",
    force_live: bool = True,
    agent_id: Optional[int] = None,
    reply_to: Optional[str] = None,
) -> bool:
    """Wrapper sync. Compat signature avec `EmailAgent.send_email(to, subject, body)`.

    Détecte automatiquement si on est déjà dans un event loop (cas de
    `EmailAgent.process()` qui est async) → exécute alors dans un thread
    avec son propre loop. Sinon `asyncio.run` direct.
    """
    kwargs = dict(
        tenant_id=tenant_id, to=to, subject=subject, body=body,
        body_is_html=body_is_html, use_case=use_case,
        force_live=force_live, agent_id=agent_id, reply_to=reply_to,
    )

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        return asyncio.run(send_via_comms_async(**kwargs))

    # Déjà dans un loop : nouveau thread + nouveau loop pour ne pas bloquer.
    box: dict = {"r": False, "exc": None}

    def _runner():
        try:
            box["r"] = asyncio.run(send_via_comms_async(**kwargs))
        except Exception as e:  # noqa: BLE001
            box["exc"] = e

    th = threading.Thread(target=_runner, name="comms-mail-bridge")
    th.start()
    th.join()
    if box["exc"]:
        logger.error("[bridge tools_email] sync wrapper exc: %s", box["exc"])
        return False
    return box["r"]
