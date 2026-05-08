"""Connecteur Facebook Graph API webhook (Phase 1).

Stub Phase 0 : structure de la fonction de parsing à compléter quand le
token FB sera provisionné. Documenté pour OpenAPI.

Format attendu (FB Page webhook) : {"entry": [{"changes": [{"value": {...}}]}]}
"""
from __future__ import annotations

from typing import Any

from .base import NormalizedMessage


def parse_facebook_payload(payload: dict[str, Any], *, tenant_id: int) -> list[NormalizedMessage]:
    """TODO Phase 1 : extraire chaque commentaire individuel."""
    msgs: list[NormalizedMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            if value.get("item") not in ("comment", "post"):
                continue
            text = value.get("message") or ""
            if not text:
                continue
            msgs.append(NormalizedMessage(
                tenant_id=tenant_id,
                source="facebook",
                raw_message=str(text),
                platform=f"fb_page_{value.get('post_id', '').split('_')[0] or 'unknown'}",
                author_name=(value.get("from") or {}).get("name"),
                author_external_id=(value.get("from") or {}).get("id"),
                metadata={"fb_payload": value},
            ))
    return msgs
