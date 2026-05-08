"""tools/anonymizer — module d'anonymisation PII + classification d'intent.

Phase 11 chat_logs (data-resale-ready).

Exports :
    anonymize(text)          → (texte_anonymisé, pii_detected: bool)
    hash_user_id(identifier) → sha256[:16] stable pour user_anon_id
    classify_message(msg, industry) → {"intent": "...", "topic_tags": [...]}
"""
from .pii import anonymize, hash_user_id
from .intent import classify_message

__all__ = ["anonymize", "hash_user_id", "classify_message"]
