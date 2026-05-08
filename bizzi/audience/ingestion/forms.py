"""Connecteur formulaires HTML standard.

Le frontend tenant POSTe directement sur /api/audience/ingest avec
source='forms'. Ce module normalise les noms de champs courants
(name/email/city/message) côté backend.
"""
from __future__ import annotations

from typing import Any

from .base import NormalizedMessage


def parse_form_payload(payload: dict[str, Any], *, tenant_id: int) -> NormalizedMessage:
    msg = payload.get("message") or payload.get("text") or payload.get("comment") or ""
    return NormalizedMessage(
        tenant_id=tenant_id,
        source="forms",
        raw_message=str(msg),
        platform=payload.get("platform") or payload.get("form_id"),
        author_name=payload.get("name") or payload.get("author_name"),
        author_external_id=payload.get("email") or payload.get("author_external_id"),
        city=payload.get("city"),
        metadata={k: v for k, v in payload.items()
                  if k not in {"message", "text", "comment", "platform", "form_id",
                               "name", "author_name", "email", "author_external_id", "city"}},
    )
