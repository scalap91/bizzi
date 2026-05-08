"""Connecteur webhook générique (Zendesk, Trustpilot, GoogleReviews, ...).

Phase 1 : signature/HMAC + parsers spécifiques par provider. Ici, on
expose juste un parser passthrough pour le scaffold OpenAPI.
"""
from __future__ import annotations

from typing import Any

from .base import NormalizedMessage


def parse_generic_webhook(provider: str, payload: dict[str, Any], *, tenant_id: int) -> NormalizedMessage:
    text = (
        payload.get("message")
        or payload.get("comment")
        or payload.get("review", {}).get("text")
        or payload.get("ticket", {}).get("description")
        or ""
    )
    return NormalizedMessage(
        tenant_id=tenant_id,
        source="webhook",
        raw_message=str(text),
        platform=f"webhook_{provider}",
        author_external_id=str(payload.get("id") or payload.get("ticket_id") or ""),
        metadata={"provider": provider, "payload": payload},
    )
