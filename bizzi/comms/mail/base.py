"""Interface abstraite des providers mail. Pattern miroir de phone/provider.py."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class MailAttachment:
    filename: str
    content_b64: Optional[str] = None      # base64 inline
    url: Optional[str] = None              # ou URL distante (provider-side fetch)
    content_type: str = "application/octet-stream"


@dataclass
class MailRequest:
    tenant_id: int
    to: list[str]
    subject: str
    html: Optional[str] = None
    text: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    attachments: list[MailAttachment] = field(default_factory=list)
    template_id: Optional[str] = None
    template_context: dict = field(default_factory=dict)
    track_opens: bool = True
    track_clicks: bool = True
    agent_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class MailResult:
    provider_message_id: str
    status: str              # queued | sent | delivered | bounced | complained | failed
    error: Optional[str] = None
    delivered_at: Optional[datetime] = None
    opens: int = 0
    clicks: int = 0
    raw: dict = field(default_factory=dict)


class MailProvider(ABC):
    name: str  # 'brevo' | 'sendgrid' | 'mailgun' | 'ses'

    @abstractmethod
    async def send(self, req: MailRequest) -> MailResult: ...

    @abstractmethod
    async def fetch_status(self, provider_message_id: str) -> dict: ...

    @abstractmethod
    def health_check(self) -> dict: ...
