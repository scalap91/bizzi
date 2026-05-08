"""tools/anonymizer/intent.py — classification d'intent via Claude Haiku.

Appelé après chaque tour de chat pour caractériser le besoin du visiteur :
intent (catégorie unique) + topic_tags (mots-clés courts).

Cache in-memory simple (TTL 1h) pour éviter de re-classer un même message
identique pendant la fenêtre.

Coût indicatif : ~$0.0001 par appel Haiku 4.5.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import anthropic

logger = logging.getLogger("tools.anonymizer.intent")

INTENT_MODEL = "claude-haiku-4-5"
INTENT_MAX_TOKENS = 80
INTENT_TEMPERATURE = 0.1
INTENT_CACHE_TTL_SEC = 3600

ALLOWED_INTENTS = {
    "pricing_query",
    "product_search",
    "modify_booking",
    "complaint",
    "info_only",
    "escalation_request",
    "other",
}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FALLBACK = {"intent": "other", "topic_tags": []}


def _cache_key(message: str, industry: str) -> str:
    h = hashlib.sha256(f"{industry}::{message}".encode("utf-8")).hexdigest()
    return h[:32]


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    if time.time() - ts > INTENT_CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: dict[str, Any]) -> None:
    # GC paresseuse si la map gonfle.
    if len(_CACHE) > 5000:
        cutoff = time.time() - INTENT_CACHE_TTL_SEC
        for k in list(_CACHE.keys()):
            if _CACHE[k][0] < cutoff:
                _CACHE.pop(k, None)
    _CACHE[key] = (time.time(), val)


def _parse_response(text: str) -> dict[str, Any]:
    """Tente de parser un JSON {"intent": str, "topic_tags": list[str]}.

    Tolérant : extrait le 1er bloc {…} si du texte parasite l'entoure.
    """
    if not text:
        return dict(_FALLBACK)
    candidate = text.strip()
    # Si le modèle a entouré de fences markdown.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        data = json.loads(candidate)
    except Exception:
        return dict(_FALLBACK)
    intent = data.get("intent", "other")
    if intent not in ALLOWED_INTENTS:
        intent = "other"
    tags = data.get("topic_tags", [])
    if not isinstance(tags, list):
        tags = []
    # Normalise : strings courtes, max 5 tags.
    clean_tags: list[str] = []
    for t in tags[:5]:
        if isinstance(t, str):
            tt = t.strip().lower()[:30]
            if tt:
                clean_tags.append(tt)
    return {"intent": intent, "topic_tags": clean_tags}


def classify_message(message_user: str, tenant_industry: str = "general") -> dict[str, Any]:
    """Classifie un message visiteur.

    Args:
        message_user: texte brut (ou anonymisé — peu importe pour l'intent).
        tenant_industry: contexte sectoriel (ex: "travel", "media").

    Returns:
        {"intent": str, "topic_tags": list[str]}.
        En cas d'échec (no API key, parsing fail, exception) : {"intent": "other", "topic_tags": []}.
    """
    if not message_user or not message_user.strip():
        return dict(_FALLBACK)

    industry = (tenant_industry or "general").strip().lower()[:30]
    key = _cache_key(message_user, industry)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning("classify_message: ANTHROPIC_API_KEY missing, fallback")
        return dict(_FALLBACK)

    prompt = (
        "Classifie ce message visiteur en UN intent parmi : "
        "pricing_query, product_search, modify_booking, complaint, info_only, "
        "escalation_request, other.\n"
        "Extrais 2-5 topic_tags pertinents (mots courts, en minuscules, en français).\n"
        f"Industry contexte : {industry}\n"
        f'Message : "{message_user[:1000]}"\n'
        'Réponds STRICTEMENT en JSON : {"intent": "...", "topic_tags": ["..."]}'
    )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=INTENT_MODEL,
            max_tokens=INTENT_MAX_TOKENS,
            temperature=INTENT_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        result = _parse_response(text)
    except Exception as e:
        logger.warning(f"classify_message anthropic call failed: {e}")
        return dict(_FALLBACK)

    _cache_put(key, result)
    return result
