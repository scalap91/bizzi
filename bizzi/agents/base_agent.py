

"""
agents/base_agent.py
=====================
Classe de base pour tous les agents Bizzi.
Refactor 24/04/2026 : OpenAI GPT-4o mini (ex-Ollama).
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from openai import AsyncOpenAI
from config.domain_loader import DomainConfig

logger = logging.getLogger("core.agent")


_OPENAI_CLIENT = None

def _get_openai_client() -> AsyncOpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        try:
            with open("/opt/bizzi/bizzi/.env") as f:
                key = f.read().split("OPENAI_API_KEY=")[1].split("\n")[0].strip()
        except Exception as e:
            logger.error(f"[OPENAI] Cle introuvable: {e}")
            key = os.getenv("OPENAI_API_KEY", "")
        _OPENAI_CLIENT = AsyncOpenAI(api_key=key)
    return _OPENAI_CLIENT


SEO_PAGE_STRUCTURE = """Tu dois produire le contenu en DEUX BLOCS separes UNIQUEMENT par ---BODY--- :

BLOC 1 - meta description (150 caracteres MAX) :
- Mots cles en tete
- Percutant, donne envie de cliquer depuis Google
- Chiffres locaux si disponibles
- Maximum 150 caracteres

---BODY---

BLOC 2 - corps du texte (80 a 150 mots) :
- Contexte local avec donnees reelles
- Obligations reglementaires
- Types de biens concernes
- CTA naturel avec nom du franchise et telephone
- Ne pas repeter le BLOC 1

