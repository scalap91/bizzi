"""
agents/base_agent.py
=====================
Classe de base pour tous les agents.
Fonctionne avec n'importe quel domaine.

Usage :
    from config.domain_loader import DomainLoader
    from agents.base_agent import Agent

    config = DomainLoader.load_domain('media')

    claire = Agent(
        slug      = "claire-bernard",
        name      = "Claire BERNARD",
        agent_id  = "writer",
        specialty = "Société & Politique",
        domain    = config,
    )

    article = await claire.produce(topic="Grand Paris Express")
"""

import logging
import httpx
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from config.domain_loader import DomainConfig

logger = logging.getLogger("core.agent")

# ── Constante globale — structure seo_page ────────────────────
# S'applique à TOUS les clients qui utilisent le type seo_page
# Indépendant du domaine (media, politics, diagnostic, etc.)
SEO_PAGE_STRUCTURE = """Tu dois produire le contenu en DEUX BLOCS séparés UNIQUEMENT par ---BODY--- :

BLOC 1 — meta description (150 caractères MAX) :
- Mots clés en tête
- Percutant, donne envie de cliquer depuis Google
- Chiffres locaux si disponibles (population, %)
- Maximum 150 caractères, pas un de plus

---BODY---

BLOC 2 — corps du texte (80 à 150 mots) :
- Contexte local avec données réelles
- Obligations réglementaires liées à la prestation
- Types de biens concernés dans cette ville
- CTA naturel avec nom du franchisé et téléphone en fin de texte
- Ne pas répéter les mêmes formulations que le BLOC 1

Respecte strictement ce format. Aucun titre. Aucune explication. Juste les deux blocs."""


# ══════════════════════════════════════════════════════════════
# MODÈLE AGENT
# ══════════════════════════════════════════════════════════════

