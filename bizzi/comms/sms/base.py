"""Interface abstraite des providers SMS. Pattern miroir de phone/provider.py."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SmsRequest:
    tenant_id: int
    to_phone: str            # E.164 ex: "+33612345678"
    body: str                # texte du SMS (≤ 160 chars idéal)
    sender_id: Optional[str] = None  # alphanumeric ou MSISDN selon provider
    template_id: Optional[str] = None
    template_context: dict = field(default_factory=dict)
    agent_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class SmsResult:
    provider_message_id: str
    status: str              # queued | sent | delivered | failed | rejected
    cost_eur: float = 0.0
    segments: int = 1
    error: Optional[str] = None
    delivered_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict)


class SmsProvider(ABC):
    name: str  # 'twilio' | 'ovh' | 'brevo' | 'vonage'

    @abstractmethod
    async def send(self, req: SmsRequest) -> SmsResult: ...

    @abstractmethod
    def estimate_cost(self, req: SmsRequest) -> float: ...

    @abstractmethod
    def health_check(self) -> dict: ...