Respecte strictement ce format."""


@dataclass
class Agent:
    slug: str
    name: str
    agent_id: str
    domain: DomainConfig

    specialty: str = ""
    email: str = ""
    status: str = "active"
    custom_prompt: str = ""

    twitter_token: str = ""
    linkedin_token: str = ""
    instagram_token: str = ""
    tiktok_token: str = ""

    content_count: int = 0
    avg_score: float = 0.0
    last_active: Optional[datetime] = None

    openai_model: str = "gpt-4o-mini"

    def __post_init__(self):
        self.last_active = datetime.utcnow()

    @property
    def prompt(self) -> str:
        if self.custom_prompt:
            return self.custom_prompt
        return self.domain.build_prompt(
            self.agent_id,
            agent_name=self.name,
            specialty=self.specialty,
        )

    @property
    def title(self) -> str:
        agent_cfg = self.domain.get_agent(self.agent_id)
        return agent_cfg.title if agent_cfg else self.agent_id

    @property
    def role(self) -> str:
        agent_cfg = self.domain.get_agent(self.agent_id)
        return agent_cfg.role if agent_cfg else ""

    @property
    def email_address(self) -> str:
        if self.email:
            return self.email
        slug_email = self.slug.replace('-', '.')
        return f"{slug_email}@{self.domain.name.lower().replace(' ', '')}.fr"

    async def _ask_openai(self, system: str, user: str, temperature: float = 0.7, max_tokens: int = 1500, json_mode: bool = False) -> str:
        try:
            client = _get_openai_client()
            kwargs = {
                "model": self.openai_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[{self.name}] OpenAI error: {e}")
            return ""

    async def produce(self, topic: str, context: str = "") -> dict:
        vocab = self.domain.ui.vocabulary
        content_type = vocab.content_unit

        system_prompt = self.prompt + "\n\nRegles de " + self.domain.name + " :\n"
        for r in self.domain.editorial_rules:
            system_prompt += "- " + r + "\n"
        system_prompt += "\nTu produis un " + content_type + " de " + str(self.domain.output.word_count_min) + " a " + str(self.domain.output.word_count_max) + " mots."

        knowledge_context = ""
        try:
            from tools.knowledge.knowledge_engine import KnowledgeEngine
            engine = KnowledgeEngine(agent_slug=self.slug)
            knowledge_context = await engine.get_context(topic)
        except Exception:
            pass

        user_prompt = "Sujet : " + topic
        if context:
            user_prompt += "\n\nContexte fourni : " + context
        if knowledge_context:
            user_prompt += "\n\nBibliotheque de reference :\n" + knowledge_context
        user_prompt += "\n\nProduis le " + content_type + " maintenant."

        content = await self._ask_openai(
            system=system_prompt,
            user=user_prompt,
            temperature=0.7,
            max_tokens=1500,
        )

        if content:
            self.content_count += 1
            self.last_active = datetime.utcnow()
            logger.info(f"[{self.name}] {content_type} produit - {len(content.split())} mots")
            return {
                "agent": self.slug,
                "name": self.name,
                "type": content_type,
                "topic": topic,
                "content": content,
                "status": "produced",
            }
        return {"agent": self.slug, "status": "error", "type": content_type}

    async def validate(self, content: str) -> dict:
        if self.role != "validation":
            raise ValueError(self.name + " n'a pas le role validation")

        vocab = self.domain.ui.vocabulary

        user_prompt = "Evalue ce " + vocab.content_unit + " sur 100 points (" + vocab.score_label + ").\n"
        user_prompt += "Criteres : qualite, pertinence, conformite aux regles, clarte.\n\n"
        user_prompt += "Reponds UNIQUEMENT avec ce format JSON :\n"
        user_prompt += '{"score": <0-100>, "decision": "approve" ou "reject", "feedback": "<raison courte>"}\n\n'
        user_prompt += vocab.content_unit.capitalize() + " a evaluer :\n" + content[:2000]

        raw = await self._ask_openai(
            system=self.prompt,
            user=user_prompt,
            temperature=0.3,
            max_tokens=200,
            json_mode=True,
        )

        if raw:
            try:
                import json
                result = json.loads(raw)
                approved = result.get("score", 0) >= self.domain.output.validation_score_min
                result["decision"] = "approve" if approved else "reject"
                logger.info(f"[{self.name}] Validation : score {result.get('score')} -> {result['decision']}")
                return result
            except Exception as e:
                logger.error(f"[{self.name}] Parse JSON validation: {e}")

        return {"score": 0, "decision": "reject", "feedback": "Erreur validation"}

    async def speak(self, context: str) -> str:
        user_prompt = "Contexte de la reunion : " + context + "\n\nTu interviens brievement (2-3 phrases max). Reste dans ton role de " + self.title + "."
        return await self._ask_openai(
            system=self.prompt,
            user=user_prompt,
            temperature=0.6,
            max_tokens=200,
        )

    async def place(self, title: str, content: str, category: str, valid_regions: list) -> dict:
        """Decide la PLACE d'un article : region francaise (ou null si international/national flou).

        Reserve aux agents de role 'placement'.
        Retourne {"region": "<nom exact ou null>", "confidence": <0-100>, "reason": "<phrase>"}.
        """
        if self.role != "placement":
            raise ValueError(self.name + " n'a pas le role placement")

        regions_csv = " | ".join(valid_regions)
        user_prompt = (
            "Voici un article a placer dans le journal. Decide UNIQUEMENT sa region geographique.\n\n"
            f"TITRE : {title}\n"
            f"CATEGORIE : {category or 'Une'}\n"
            f"CONTENU :\n{(content or '')[:2500]}\n\n"
            "REGLE STRICTE :\n"
            "- region = un nom de cette liste UNIQUEMENT si l'article traite SPECIFIQUEMENT un sujet regional francais (evenement local, decision regionale, fait divers regional, sport club regional, politique locale) :\n"
            f"  {regions_csv}\n"
            "- region = null pour TOUT le reste : sujets nationaux, internationaux, sport europeen ou mondial, business international, politique nationale, articles ou Paris/ville-fr est cite en passant.\n"
            "- En cas de doute, region = null. Mieux vaut ne pas placer que mal placer.\n\n"
            'Reponds STRICTEMENT en JSON : {"region": "<nom exact ou null>", "confidence": <0-100>, "reason": "<une phrase>"}'
        )

        raw = await self._ask_openai(
            system=self.prompt,
            user=user_prompt,
            temperature=0.1,
            max_tokens=150,
            json_mode=True,
        )
        if raw:
            try:
                import json
                result = json.loads(raw)
                region = result.get("region")
                if region in (None, "", "null", "None"):
                    region = None
                elif region not in valid_regions:
                    logger.warning(f"[{self.name}] region '{region}' hors liste, ignoree")
                    region = None
                return {
                    "region": region,
                    "confidence": int(result.get("confidence", 0) or 0),
                    "reason": (result.get("reason") or "")[:200],
                }
            except Exception as e:
                logger.error(f"[{self.name}] Parse JSON placement : {e}")

        return {"region": None, "confidence": 0, "reason": "erreur placement"}

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "agent_id": self.agent_id,
            "title": self.title,
            "role": self.role,
            "specialty": self.specialty,
            "email": self.email_address,
            "status": self.status,
            "content_count": self.content_count,
            "avg_score": self.avg_score,
            "domain": self.domain.domain,
            "prompt": self.prompt,
        }
