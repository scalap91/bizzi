"""Rate limit anti-flood mail, deux niveaux :

- per_tenant_per_hour : nombre max de mails /h pour le tenant (défaut 1000)
- per_email_per_day   : nombre max de mails à un même destinataire / 24h (défaut 5)

Évalué sur mail_logs (count_recent_*). Pas de table dédiée.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import mail_log

DEFAULT_PER_TENANT_PER_HOUR = 1000
DEFAULT_PER_EMAIL_PER_DAY = 5


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = ""
    tenant_count_last_hour: int = 0
    email_count_last_day: int = 0


def _counts(tenant_id: int, to_email: str) -> tuple[int, int]:
    return (
        mail_log.count_recent_for_tenant(tenant_id, hours=1),
        mail_log.count_recent_for_email(tenant_id, to_email, hours=24),
    )


def check(
    tenant_id: int,
    to_email: str,
    *,
    per_tenant_per_hour: Optional[int] = None,
    per_email_per_day: Optional[int] = None,
) -> RateLimitDecision:
    th = per_tenant_per_hour if per_tenant_per_hour is not None else DEFAULT_PER_TENANT_PER_HOUR
    pd = per_email_per_day   if per_email_per_day   is not None else DEFAULT_PER_EMAIL_PER_DAY
    tenant_count, email_count = _counts(tenant_id, to_email)
    if tenant_count >= th:
        return RateLimitDecision(
            allowed=False,
            reason=f"rate_limit tenant : {tenant_count}/h ≥ {th}",
            tenant_count_last_hour=tenant_count,
            email_count_last_day=email_count,
        )
    if email_count >= pd:
        return RateLimitDecision(
            allowed=False,
            reason=f"rate_limit destinataire {to_email} : {email_count}/24h ≥ {pd}",
            tenant_count_last_hour=tenant_count,
            email_count_last_day=email_count,
        )
    return RateLimitDecision(
        allowed=True,
        tenant_count_last_hour=tenant_count,
        email_count_last_day=email_count,
    )
