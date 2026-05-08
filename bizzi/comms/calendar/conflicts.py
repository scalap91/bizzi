"""Détection de conflits d'agenda.

Phase 1 : check interne via DB (calendar_events). Provider-side check
(freebusy Google / getSchedule Outlook) reste optionnel et est délégué au
caller (orchestrator passe `check_external=True`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from . import event_log


@dataclass
class ConflictReport:
    has_conflict: bool
    overlapping_events: list[dict]


def check_internal(
    tenant_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    organizer_email: Optional[str] = None,
    exclude_event_id: Optional[int] = None,
) -> ConflictReport:
    """Cherche en DB les événements actifs qui chevauchent [start_at, end_at)."""
    rows = event_log.overlaps(
        tenant_id, start_at, end_at,
        organizer_email=organizer_email,
        exclude_event_id=exclude_event_id,
    )
    return ConflictReport(has_conflict=bool(rows), overlapping_events=rows)
