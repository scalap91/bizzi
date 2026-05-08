"""Interface universelle d'ingestion. Aucun champ sectoriel.

Un connecteur (chatbot, FB, formulaire, webhook tiers, email, etc.)
transforme un payload brut spécifique au protocole en `NormalizedMessage`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class NormalizedMessage:
    tenant_id: int
    source: str                       # chatbot|facebook|forms|webhook|email|...
    raw_message: str
    platform: Optional[str] = None     # ex: 'site_<slug>', 'fb_page_<id>'
    author_name: Optional[str] = None
    author_external_id: Optional[str] = None
    city: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class IngestionConnector(Protocol):
    """Interface minimale d'un connecteur."""
    name: str

    def parse(self, payload: dict[str, Any], *, tenant_id: int) -> NormalizedMessage:
        ...
