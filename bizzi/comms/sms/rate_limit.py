"""Rate limit anti-flood SMS, deux niveaux :

- per_tenant_per_hour : nombre max de SMS envoyés (status sent/delivered) /h pour le tenant
- per_phone_per_day   : nombre max de SMS envoyés à un même numéro / 24h

Évalué sur sms_logs. Pas de table dédiée → simple, suffisant pour un volume
réaliste (<10k SMS/h/tenant).

Limites par défaut (override via yaml `comms.sms.rate_limit`):
  per_tenant_per_hour: 200
  per_phone_per_day:   3
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .._db import get_conn

DEFAULT_PER_TENANT_PER_HOUR = 200
DEFAULT_PER_PHONE_PER_DAY = 3


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = ""
    tenant_count_last_hour: int = 0
    phone_count_last_day: int = 0


def _counts(tenant_id: int, to_phone: str) -> tuple[int, int]:
    """Retourne (tenant_last_hour, phone_last_day) en comptant pending/queued/sent/delivered.

    On compte aussi pending pour éviter qu'un flood en shadow ne dépasse les
    quotas une fois validé.
    """
    counted = ("pending", "approved", "queued", "sent", "delivered")
    placeholders = ",".join(["%s"] * len(counted))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT COUNT(*) FROM sms_logs
                WHERE tenant_id = %s
                  AND status IN ({placeholders})
                  AND created_at > now() - interval '1 hour'""",
            (tenant_id, *counted),
        )
        tenant_count = int(cur.fetchone()[0] or 0)
        cur.execute(
            f"""SELECT COUNT(*) FROM sms_logs
                WHERE tenant_id = %s AND to_phone = %s
                  AND status IN ({placeholders})
                  AND created_at > now() - interval '24 hours'""",
            (tenant_id, to_phone, *counted),
        )
        phone_count = int(cur.fetchone()[0] or 0)
        return tenant_count, phone_count


def check(
    tenant_id: int,
    to_phone: str,
    *,
    per_tenant_per_hour: Optional[int] = None,
    per_phone_per_day: Optional[int] = None,
) -> RateLimitDecision:
    th = per_tenant_per_hour if per_tenant_per_hour is not None else DEFAULT_PER_TENANT_PER_HOUR
    pd = per_phone_per_day   if per_phone_per_day   is not None else DEFAULT_PER_PHONE_PER_DAY
    tenant_count, phone_count = _counts(tenant_id, to_phone)
    if tenant_count >= th:
        return RateLimitDecision(
            allowed=False,
            reason=f"rate_limit tenant : {tenant_count}/h ≥ {th}",
            tenant_count_last_hour=tenant_count,
            phone_count_last_day=phone_count,
        )
    if phone_count >= pd:
        return RateLimitDecision(
            allowed=False,
            reason=f"rate_limit numéro {to_phone} : {phone_count}/24h ≥ {pd}",
            tenant_count_last_hour=tenant_count,
            phone_count_last_day=phone_count,
        )
    return RateLimitDecision(
        allowed=True,
        tenant_count_last_hour=tenant_count,
        phone_count_last_day=phone_count,
    )
