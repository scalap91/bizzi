"""Nettoyage + anonymisation PII universel.

Engine agnostique : aucune heuristique sectorielle. Les patterns retirent
les éléments à risque RGPD avant que le texte ne touche le LLM ou la DB.

Patterns couverts :
- emails
- numéros de téléphone (FR + format international +33/+44/...)
- IBAN (FR + autres ISO 13616)
- numéros de carte bancaire (Luhn-like, séquence 13-19 chiffres)
- numéros de sécurité sociale FR (15 chiffres)
- URLs (conservation du domaine pour contexte)
- whitespace excessif

NB : volontairement conservateur. La détection de noms propres demande
NER (spaCy/transformers) et sera ajoutée Phase 1 si nécessaire — ici on
remplace seulement les PII structurées dont la regex est fiable.
"""
from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple


class CleaningResult(NamedTuple):
    cleaned: str
    redactions: dict[str, int]   # ex: {"email": 1, "phone": 2}


# ── patterns ──────────────────────────────────────────────────────
_RE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
# Téléphone : +XX suivi de 7-14 chiffres avec séparateurs optionnels,
# ou format FR 0X XX XX XX XX. On exige au moins 9 chiffres au total.
_RE_PHONE_INTL = re.compile(
    r"\+\d{1,3}[\s.\-()]*(?:\d[\s.\-()]*){7,14}\d"
)
_RE_PHONE_FR = re.compile(
    r"\b0[1-9](?:[\s.\-]?\d{2}){4}\b"
)
_RE_IBAN = re.compile(
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b"
)
_RE_CARD = re.compile(
    r"\b(?:\d[ -]?){13,19}\b"
)
_RE_SS_FR = re.compile(
    r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"
)
_RE_URL = re.compile(
    r"https?://\S+", re.IGNORECASE
)
_RE_WS = re.compile(r"\s+")


def _redact(pattern: re.Pattern, text: str, label: str, counter: dict[str, int]) -> str:
    matches = pattern.findall(text)
    if not matches:
        return text
    counter[label] = counter.get(label, 0) + len(matches)
    return pattern.sub(f"[{label}_REDACTED]", text)


def _shorten_url(text: str, counter: dict[str, int]) -> str:
    def repl(m: re.Match) -> str:
        counter["url"] = counter.get("url", 0) + 1
        url = m.group(0)
        m2 = re.match(r"https?://([^/?#]+)", url)
        host = m2.group(1) if m2 else "url"
        return f"[url:{host}]"
    return _RE_URL.sub(repl, text)


def clean_and_anonymize(raw: str) -> CleaningResult:
    """Retourne (texte propre, dict de redactions par label).

    Préserve le sens du message (URLs réduites au domaine, PII remplacées
    par tokens explicites) pour que le LLM puisse encore catégoriser.
    """
    if not raw:
        return CleaningResult("", {})

    text = unicodedata.normalize("NFC", raw).strip()
    counter: dict[str, int] = {}

    text = _shorten_url(text, counter)
    text = _redact(_RE_EMAIL, text, "email", counter)
    text = _redact(_RE_SS_FR, text, "ssn", counter)
    text = _redact(_RE_IBAN, text, "iban", counter)
    text = _redact(_RE_CARD, text, "card", counter)
    text = _redact(_RE_PHONE_INTL, text, "phone", counter)
    text = _redact(_RE_PHONE_FR, text, "phone", counter)

    text = _RE_WS.sub(" ", text).strip()
    return CleaningResult(text, counter)
