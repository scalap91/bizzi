"""bizzi.audience.ingestion — Connecteurs sources (Phase 0 = base + chatbot/forms).

Phase 1 : facebook (Graph webhook), webhook generic (Zendesk/Trustpilot/
GoogleReviews), email IMAP, twitter.

Tous les connecteurs convergent vers `audience.routes.ingest_message()`
qui orchestre clean → analyze → embed → store.
"""
from .base import IngestionConnector, NormalizedMessage

__all__ = ["IngestionConnector", "NormalizedMessage"]
