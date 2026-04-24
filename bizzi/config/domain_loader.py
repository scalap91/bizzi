"""
config/domain_loader.py
========================
Lit un fichier de domaine .yaml et configure
tout le moteur automatiquement.

Usage :
    loader = DomainLoader("domains/media.yaml")
    config = loader.load()
    print(config.vocabulary.content_unit)  # "article"
    print(config.pipeline.steps)           # ["scrape", "write", ...]
"""

import yaml
import os
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════
# MODÈLES DE CONFIGURATION
# ══════════════════════════════════════════════════════════════

@dataclass
class AgentConfig:
    id:          str
    title:       str
    role:        str
    required:    bool
    prompt_base: str
    min_count:   int = 1
    max_count:   int = 1


@dataclass
class PipelineConfig:
    schedule: str
    steps:    list[str]


@dataclass
class OutputConfig:
    type:                 str
    word_count_min:       int
    word_count_max:       int
    validation_score_min: int
    formats:              list[str]


@dataclass
class VocabularyConfig:
    content_unit:  str
    content_units: str
    producer:      str
    producers:     str
    validator:     str
    meeting_room:  str
    workspace:     str
    source:        str
    output_verb:   str
    trash_reason:  str
    score_label:   str


@dataclass
class UIConfig:
    primary_color: str
    vocabulary:    VocabularyConfig


@dataclass
class DomainConfig:
    """Configuration complète d'un domaine."""
    domain:          str
    version:         str
    name:            str
    tagline:         str
    language:        str
    timezone:        str
    agents:          list[AgentConfig]
    pipeline:        PipelineConfig
    output:          OutputConfig
    editorial_rules: list[str]
    ui:              UIConfig

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """Récupère un agent par son ID."""
        return next((a for a in self.agents if a.id == agent_id), None)

    def get_agents_by_role(self, role: str) -> list[AgentConfig]:
        """Récupère tous les agents d'un rôle donné."""
        return [a for a in self.agents if a.role == role]

    def build_prompt(self, agent_id: str, **kwargs) -> str:
        """
        Construit le prompt d'un agent en remplaçant les variables.

        Variables disponibles :
            {org_name}            → nom de l'organisation
            {agent_name}          → nom de l'agent
            {specialty}           → spécialité de l'agent
            {word_count_min}      → longueur minimale du contenu
            {word_count_max}      → longueur maximale du contenu
            {validation_score_min}→ score minimum de validation
        """
        agent = self.get_agent(agent_id)
        if not agent:
            return ""

        context = {
            "org_name":            self.name,
            "word_count_min":      self.output.word_count_min,
            "word_count_max":      self.output.word_count_max,
            "validation_score_min": self.output.validation_score_min,
            **kwargs,
        }

        prompt = agent.prompt_base
        for key, value in context.items():
            prompt = prompt.replace("{" + key + "}", str(value))

        return prompt


# ══════════════════════════════════════════════════════════════
# CHARGEUR DE DOMAINE
# ══════════════════════════════════════════════════════════════

class DomainLoader:
    """
    Lit un fichier .yaml et retourne une DomainConfig complète.
    Le moteur utilise cette config pour tout adapter automatiquement.
    """

    DOMAINS_DIR = os.path.join(os.path.dirname(__file__), '..', 'domains')

    def __init__(self, domain_name: str):
        """
        Args:
            domain_name : nom du domaine ("media", "politics", "diagnostic")
                          ou chemin complet vers un fichier .yaml
        """
        if domain_name.endswith('.yaml'):
            self.path = domain_name
        else:
            self.path = os.path.join(self.DOMAINS_DIR, f"{domain_name}.yaml")

        if not os.path.exists(self.path):
            raise FileNotFoundError(
                f"Domaine '{domain_name}' introuvable.\n"
                f"Chemin cherché : {self.path}\n"
                f"Domaines disponibles : {self.list_available()}"
            )

    def load(self) -> DomainConfig:
        """Charge et valide la configuration du domaine."""
        with open(self.path, encoding='utf-8') as f:
            data = yaml.safe_load(f)

        return DomainConfig(
            domain  = data['domain'],
            version = data.get('version', '1.0'),
            name    = data['identity']['name'],
            tagline = data['identity'].get('tagline', ''),
            language= data['identity'].get('language', 'fr'),
            timezone= data['identity'].get('timezone', 'Europe/Paris'),

            agents = [
                AgentConfig(
                    id          = a['id'],
                    title       = a['title'],
                    role        = a['role'],
                    required    = a.get('required', False),
                    prompt_base = a['prompt_base'],
                    min_count   = a.get('min_count', 1),
                    max_count   = a.get('max_count', 1),
                )
                for a in data.get('agents', [])
            ],

            pipeline = PipelineConfig(
                schedule = data['pipeline']['schedule'],
                steps    = data['pipeline']['steps'],
            ),

            output = OutputConfig(
                type                 = data['output']['type'],
                word_count_min       = data['output']['word_count_min'],
                word_count_max       = data['output']['word_count_max'],
                validation_score_min = data['output']['validation_score_min'],
                formats              = data['output']['formats'],
            ),

            editorial_rules = data.get('editorial_rules', []),

            ui = UIConfig(
                primary_color = data['ui']['primary_color'],
                vocabulary    = VocabularyConfig(**data['ui']['vocabulary']),
            ),
        )

    @classmethod
    def list_available(cls) -> list[str]:
        """Liste tous les domaines disponibles."""
        if not os.path.exists(cls.DOMAINS_DIR):
            return []
        return [
            f.replace('.yaml', '')
            for f in os.listdir(cls.DOMAINS_DIR)
            if f.endswith('.yaml')
        ]

    @classmethod
    def load_domain(cls, domain_name: str) -> DomainConfig:
        """Raccourci : DomainLoader.load_domain('media')"""
        return cls(domain_name).load()
