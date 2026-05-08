"""bizzi.comms.sms — Envoi SMS sortant multi-provider (Phase 1).

Surface publique :
- Provider abstraction : SmsProvider, SmsRequest, SmsResult
- Orchestrator : send_sms, validate_pending, apply_webhook_event, build_provider
- Templates    : render, render_inline, list_templates, get_sms_config
- Rate limit   : check, RateLimitDecision
- Logs DB      : module sms_log
"""
from .base import SmsProvider, SmsRequest, SmsResult
from .orchestrator import (
    send_sms, validate_pending, apply_webhook_event, build_provider,
)
from . import sms_log, templates, rate_limit

__all__ = [
    "SmsProvider", "SmsRequest", "SmsResult",
    "send_sms", "validate_pending", "apply_webhook_event", "build_provider",
    "sms_log", "templates", "rate_limit",
]
