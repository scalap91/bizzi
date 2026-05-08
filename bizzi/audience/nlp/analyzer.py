"""Analyse LLM universelle des remontées d'audience.

Engine agnostique : la liste de catégories est injectée dans le prompt
depuis le YAML tenant — aucune valeur sectorielle codée ici.

Modèle : gpt-4o-mini (rapide + peu coûteux). Si OPENAI_API_KEY absent,
fallback heuristique déterministe pour ne pas bloquer dev/test.

Sortie normalisée :
{
  "categories":     [str, ...],           # 1..3, sous-ensemble strict des cats du YAML
  "subcategory":    str,                   # libre, plus précis
  "emotion":        str,                   # neutre|inquiet|frustré|en_colere|déçu|satisfait|enthousiaste
  "keywords":       [str, ...],            # 3..5
  "priority_score": int,                   # 0..10 (post-boost YAML)
  "language":       str,                   # fr|en|...
  "summary":        str,                   # 1 phrase
  "model":          str,                   # 'gpt-4o-mini' ou 'heuristic'
}
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("BIZZI_AUDIENCE_MODEL", "gpt-4o-mini")
EMOTIONS = [
    "neutre", "inquiet", "frustré", "en_colere",
    "déçu", "satisfait", "enthousiaste",
]


SYSTEM_PROMPT_TEMPLATE = """Tu es un classifieur de remontées d'audience pour le tenant "{tenant_name}".
Tu dois catégoriser un message reçu (chat, commentaire, formulaire, ticket, avis, etc.) selon UNIQUEMENT les catégories listées ci-dessous.

