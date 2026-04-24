"""
moteur/pipeline.py
===================
Pipeline générique. S'adapte à n'importe quel domaine.

Usage :
    from config.domain_loader import DomainLoader
    from agents.base_agent import Agent
    from moteur.pipeline import Pipeline

    domain = DomainLoader.load_domain('media')

    agents = [
        Agent(slug="sophie", name="Sophie", agent_id="writer", domain=domain),
        Agent(slug="marc",   name="Marc",   agent_id="editor", domain=domain),
    ]

    pipeline = Pipeline(domain=domain, agents=agents)
    result = await pipeline.run(topics=["Grand Paris Express"])
"""

import logging
import asyncio
from datetime import datetime
from config.domain_loader import DomainConfig
from moteur.meeting_room import MeetingRoom

logger = logging.getLogger("core.pipeline")


class Pipeline:
    """
    Pipeline universel.
    Les étapes sont définies dans le domain.yaml.
    """

    def __init__(self, domain: DomainConfig, agents: list):
        self.domain  = domain
        self.agents  = agents
        self.vocab   = domain.ui.vocabulary

    def _get_agent_by_role(self, role: str):
        return next((a for a in self.agents if a.role == role and a.status == "active"), None)

    def _get_producers(self):
        return [a for a in self.agents if a.role == "production" and a.status == "active"]

    async def run(self, topics: list[str] = None) -> dict:
        """
        Lance le pipeline complet selon les étapes définies dans le domaine.

        Args:
            topics : liste de sujets à traiter (si None → collecte automatique)

        Returns:
            dict avec les stats du run
        """
        start = datetime.utcnow()
        vocab = self.vocab
        steps = self.domain.pipeline.steps

        stats = {
            "domain":     self.domain.domain,
            "org":        self.domain.name,
            "started_at": start.isoformat(),
            "steps_run":  [],
            "produced":   0,
            "approved":   0,
            "rejected":   0,
            "errors":     0,
        }

        logger.info(
            f"[PIPELINE] Démarrage · {self.domain.name} · "
            f"{len(steps)} étapes · {len(self._get_producers())} {vocab.producers}"
        )

        results = []

        for step in steps:

            # ── Réunion ───────────────────────────────────────
            if step == "meeting":
                try:
                    room   = MeetingRoom(domain=self.domain, agents=self.agents)
                    report = await room.run(agenda=topics or [])
                    stats["steps_run"].append({"step": step, "status": "ok", "decisions": len(report.decisions)})
                    logger.info(f"[PIPELINE] {vocab.meeting_room} terminée · {len(report.decisions)} décisions")
                except Exception as e:
                    logger.warning(f"[PIPELINE] {vocab.meeting_room} non bloquante : {e}")
                    stats["steps_run"].append({"step": step, "status": "skipped"})

            # ── Production du contenu ─────────────────────────
            elif step in ["write", "write_article", "write_statement", "write_report", "analyze"]:
                producers = self._get_producers()
                if not producers:
                    logger.warning(f"[PIPELINE] Aucun {vocab.producer} disponible")
                    stats["errors"] += 1
                    continue

                for i, topic in enumerate(topics or ["Revue générale"]):
                    producer = producers[i % len(producers)]
                    try:
                        result = await producer.produce(topic=topic)
                        if result.get("status") == "produced":
                            results.append(result)
                            stats["produced"] += 1
                            logger.info(f"[PIPELINE] {vocab.content_unit} produit · {producer.name} · {topic[:50]}")
                        else:
                            stats["errors"] += 1
                    except Exception as e:
                        logger.error(f"[PIPELINE] Erreur production : {e}")
                        stats["errors"] += 1

                stats["steps_run"].append({"step": step, "status": "ok", "count": stats["produced"]})

            # ── Validation ────────────────────────────────────
            elif step in ["validate", "validate_content", "legal_check", "regulatory_check"]:
                validator = self._get_agent_by_role("validation")
                if not validator or not results:
                    stats["steps_run"].append({"step": step, "status": "skipped"})
                    continue

                approved_results = []
                for result in results:
                    try:
                        validation = await validator.validate(result.get("content", ""))
                        if validation.get("decision") == "approve":
                            result["validation"] = validation
                            approved_results.append(result)
                            stats["approved"] += 1
                        else:
                            stats["rejected"] += 1
                            logger.info(
                                f"[PIPELINE] {vocab.content_unit} rejeté · "
                                f"score {validation.get('score')} · "
                                f"{validation.get('feedback', '')}"
                            )
                    except Exception as e:
                        logger.error(f"[PIPELINE] Erreur validation : {e}")
                        stats["errors"] += 1

                results = approved_results
                stats["steps_run"].append({
                    "step": step, "status": "ok",
                    "approved": stats["approved"], "rejected": stats["rejected"]
                })

            # ── Distribution ──────────────────────────────────
            elif step in ["distribute", "distribute_social", "publish_press", "send_client"]:
                distributor = self._get_agent_by_role("distribution")
                if distributor and results:
                    logger.info(
                        f"[PIPELINE] Distribution · {len(results)} {vocab.content_units} · "
                        f"formats : {', '.join(self.domain.output.formats[:3])}"
                    )
                stats["steps_run"].append({"step": step, "status": "ok", "count": len(results)})

            # ── Étapes passthrough (collecte, vérification, archivage...) ──
            else:
                logger.info(f"[PIPELINE] Étape : {step}")
                stats["steps_run"].append({"step": step, "status": "ok"})

            # Petite pause entre les étapes
            await asyncio.sleep(0.1)

        stats["finished_at"] = datetime.utcnow().isoformat()
        stats["duration_s"]  = int((datetime.utcnow() - start).total_seconds())
        stats["results"]     = results

        logger.info(
            f"[PIPELINE] Terminé · {stats['produced']} produits · "
            f"{stats['approved']} approuvés · {stats['rejected']} rejetés · "
            f"{stats['duration_s']}s"
        )

        return stats
