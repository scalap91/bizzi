"""Connecteur chatbot — PARSER BRIDGE pur (pas de logique IA).

⚠️ STRATÉGIE ADDITIVE PASCAL — coexistence avec tools/chat/chat_agent.py
═══════════════════════════════════════════════════════════════════════
`bizzi/tools/chat/chat_agent.py` reste l'agent IA conversationnel
standalone (intent + actions, dialogue avec l'utilisateur). Il N'EST
PAS remplacé.

Ce module est un PARSER BRIDGE qui :
  - reçoit un payload widget HTTP (déjà émis par chat_widget.js)
  - le normalise en NormalizedMessage pour le pipeline d'ingestion
    (clean → analyze → embed → store) du module audience

Le widget peut donc, en parallèle de son appel à api.anthropic ou à
l'agent existant, POSTer la même question vers /api/audience/ingest
pour faire entrer la voix utilisateur dans le capteur d'opinion.

Aucune logique IA, aucune réponse générée ici. Si on veut une réponse
chatbot, on continue à appeler chat_agent côté tenant.
"""
from __future__ import annotations

from typing import Any

from .base import NormalizedMessage


def parse_chatbot_payload(payload: dict[str, Any], *, tenant_id: int) -> NormalizedMessage:
    """Parser bridge widget JS → NormalizedMessage. Aucune génération."""
    return NormalizedMessage(
        tenant_id=tenant_id,
        source="chatbot",
        raw_message=str(payload.get("message") or ""),
        platform=payload.get("platform"),
        author_name=payload.get("author_name"),
        author_external_id=payload.get("session_id") or payload.get("author_external_id"),
        city=payload.get("city"),
        metadata={k: v for k, v in payload.items()
                  if k not in {"message", "platform", "author_name",
                               "session_id", "author_external_id", "city"}},
    )