@dataclass
class Agent:
    """
    Agent IA générique. S'adapte à n'importe quel domaine.
    """
    slug:      str            # identifiant unique : "claire-bernard"
    name:      str            # nom affiché : "Claire BERNARD"
    agent_id:  str            # rôle dans le domaine : "writer", "analyst"...
    domain:    DomainConfig   # config du domaine chargée depuis le .yaml

    # Optionnel
    specialty:    str = ""
    email:        str = ""
    status:       str = "active"   # active / paused / offline
    custom_prompt:str = ""          # prompt personnalisé (écrase le défaut)

    # Réseau sociaux
    twitter_token:   str = ""
    linkedin_token:  str = ""
    instagram_token: str = ""
    tiktok_token:    str = ""

    # Stats
    content_count:  int = 0
    avg_score:      float = 0.0
    last_active:    Optional[datetime] = None

    # Ollama
    ollama_url:   str = "http://localhost:11434"
    ollama_model: str = "mistral:7b"

    def __post_init__(self):
        self.last_active = datetime.utcnow()

    @property
    def prompt(self) -> str:
        """Retourne le prompt final de l'agent."""
        if self.custom_prompt:
            return self.custom_prompt
        return self.domain.build_prompt(
            self.agent_id,
            agent_name = self.name,
            specialty  = self.specialty,
        )

    @property
    def title(self) -> str:
        """Titre dans le domaine."""
        agent_cfg = self.domain.get_agent(self.agent_id)
        return agent_cfg.title if agent_cfg else self.agent_id

    @property
    def role(self) -> str:
        """Rôle dans le domaine."""
        agent_cfg = self.domain.get_agent(self.agent_id)
        return agent_cfg.role if agent_cfg else ""

    @property
    def email_address(self) -> str:
        """Email généré automatiquement si non fourni."""
        if self.email:
            return self.email
        slug_email = self.slug.replace('-', '.')
        return f"{slug_email}@{self.domain.name.lower().replace(' ', '')}.fr"

    # ── PRODUCTION ────────────────────────────────────────────

    async def produce(self, topic: str, context: str = "") -> dict:
        """
        Produit un contenu (article, communiqué, rapport...).
        Le type de contenu dépend du domaine.
        """
        vocab = self.domain.ui.vocabulary
        content_type = vocab.content_unit

        system_prompt = f"""{self.prompt}

Règles de {self.domain.name} :
{chr(10).join('- ' + r for r in self.domain.editorial_rules)}

Tu produis un {content_type} de {self.domain.output.word_count_min} 
à {self.domain.output.word_count_max} mots.
"""

        # Enrichir avec la bibliothèque de compétences de l'agent
        knowledge_context = ""
        try:
            from tools.knowledge.knowledge_engine import KnowledgeEngine
            engine = KnowledgeEngine(agent_slug=self.slug)
            knowledge_context = await engine.get_context(topic)
        except Exception:
            pass  # Non bloquant — fonctionne sans bibliothèque

        user_prompt = f"""Sujet : {topic}

{f"Contexte fourni : {context}" if context else ""}
{f"Bibliothèque de référence :{chr(10)}{knowledge_context}" if knowledge_context else ""}

Produis le {content_type} maintenant."""

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model":  self.ollama_model,
                        "prompt": f"{system_prompt}\n\n{user_prompt}",
                        "stream": False,
                        "options": {"temperature": 0.7, "num_predict": 1500},
                    }
                )
                if resp.status_code == 200:
                    content = resp.json().get("response", "").strip()
                    self.content_count += 1
                    self.last_active = datetime.utcnow()
                    logger.info(f"[{self.name}] {content_type} produit · {len(content.split())} mots")
                    return {
                        "agent":   self.slug,
                        "name":    self.name,
                        "type":    content_type,
                        "topic":   topic,
                        "content": content,
                        "status":  "produced",
                    }
        except Exception as e:
            logger.error(f"[{self.name}] Erreur production : {e}")

        return {"agent": self.slug, "status": "error", "type": content_type}

    async def validate(self, content: str) -> dict:
        """
        Valide un contenu (uniquement pour les agents role=validation).
        Retourne un score et un feedback.
        """
        if self.role != "validation":
            raise ValueError(f"{self.name} n'a pas le rôle 'validation'")

        vocab = self.domain.ui.vocabulary
        score_label = vocab.score_label

        prompt = f"""{self.prompt}

Évalue ce {vocab.content_unit} sur 100 points ({score_label}).
Critères : qualité, pertinence, conformité aux règles, clarté.

Réponds UNIQUEMENT avec ce format JSON :
{{
  "score": <0-100>,
  "decision": "approve" ou "reject",
  "feedback": "<raison courte>"
}}

{vocab.content_unit.capitalize()} à évaluer :
{content[:2000]}"""

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model":  self.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 200},
                    }
                )
                if resp.status_code == 200:
                    import json, re
                    raw = resp.json().get("response", "")
                    match = re.search(r'\{.*?\}', raw, re.DOTALL)
                    if match:
                        result = json.loads(match.group())
                        approved = result.get("score", 0) >= self.domain.output.validation_score_min
                        result["decision"] = "approve" if approved else "reject"
                        logger.info(f"[{self.name}] Validation : score {result.get('score')} → {result['decision']}")
                        return result
        except Exception as e:
            logger.error(f"[{self.name}] Erreur validation : {e}")

        return {"score": 0, "decision": "reject", "feedback": "Erreur de validation"}

    async def speak(self, context: str) -> str:
        """
        Prend la parole dans une réunion.
        """
        prompt = f"""{self.prompt}

Contexte de la réunion : {context}

Tu interviens brièvement (2-3 phrases max). 
Reste dans ton rôle de {self.title}."""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model":  self.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.6, "num_predict": 150},
                    }
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"[{self.name}] Erreur prise de parole : {e}")
        return ""

    def to_dict(self) -> dict:
        """Sérialise l'agent pour l'API ou la DB."""
        return {
            "slug":          self.slug,
            "name":          self.name,
            "agent_id":      self.agent_id,
            "title":         self.title,
            "role":          self.role,
            "specialty":     self.specialty,
            "email":         self.email_address,
            "status":        self.status,
            "content_count": self.content_count,
            "avg_score":     self.avg_score,
            "domain":        self.domain.domain,
            "prompt":        self.prompt,
        }
