"""bizzi.comms.mail — Envoi mail sortant multi-provider (Phase 1).

Surface publique :
- Provider abstraction : MailProvider, MailRequest, MailResult, MailAttachment
- Orchestrator : send_mail, validate_pending, apply_webhook_event, build_provider
- Templates    : render, render_inline, list_templates, get_mail_config, RenderedMail
- Rate limit   : check, RateLimitDecision
- Logs DB      : module mail_log
"""
from .base import MailProvider, MailRequest, MailResult, MailAttachment
from .orchestrator import (
    send_mail, validate_pending, apply_webhook_event, build_provider,
)
from . import mail_log, templates, rate_limit, bridges

__all__ = [
    "MailProvider", "MailRequest", "MailResult", "MailAttachment",
    "send_mail", "validate_pending", "apply_webhook_event", "build_provider",
    "mail_log", "templates", "rate_limit", "bridges",
]
