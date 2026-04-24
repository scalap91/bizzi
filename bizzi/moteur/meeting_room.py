"""
moteur/meeting_room.py
=======================
Salle de réunion générique.
Fonctionne pour tous les domaines : média, politique, diagnostic, etc.

Le nom "salle de réunion" remplace "salle de rédaction" —
universel quel que soit le type d'organisation.

Usage :
    room = MeetingRoom(domain=config, agents=my_agents)
    report = await room.run(agenda=["Sujet 1", "Sujet 2"])
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from config.domain_loader import DomainConfig

logger = logging.getLogger("core.meeting")


# ══════════════════════════════════════════════════════════════
# MODÈLES
# ══════════════════════════════════════════════════════════════

@dataclass
class MeetingMessage:
    speaker:  str
    role:     str
    content:  str
    time:     str = ""

    def __post_init__(self):
        if not self.time:
            self.time = datetime.utcnow().strftime("%H:%M")


@dataclass
class MeetingReport:
    date:              str
    org_name:          str
    domain:            str
    participants:      list[str]
    absent:            list[str]
    messages:          list[MeetingMessage]
    decisions:         list[str]
    assignments:       dict          # {agent_slug: topic}
    absence_summaries: dict          # {agent_slug: summary}
    duration_seconds:  int = 0


# ══════════════════════════════════════════════════════════════
# SALLE DE RÉUNION
# ══════════════════════════════════════════════════════════════

class MeetingRoom:
    """
    Salle de réunion universelle.
    S'adapte au domaine via le configurateur.
    """

    def __init__(self, domain: DomainConfig, agents: list):
        self.domain  = domain
        self.agents  = agents
        self.vocab   = domain.ui.vocabulary

    def _get_director(self):
        """Trouve l'agent de direction."""
        for agent in self.agents:
            if agent.role == "direction" and agent.status == "active":
                return agent
        return None

    def _get_validator(self):
        """Trouve l'agent de validation."""
        for agent in self.agents:
            if agent.role == "validation" and agent.status == "active":
                return agent
        return None

    def _get_distributor(self):
        """Trouve l'agent de distribution (CM, commercial...)."""
        for agent in self.agents:
            if agent.role == "distribution" and agent.status == "active":
                return agent
        return None

    def _get_producers(self):
        """Trouve tous les agents de production actifs."""
        return [a for a in self.agents if a.role == "production" and a.status == "active"]

    def _get_absent(self):
        """Trouve les agents absents."""
        return [a for a in self.agents if a.status in ["paused", "offline"]]

    async def run(self, agenda: list[str] = None) -> MeetingReport:
        """
        Lance la réunion complète.

        Args:
            agenda : liste de sujets à traiter

        Returns:
            MeetingReport avec transcript et décisions
        """
        start = datetime.utcnow()
        messages   = []
        decisions  = []
        assignments = {}

        director   = self._get_director()
        validator  = self._get_validator()
        distributor = self._get_distributor()
        producers  = self._get_producers()
        absent     = self._get_absent()

        org       = self.domain.name
        vocab     = self.vocab
        content   = vocab.content_unit
        contents  = vocab.content_units

        logger.info(
            f"[{vocab.meeting_room.upper()}] Démarrage · {org} · "
            f"{len(producers)} {vocab.producers} actifs · "
            f"{len(absent)} absents"
        )

        # ── 1. Ouverture par le directeur ─────────────────────
        if director:
            ctx = (
                f"Tu ouvres la réunion de {org}. "
                f"Sujets à l'ordre du jour : {', '.join(agenda) if agenda else 'revue générale'}. "
                f"{len(absent)} membres absents. "
                f"Sois bref et direct. 2-3 phrases."
            )
            speech = await director.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker = director.name,
                    role    = director.title,
                    content = speech,
                ))

        # ── 2. Le distributeur présente les tendances / actualités ──
        if distributor:
            ctx = (
                f"Tu présentes les dernières tendances et actualités pertinentes pour {org}. "
                f"Tu recommandes les sujets prioritaires. 2-3 phrases."
            )
            speech = await distributor.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker = distributor.name,
                    role    = distributor.title,
                    content = speech,
                ))

        # ── 3. Le validateur tranche les priorités ────────────
        if validator and agenda:
            for topic in agenda[:3]:
                ctx = (
                    f"Tu décides qui traite '{topic}' et avec quel angle. "
                    f"Assigne un {vocab.producer} et fixe la priorité. 1-2 phrases."
                )
                speech = await validator.speak(ctx)
                if speech:
                    messages.append(MeetingMessage(
                        speaker = validator.name,
                        role    = validator.title,
                        content = speech,
                    ))
                    # Assigner au premier producteur disponible
                    if producers and topic not in assignments.values():
                        producer = producers[len(assignments) % len(producers)]
                        assignments[producer.slug] = topic
                        decisions.append(f"{producer.name} → {topic}")

        # ── 4. Les producteurs répondent ──────────────────────
        for producer in producers[:3]:
            assigned = assignments.get(producer.slug, "")
            ctx = (
                f"Tu confirmes ta prise en charge"
                f"{f' de : {assigned}' if assigned else ''}. "
                f"Tu mentionnes brièvement ton angle ou tes ressources. 1-2 phrases."
            )
            speech = await producer.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker = producer.name,
                    role    = producer.title,
                    content = speech,
                ))

        # ── 5. Clôture par le directeur ───────────────────────
        if director:
            ctx = (
                f"Tu clôtures la réunion. "
                f"Décisions prises : {', '.join(decisions) if decisions else 'pipeline standard'}. "
                f"1-2 phrases."
            )
            speech = await director.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker = director.name,
                    role    = director.title,
                    content = speech,
                ))

        # ── 6. Résumés pour les absents ───────────────────────
        absence_summaries = {}
        if absent and messages:
            transcript = "\n".join([
                f"{m.speaker} ({m.time}) : {m.content}"
                for m in messages
            ])
            for absentee in absent:
                summary = await self._generate_absence_summary(
                    absentee   = absentee,
                    transcript = transcript,
                    decisions  = decisions,
                )
                absence_summaries[absentee.slug] = summary
                logger.info(f"[{vocab.meeting_room}] Résumé généré pour {absentee.name}")

        duration = int((datetime.utcnow() - start).total_seconds())

        report = MeetingReport(
            date              = datetime.utcnow().isoformat(),
            org_name          = org,
            domain            = self.domain.domain,
            participants      = [a.slug for a in self.agents if a.status == "active"],
            absent            = [a.slug for a in absent],
            messages          = messages,
            decisions         = decisions,
            assignments       = assignments,
            absence_summaries = absence_summaries,
            duration_seconds  = duration,
        )

        logger.info(
            f"[{vocab.meeting_room}] Terminée · {duration}s · "
            f"{len(messages)} interventions · {len(decisions)} décisions"
        )

        return report

    async def _generate_absence_summary(
        self,
        absentee:   object,
        transcript: str,
        decisions:  list[str],
    ) -> str:
        """Génère un résumé pour un absent."""
        vocab = self.vocab
        prompt = (
            f"Tu es un assistant de {self.domain.name}.\n"
            f"{absentee.name} était absent(e) de la {vocab.meeting_room}.\n"
            f"Rédige un résumé bref de ce qu'il/elle a manqué.\n\n"
            f"Décisions prises : {', '.join(decisions) if decisions else 'aucune décision majeure'}\n\n"
            f"Transcript :\n{transcript[:1500]}\n\n"
            f"Résumé pour {absentee.name} (3-5 phrases max) :"
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model":  "mistral:7b",
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200},
                    }
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()
        except Exception as e:
            logger.debug(f"Résumé absence (non bloquant) : {e}")

        return (
            f"Résumé {vocab.meeting_room} pour {absentee.name} : "
            f"Décisions : {', '.join(decisions) if decisions else 'pipeline standard'}."
        )
