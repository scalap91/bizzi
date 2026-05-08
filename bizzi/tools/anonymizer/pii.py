"""tools/anonymizer/pii.py — détection + anonymisation PII basique.

Couvre les cas évidents (email, téléphones FR, IBAN, cartes, dates, montants).
Accepte un faux négatif sur les noms propres : la classification intent (Claude)
sert de filet, et la table chat_logs garde aussi `message_user` brut côté tenant.

Philosophie :
- Conservatrice (mieux vaut anonymiser un peu trop que pas assez côté `_anon`).
- Stable : remplaçants numérotés (EMAIL_1, EMAIL_2, …) — utile pour relire un
  thread en clair côté analyste.
- Idempotente : appliquer 2× ne crée pas EMAIL_1_1.
"""
from __future__ import annotations

import hashlib
import re

# ── Patterns ──────────────────────────────────────────────────────────────
# Ordre d'application (le 1er match gagne sur le segment) :
# Cartes AVANT téléphones (16 chiffres collés ressemblent à un téléphone FR sur
# certaines saisies maladroites). IBAN AVANT cartes (préfixe pays).
# Montants AVANT dates (le caractère '€' ne collisionne pas mais on garde
# l'ordre clair).
PATTERN_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATTERN_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
PATTERN_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
PATTERN_PHONE_FR = re.compile(r"(?:\+33|0)[1-9](?:[\s.\-]?\d{2}){4}")
PATTERN_DATE = re.compile(r"\b\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}\b")
PATTERN_AMOUNT = re.compile(r"\b\d+(?:[ .,]\d{3})*(?:[,.]\d{1,2})?\s?€")

# Tokens déjà anonymisés (évite double substitution).
PATTERN_ALREADY_TAGGED = re.compile(
    r"\b(?:EMAIL|PHONE|IBAN|CARD|DATE|AMOUNT)_\d+\b"
)


def _replace_with_counter(
    text: str,
    pattern: re.Pattern[str],
    label: str,
    counter_start: int,
    skip_spans: list[tuple[int, int]],
) -> tuple[str, int, list[tuple[int, int]]]:
    """Remplace toutes les occurrences de `pattern` par {label}_N, sans
    toucher aux segments listés dans `skip_spans` (déjà tagués)."""
    out_parts: list[str] = []
    cursor = 0
    counter = counter_start
    new_skip: list[tuple[int, int]] = list(skip_spans)
    matches = list(pattern.finditer(text))
    if not matches:
        return text, counter, new_skip
    cumulative_offset = 0
    for m in matches:
        start, end = m.span()
        # Skip si chevauche un segment déjà tagué (relatif à text original).
        if any(s <= start < e or s < end <= e for s, e in skip_spans):
            continue
        out_parts.append(text[cursor:start])
        replacement = f"{label}_{counter}"
        out_parts.append(replacement)
        # Met à jour skip_spans dans le texte de sortie (approximatif : on
        # n'utilise plus skip_spans après, donc inutile de le maintenir
        # parfaitement — mais on note la position du remplaçant pour idempotence
        # éventuelle d'un autre passage).
        new_skip.append(
            (start + cumulative_offset, start + cumulative_offset + len(replacement))
        )
        cumulative_offset += len(replacement) - (end - start)
        cursor = end
        counter += 1
    out_parts.append(text[cursor:])
    return "".join(out_parts), counter, new_skip


def anonymize(text: str) -> tuple[str, bool]:
    """Anonymise un texte. Retourne (texte_anonymisé, pii_detected)."""
    if not text:
        return text or "", False

    original = text
    # Repère les segments déjà tagués pour idempotence.
    skip_spans: list[tuple[int, int]] = [m.span() for m in PATTERN_ALREADY_TAGGED.finditer(text)]

    # Ordre : email > iban > card > phone > date > amount.
    pii_found = False
    text, _, skip_spans = _replace_with_counter(text, PATTERN_EMAIL, "EMAIL", 1, skip_spans)
    if text != original:
        pii_found = True
        original = text

    text, _, skip_spans = _replace_with_counter(text, PATTERN_IBAN, "IBAN", 1, skip_spans)
    if text != original:
        pii_found = True
        original = text

    text, _, skip_spans = _replace_with_counter(text, PATTERN_CARD, "CARD", 1, skip_spans)
    if text != original:
        pii_found = True
        original = text

    text, _, skip_spans = _replace_with_counter(text, PATTERN_PHONE_FR, "PHONE", 1, skip_spans)
    if text != original:
        pii_found = True
        original = text

    text, _, skip_spans = _replace_with_counter(text, PATTERN_DATE, "DATE", 1, skip_spans)
    if text != original:
        pii_found = True
        original = text

    text, _, _ = _replace_with_counter(text, PATTERN_AMOUNT, "AMOUNT", 1, skip_spans)
    if text != original:
        pii_found = True

    return text, pii_found


def hash_user_id(identifier: str) -> str:
    """Hash stable d'un identifiant (email, téléphone) → 16 hex chars.

    Utilisé pour `user_anon_id` : permet de regrouper les sessions d'un même
    visiteur sans stocker l'identifiant en clair.
    """
    if not identifier:
        return ""
    norm = identifier.strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
