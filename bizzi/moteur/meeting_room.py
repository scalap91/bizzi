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

    def _get_verifier(self):
        for agent in self.agents:
            if agent.role == "verification" and agent.status == "active":
                return agent
        return None

    def _team_brief(self) -> str:
        lines = []
        for a in self.agents:
            if a.status != "active":
                continue
            spec = f" — {a.specialty}" if a.specialty else ""
            lines.append(f"  • {a.name} ({a.title}{spec})")
        return "Équipe présente :\n" + "\n".join(lines)

    def _match_producer(self, topic: str, producers: list, taken: set):
        topic_low = topic.lower()
        free = [p for p in producers if p.slug not in taken]
        if not free:
            return None
        for p in free:
            spec_words = [w.lower() for w in (p.specialty or "").split() if len(w) >= 4]
            if any(w in topic_low for w in spec_words):
                return p
        return free[0]

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
        agenda = agenda or []
        messages    = []
        decisions   = []
        assignments = {}

        director    = self._get_director()
        validator   = self._get_validator()
        distributor = self._get_distributor()
        verifier    = self._get_verifier()
        producers   = self._get_producers()
        absent      = self._get_absent()

        org   = self.domain.name
        vocab = self.vocab
        team_brief = self._team_brief()

        logger.info(
            f"[{vocab.meeting_room.upper()}] {org} · "
            f"{len(producers)} {vocab.producers} · "
            f"{1 if validator else 0} validateur · "
            f"{1 if verifier else 0} vérificateur · "
            f"{len(absent)} absents"
        )

        # ── 1. Direction : ouverture ─────────────────────────────
        if director:
            ctx = (
                f"{team_brief}\n\n"
                f"Tu ouvres la conférence de rédaction de {org}. "
                f"Ordre du jour : {', '.join(agenda) if agenda else 'revue générale'}. "
                f"Salue brièvement et donne le ton (3-4 phrases max)."
            )
            speech = await director.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker=director.name, role=director.title, content=speech))

        # ── 2. Distribution : tendances ──────────────────────────
        if distributor:
            ctx = (
                f"Tu fais le point sur les tendances réseaux et signaux faibles "
                f"que tu as captés ce matin pour {org}. "
                f"Cite 2 ou 3 tendances précises. 3-4 phrases max."
            )
            speech = await distributor.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker=distributor.name, role=distributor.title, content=speech))

        # ── 3. Pré-assignation algorithmique des sujets ──────────
        taken = set()
        for topic in agenda:
            p = self._match_producer(topic, producers, taken)
            if p:
                assignments[p.slug] = topic
                decisions.append(f"{p.name} → {topic}")
                taken.add(p.slug)

        # ── 4. Validation : Victor distribue (sans inventer) ─────
        if validator and assignments:
            assignment_lines = "\n".join([
                f"  • {topic} → {next((p.name for p in producers if p.slug == slug), slug)}"
                for slug, topic in assignments.items()
            ])
            ctx = (
                f"{team_brief}\n\n"
                f"Voici les assignations qui viennent d'être actées :\n{assignment_lines}\n\n"
                f"Pour chaque assignation, indique en UNE phrase l'angle attendu et la priorité. "
                f"N'invente AUCUN nom de journaliste : utilise UNIQUEMENT les noms ci-dessus."
            )
            speech = await validator.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker=validator.name, role=validator.title, content=speech))

        # ── 5. Chaque journaliste assigné confirme son angle ─────
        for slug, topic in assignments.items():
            producer = next((p for p in producers if p.slug == slug), None)
            if not producer:
                continue
            ctx = (
                f"On vient de te confier le sujet : « {topic} ».\n"
                f"Tu confirmes brièvement (2-3 phrases max) ton angle de traitement "
                f"et tes premières sources. Reste fidèle à ton style perso."
            )
            speech = await producer.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker=producer.name, role=producer.title, content=speech))

        # ── 6. Vérification : Alex pointe les vigilances ─────────
        if verifier and assignments:
            topics_list = " ; ".join(assignments.values())
            ctx = (
                f"Tu interviens en fin de réunion comme fact-checker. "
                f"Liste 3 à 5 points de vigilance ou sources prioritaires à vérifier "
                f"sur ces sujets : {topics_list}. "
                f"Format puces courtes."
            )
            speech = await verifier.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker=verifier.name, role=verifier.title, content=speech))

        # ── 7. Direction : clôture ───────────────────────────────
        if director:
            decisions_str = " ; ".join(decisions) if decisions else "pipeline standard"
            ctx = (
                f"Tu clôtures la conférence. Décisions actées : {decisions_str}. "
                f"Une phrase de clôture, donne le top départ."
            )
            speech = await director.speak(ctx)
            if speech:
                messages.append(MeetingMessage(
                    speaker=director.name, role=director.title, content=speech))

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
