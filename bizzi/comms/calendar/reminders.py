"""Rappels d'événements (J-1, H-1) — déclenche SMS et/ou mail.

Logique :
- Pour chaque event actif (status created/confirmed) avec start_at futur,
  on calcule pour chaque `m ∈ reminders_minutes` si `now + m ∈ [start_at - lookahead, start_at]`.
- Si oui ET si pas déjà envoyé (check `reminders_sent`), on envoie.

Channels :
- 'sms' : envoie un SMS au premier attendee dont la "phone" est connue (yaml ou metadata)
- 'mail': envoie un mail à chaque attendee (email = chaque entrée d'`attendees`)

Phase 1 : pas de scheduler intégré (cron / systemd timer côté ops). On expose
`run_due_reminders(...)` que le caller exécute périodiquement.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import _template
from . import event_log

logger = logging.getLogger("comms.calendar.reminders")


def _tenant_slug_from_id(tenant_id: int) -> Optional[str]:
    from .._db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else None


def _format_default_body(event: dict, channel: str) -> tuple[str, Optional[str], Optional[str]]:
    """Retourne (text, subject, html) selon le canal. Format simple, sûr, court."""
    title = event.get("title") or "RDV"
    start_local = event["start_at"]
    if isinstance(start_local, datetime):
        start_str = start_local.strftime("%d/%m/%Y %H:%M")
    else:
        start_str = str(start_local)
    location = event.get("location") or ""
    text = f"Rappel : {title} le {start_str}"
    if location:
        text += f" — {location}"
    if channel == "mail":
        subject = f"[Rappel] {title} — {start_str}"
        html = (
            f"<p>Bonjour,</p>"
            f"<p>Rappel de votre rendez-vous : <strong>{title}</strong></p>"
            f"<p>Date : {start_str}</p>"
            + (f"<p>Lieu : {location}</p>" if location else "")
            + "<p>À très vite.</p>"
        )
        return text, subject, html
    return text, None, None


def _already_sent(event: dict, minutes_before: int, channel: str) -> bool:
    sent = event.get("reminders_sent") or []
    for entry in sent:
        if (
            entry.get("minutes_before") == minutes_before
            and entry.get("channel") == channel
            and entry.get("ok") is True
        ):
            return True
    return False


def _due_this_tick(
    event: dict, *, now: datetime, lookahead_minutes: int,
) -> list[int]:
    """Renvoie la liste des reminders_minutes qui doivent être envoyés au tick courant.

    Un reminder de `m` minutes avant doit partir si `start_at - now ∈ [m - lookahead, m]`.
    Cad on est entre `m + lookahead` minutes avant et `m` minutes avant le start_at.
    """
    out: list[int] = []
    start_at = event.get("start_at")
    if not isinstance(start_at, datetime):
        return out
    delta_min = (start_at - now).total_seconds() / 60
    for m in event.get("reminders_minutes") or []:
        m = int(m)
        if delta_min <= m and delta_min >= (m - lookahead_minutes):
            out.append(m)
    return out


async def run_due_reminders(
    *,
    tenant_id: Optional[int] = None,
    lookahead_minutes: int = 5,
    channels: Optional[list[str]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Scanne les events actifs, envoie les reminders dûs.

    `channels` : par défaut lit depuis yaml `comms.calendar.reminder_channels`
    ('sms' et/ou 'mail'). Caller peut forcer.
    `now` : surchargeable pour tests.

    Retour : {scanned, sent: [{event_id, channel, ref_id?, ok}], skipped: [...]}.
    """
    now = now or datetime.now(timezone.utc)
    events = event_log.list_due_reminders(
        tenant_id=tenant_id, lookahead_minutes=lookahead_minutes,
    )

    sent: list[dict] = []
    skipped: list[dict] = []

    for ev in events:
        due = _due_this_tick(ev, now=now, lookahead_minutes=lookahead_minutes)
        if not due:
            continue

        slug = _tenant_slug_from_id(ev["tenant_id"])
        cal_cfg = {}
        if slug:
            cfg = _template.load_tenant_yaml(slug) or {}
            cal_cfg = ((cfg.get("comms") or {}).get("calendar") or {})

        ev_channels = channels or cal_cfg.get("reminder_channels") or ["mail"]

        for m in due:
            for chan in ev_channels:
                if _already_sent(ev, m, chan):
                    continue
                ok, ref_id, err = await _dispatch(
                    ev, channel=chan, minutes_before=m,
                    cal_cfg=cal_cfg,
                )
                entry = {
                    "minutes_before": m,
                    "channel": chan,
                    "sent_at": now.isoformat(),
                    "ref_id": ref_id,
                    "ok": ok,
                }
                if err:
                    entry["error"] = err
                event_log.append_reminder_sent(ev["id"], entry)
                (sent if ok else skipped).append({"event_id": ev["id"], **entry})

    return {"scanned": len(events), "sent": sent, "skipped": skipped, "now": now.isoformat()}


async def _dispatch(
    event: dict,
    *,
    channel: str,
    minutes_before: int,
    cal_cfg: dict,
) -> tuple[bool, Optional[int], Optional[str]]:
    """Envoie le reminder via le canal demandé. Retour (ok, ref_id, error?)."""
    text, subject, html = _format_default_body(event, channel)
    tenant_id = event["tenant_id"]
    attendees = list(event.get("attendees") or [])

    if channel == "sms":
        # Numéro destinataire : metadata.attendee_phone[0] ou attendees[0] si E.164
        md = event.get("metadata") or {}
        phones = md.get("attendee_phones") or []
        target = next((p for p in phones if isinstance(p, str) and p.startswith("+")), None)
        if not target:
            target = next((a for a in attendees if a.startswith("+")), None)
        if not target:
            return False, None, "no SMS recipient (metadata.attendee_phones[] manquant)"
        try:
            from ..sms import orchestrator as sms_orch
            res = await sms_orch.send_sms(
                tenant_id=tenant_id,
                to_phone=target,
                body=text,
                use_case=f"calendar_reminder_{minutes_before}min",
            )
        except Exception as e:  # noqa: BLE001
            return False, None, f"sms exc: {e}"
        if "error" in res and "sms_id" not in res:
            return False, None, res.get("error")
        return True, int(res.get("sms_id") or 0), None

    if channel == "mail":
        if not attendees:
            return False, None, "no mail recipient (attendees vide)"
        emails = [a for a in attendees if "@" in a]
        if not emails:
            return False, None, "aucune adresse email valide"
        try:
            from ..mail import orchestrator as mail_orch
            res = await mail_orch.send_mail(
                tenant_id=tenant_id,
                to=emails,
                subject=subject or "Rappel RDV",
                text=text,
                html=html,
                use_case=f"calendar_reminder_{minutes_before}min",
                force_live=True,
            )
        except Exception as e:  # noqa: BLE001
            return False, None, f"mail exc: {e}"
        if "error" in res and "mail_id" not in res:
            return False, None, res.get("error")
        return True, int(res.get("mail_id") or 0), None

    return False, None, f"channel inconnu : {channel}"
