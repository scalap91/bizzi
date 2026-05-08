"""Phase 0 — vérifie que tout le scaffold comms s'importe sans erreur.

Note packaging : `/opt/bizzi/bizzi/` n'a pas de `__init__.py` racine.
Les sous-modules s'importent au top-level (cf. `from phone import routes`
dans api/main.py). On utilise donc `comms.*` et non `bizzi.comms.*`.
"""
from __future__ import annotations


def test_root_import():
    import comms  # noqa: F401


def test_sms_imports():
    from comms.sms import SmsProvider, SmsRequest, SmsResult  # noqa: F401
    from comms.sms.providers.twilio import TwilioSmsProvider  # noqa: F401
    from comms.sms.providers.ovh import OvhSmsProvider  # noqa: F401
    from comms.sms.providers.brevo import BrevoSmsProvider  # noqa: F401
    from comms.sms.routes import router as sms_router

    assert sms_router is not None


def test_mail_imports():
    from comms.mail import (  # noqa: F401
        MailProvider, MailRequest, MailResult, MailAttachment,
    )
    from comms.mail.providers.brevo import BrevoMailProvider  # noqa: F401
    from comms.mail.providers.sendgrid import SendgridMailProvider  # noqa: F401
    from comms.mail.routes import router as mail_router

    assert mail_router is not None


def test_inbound_imports():
    from comms.inbound import InboundHandler, Qualification, qualify  # noqa: F401
    from comms.inbound.providers.vapi import VapiInboundProvider  # noqa: F401
    from comms.inbound.providers.twilio import TwilioInboundProvider  # noqa: F401
    from comms.inbound.routes import router as inbound_router

    assert inbound_router is not None


def test_calendar_imports():
    from comms.calendar import (  # noqa: F401
        CalendarProvider, EventRequest, EventResult, AvailabilitySlot,
    )
    from comms.calendar.providers.google import GoogleCalendarProvider  # noqa: F401
    from comms.calendar.providers.outlook import OutlookCalendarProvider  # noqa: F401
    from comms.calendar.providers.doctolib import DoctolibCalendarProvider  # noqa: F401
    from comms.calendar.routes import router as calendar_router

    assert calendar_router is not None


def test_provider_abstract():
    """Les classes ABC doivent rester abstraites (instanciation directe impossible)."""
    import pytest

    from comms.sms.base import SmsProvider
    from comms.mail.base import MailProvider
    from comms.calendar.base import CalendarProvider

    for cls in (SmsProvider, MailProvider, CalendarProvider):
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]
