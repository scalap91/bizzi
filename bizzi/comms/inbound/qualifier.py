"""Qualification d'un appel entrant via LLM (Ollama).

Sortie normalisée :

    Qualification(
        intent          = "rdv" | "renseignement" | "urgence" | "reclamation" | "autre",
        urgency         = 0..3,
        suggested_action= "transfer" | "rdv" | "sms_confirm" | "mail_summary" | "ticket",
        extracted       = {nom?, contact?, demande?, date?, …},
        confidence      = 0.0..1.0,
        requires_human  = bool,
        summary         = "résumé une phrase",
    )

Fallback safe si Ollama down ou JSON malformé : intent='autre',
suggested_action='ticket', requires_human=True, confidence=0.0.

Le module ne lève **jamais** : un appel entrant ne doit pas se perdre.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("comms.inbound.qualifier")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "mistral:7b"
DEFAULT_TIMEOUT = 30


VALID_INTENTS = {"rdv", "renseignement", "urgence", "reclamation", "autre"}
VALID_ACTIONS = {"transfer", "rdv", "sms_confirm", "mail_summary", "ticket"}


@dataclass
class Qualification:
    intent: str = "autre"
    urgency: int = 0
    suggested_action: str = "ticket"
    extracted: dict = field(default_factory=dict)
    confidence: float = 0.0
    requires_human: bool = True
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _build_prompt(
    transcript: list[dict],
    tenant_persona: Optional[dict] = None,
) -> str:
    persona = tenant_persona or {}
    org_name = persona.get("name") or persona.get("org") or "l'entreprise"

    lines = []
    for entry in transcript[:80]:  # limit pour le contexte
        role = entry.get("role") or "?"
        text = (entry.get("text") or entry.get("content") or "").strip()
        if text:
            lines.append(f"- {role}: {text[:500]}")
    transcript_str = "\n".join(lines) or "(transcript vide)"

    return f"""Tu es un classificateur d'appels téléphoniques entrants reçus par {org_name}.
Tu analyses la transcription ci-dessous et tu retournes UNIQUEMENT un JSON valide
au format suivant (pas de texte avant ou après) :

{{
  "intent": "rdv" | "renseignement" | "urgence" | "reclamation" | "autre",
  "urgency": 0|1|2|3,
  "suggested_action": "transfer" | "rdv" | "sms_confirm" | "mail_summary" | "ticket",
  "extracted": {{"nom": "...", "demande": "...", "contact": "...", "date": "..."}},
  "confidence": 0.0..1.0,
  "requires_human": true|false,
  "summary": "résumé en 1 phrase"
}}

Règles :
- urgency : 0=info, 1=à traiter, 2=prioritaire, 3=urgence vitale
- intent="urgence" ⇒ urgency≥2 et requires_human=true et suggested_action="transfer"
- intent="reclamation" ⇒ requires_human=true
- intent="rdv" ⇒ suggested_action="rdv" sauf si l'appelant veut juste des infos
- intent="renseignement" et réponse simple ⇒ suggested_action="sms_confirm" si numéro disponible
- Sinon ⇒ suggested_action="ticket"

Transcription :
{transcript_str}

JSON :"""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_response(raw: str) -> dict:
    """Extrait le premier objet JSON présent dans `raw`. Retourne {} si introuvable."""
    if not raw:
        return {}
    m = _JSON_RE.search(raw)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _normalize(parsed: dict) -> Qualification:
    """Coerce le dict LLM vers une Qualification valide (clamps + defaults)."""
    intent = (parsed.get("intent") or "autre").lower()
    if intent not in VALID_INTENTS:
        intent = "autre"

    action = (parsed.get("suggested_action") or "ticket").lower()
    if action not in VALID_ACTIONS:
        action = "ticket"

    try:
        urgency = max(0, min(3, int(parsed.get("urgency", 0))))
    except (TypeError, ValueError):
        urgency = 0

    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0

    extracted = parsed.get("extracted") or {}
    if not isinstance(extracted, dict):
        extracted = {}

    requires_human = bool(parsed.get("requires_human", False))

    # Cohérence : urgence haute ou réclamation → human
    if intent in ("urgence", "reclamation"):
        requires_human = True
    if urgency >= 2:
        requires_human = True

    summary = str(parsed.get("summary") or "")[:1000]

    return Qualification(
        intent=intent,
        urgency=urgency,
        suggested_action=action,
        extracted=extracted,
        confidence=confidence,
        requires_human=requires_human,
        summary=summary,
    )


async def qualify(
    transcript: list[dict],
    *,
    tenant_persona: Optional[dict] = None,
    model: str = DEFAULT_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    timeout_sec: int = DEFAULT_TIMEOUT,
    enabled: bool = True,
) -> Qualification:
    """Qualifie un transcript via Ollama. Retourne toujours une Qualification (jamais ne lève).

    Si `enabled=False` ou Ollama indisponible : Qualification fallback (ticket, requires_human).
    """
    if not enabled:
        return Qualification(intent="autre", suggested_action="ticket", requires_human=True)

    if not transcript:
        return Qualification(
            intent="autre", suggested_action="ticket", requires_human=True,
            summary="(transcript vide)",
        )

    prompt = _build_prompt(transcript, tenant_persona)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1, "num_predict": 600},
                },
            )
            if r.status_code >= 400:
                logger.warning(
                    "qualifier: Ollama HTTP %s: %s", r.status_code, r.text[:200]
                )
                return Qualification(intent="autre", suggested_action="ticket", requires_human=True)
            data = r.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.warning("qualifier: Ollama erreur (%s) — fallback ticket+human", e)
        return Qualification(intent="autre", suggested_action="ticket", requires_human=True)

    raw_response = data.get("response", "")
    parsed = _parse_llm_response(raw_response)
    if not parsed:
        logger.warning("qualifier: réponse LLM non-JSON (truncated=%r)", raw_response[:200])
        return Qualification(intent="autre", suggested_action="ticket", requires_human=True)

    return _normalize(parsed)
