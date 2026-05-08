"""bizzi.comms.calendar — Gestion RDV multi-provider (Phase 1).

Surface publique :
- Provider abstraction : CalendarProvider, EventRequest, EventResult, AvailabilitySlot
- Orchestrator : create_event, validate_pending, cancel_event, list_availability, build_provider
- Templates    : render, render_inline, list_templates, get_calendar_config, RenderedEvent
- Conflicts    : check_internal, ConflictReport
- Reminders    : run_due_reminders
- Logs DB      : module event_log

Ce sous-module shadow le stdlib `calendar`. Les imports doivent toujours
utiliser le chemin complet `comms.calendar.*`.
"""
from .base import (
    CalendarProvider, EventRequest, EventResult, AvailabilitySlot,
)
from .orchestrator import (
    create_event, validate_pending, cancel_event,
    list_availability, build_provider,
)
from .reminders import run_due_reminders
from .conflicts import check_internal, ConflictReport
from . import event_log, templates

__all__ = [
    "CalendarProvider", "EventRequest", "EventResult", "AvailabilitySlot",
    "create_event", "validate_pending", "cancel_event",
    "list_availability", "build_provider",
    "run_due_reminders",
    "check_internal", "ConflictReport",
    "event_log", "templates",
]