Catégories disponibles (utilise EXACTEMENT ces ids, pas d'autres) :
{categories_block}

Tu retournes UNIQUEMENT un JSON valide (pas de markdown, pas de texte autour). Schéma :
{{
  "categories":     [string, ...],   // 1 à 3 ids issus de la liste ci-dessus
  "subcategory":    string,           // libre, plus précis (max 60 chars), peut être vide
  "emotion":        string,           // une valeur parmi : {emotions}
  "keywords":       [string, ...],    // 3 à 5 mots-clés saillants en minuscules
  "priority_score": integer,          // 0 (trivial) à 10 (crise immédiate)
  "language":       string,           // code ISO 639-1 (fr, en, es, ...)
  "summary":        string            // 1 phrase synthèse, max 160 chars
}}

Règles :
- Choisis 1 catégorie principale, 2-3 maximum. Toujours dans la liste.
- Si aucune catégorie ne colle, utilise "autres" si présent dans la liste, sinon la moins inadaptée.
- priority_score reflète l'URGENCE pour le tenant (sécurité, danger, perte client, downtime, crise réputation, etc.).
"""


def _format_categories_block(categories: Iterable[dict]) -> str:
    lines = []
    for c in categories:
        cid = c.get("id") or c.get("label") or ""
        label = c.get("label") or cid
        if cid == label:
            lines.append(f"- {cid}")
        else:
            lines.append(f"- {cid} ({label})")
    return "\n".join(lines) if lines else "- autres"


def _validate(out: dict, valid_ids: set[str]) -> dict:
    cats = out.get("categories") or []
    if isinstance(cats, str):
        cats = [cats]
    cats = [c for c in cats if isinstance(c, str)]
    cats = [c for c in cats if c in valid_ids][:3]
    if not cats:
        cats = ["autres"] if "autres" in valid_ids else (sorted(valid_ids)[:1] or [""])

    emotion = out.get("emotion") or "neutre"
    if emotion not in EMOTIONS:
        emotion = "neutre"

    kws = out.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    kws = [str(k).strip().lower() for k in kws if str(k).strip()][:5]

    try:
        score = int(out.get("priority_score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))

    return {
        "categories": cats,
        "subcategory": str(out.get("subcategory") or "")[:60],
        "emotion": emotion,
        "keywords": kws,
        "priority_score": score,
        "language": str(out.get("language") or "fr")[:5],
        "summary": str(out.get("summary") or "")[:200],
    }


def _apply_priority_boost(score: int, text: str, boost_map: dict[int, list[str]]) -> int:
    if not boost_map or not text:
        return score
    haystack = text.lower()
    delta = 0
    for bonus, keywords in boost_map.items():
        for kw in keywords or []:
            if kw and kw.lower() in haystack:
                delta = max(delta, int(bonus))
                break
    return max(0, min(10, score + delta))


# ── LLM call ──────────────────────────────────────────────────────
def _call_openai(system: str, user: str, model: str, timeout: float = 20.0) -> Optional[dict]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import httpx
        with httpx.Client(timeout=timeout) as c:
            r = c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            data = r.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:  # noqa: BLE001
        logger.warning("audience.analyzer OpenAI call failed: %s", e)
        return None


# ── Heuristic fallback (no API key) ───────────────────────────────
_LANG_HINTS = {
    "fr": [" le ", " la ", " est ", " pas ", "bonjour", "merci", "ne ", "pourquoi"],
    "en": [" the ", " is ", " not ", "hello", "thanks", "why ", " and "],
    "es": [" el ", " la ", " no ", "hola", "gracias", "por qué"],
}
_NEG_WORDS = ["nul", "honte", "scandale", "horrible", "inacceptable", "bug", "panne",
              "danger", "urgent", "crise", "fâché", "fache", "deçu", "déçu"]
_POS_WORDS = ["merci", "super", "bravo", "parfait", "excellent", "génial", "genial"]


def _detect_language(text: str) -> str:
    low = " " + text.lower() + " "
    best, best_n = "fr", 0
    for code, hints in _LANG_HINTS.items():
        n = sum(1 for h in hints if h in low)
        if n > best_n:
            best, best_n = code, n
    return best


def _heuristic(cleaned: str, categories: list[dict]) -> dict:
    valid_ids = [c["id"] for c in categories]
    chosen = "autres" if "autres" in valid_ids else (valid_ids[0] if valid_ids else "autres")

    # Match texte ↔ label/id de catégorie
    low = cleaned.lower()
    for c in categories:
        token = (c.get("id") or "").lower()
        label = (c.get("label") or "").lower()
        if token and token in low:
            chosen = c["id"]; break
        if label and label in low:
            chosen = c["id"]; break

    score = 0
    for w in _NEG_WORDS:
        if w in low:
            score += 1
    score = min(7, score * 2)
    emotion = "neutre"
    if any(w in low for w in _POS_WORDS):
        emotion = "satisfait"
    elif score >= 5:
        emotion = "en_colere"
    elif score >= 3:
        emotion = "frustré"
    elif score >= 1:
        emotion = "inquiet"

    keywords = [w for w in re.findall(r"[\wàâäéèêëîïôöùûüç-]{4,}", low) if w not in ("dans", "pour", "avec", "cela", "merci")][:5]

    return {
        "categories": [chosen] if chosen else [],
        "subcategory": "",
        "emotion": emotion,
        "keywords": keywords,
        "priority_score": score,
        "language": _detect_language(cleaned),
        "summary": cleaned[:160],
    }


# ── Public API ────────────────────────────────────────────────────
def analyze(
    cleaned_message: str,
    *,
    categories: list[dict],
    priority_keywords_boost: Optional[dict[int, list[str]]] = None,
    tenant_name: str = "tenant",
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Analyse une remontée. Retourne le dict normalisé (cf. module docstring).

    `categories` : liste normalisée [{id, label, ...}] issue du YAML tenant.
    """
    if not cleaned_message or not cleaned_message.strip():
        return {
            "categories": [], "subcategory": "", "emotion": "neutre",
            "keywords": [], "priority_score": 0,
            "language": "fr", "summary": "",
            "model": "empty",
        }

    valid_ids = {c["id"] for c in categories} or {"autres"}
    use_model = model or DEFAULT_MODEL

    raw = _call_openai(
        system=SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            categories_block=_format_categories_block(categories),
            emotions=", ".join(EMOTIONS),
        ),
        user=cleaned_message,
        model=use_model,
    )
    if raw is None:
        out = _heuristic(cleaned_message, categories)
        out["model"] = "heuristic"
    else:
        out = _validate(raw, valid_ids)
        out["model"] = use_model

    # Boost via mots-clés YAML (post-LLM, déterministe)
    out["priority_score"] = _apply_priority_boost(
        out["priority_score"], cleaned_message, priority_keywords_boost or {}
    )
    return out
