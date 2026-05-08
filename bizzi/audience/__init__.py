"""bizzi.audience — Capteur d'opinion publique multi-source UNIVERSEL.

Engine 100% agnostique : aucune valeur sectorielle codée en dur. Les
catégories métier, les boosts de priorité et les types de propositions
de contenu sont entièrement portés par le YAML tenant.

Capacités :
- Ingestion : chatbot, commentaires FB, formulaires, webhooks, email (Phase 1)
- NLP : nettoyage + anonymisation PII, analyse LLM gpt-4o-mini, embedding
  pgvector (fallback BYTEA)
- Storage : audience_reports + audience_trends incrémentaux + audit embed
- Tendances temps réel + détection alertes (paramétrable par tenant)
- Génération automatique de propositions (Phase 1 : délègue à bizzi.social
  pour le video_clip / facebook_post)

Multi-tenant : config par tenant via domains/<tenant>.yaml section `audience:`.
Iframe white-label scopé par JWT HS256 (cf. bizzi.audience.embed).

═══════════════════════════════════════════════════════════════════
RÈGLE PASCAL — STRATÉGIE STRICTEMENT ADDITIVE
═══════════════════════════════════════════════════════════════════
Le moteur ne remplace JAMAIS une feature existante côté tenant. Il
s'ajoute par-dessus :
  • chatbots déjà en prod (ex: lesdemocrates-probleme-ville.html appelle
    api.anthropic directement) → ajouter un fetch parallèle
    `POST /api/audience/ingest` APRÈS l'appel existant, ne rien retirer.
  • panels existants avec leur propre logique (ex: panel-projets a
    `analyserProjet()`) → AJOUTER un POST en parallèle, ne pas remplacer.
  • containers vides ou placeholder → bizzi-loader.js parse
    `data-bizzi-mount="audience/<scope>"` et y injecte une iframe.

Bizzi NE CRÉE AUCUN container HTML côté tenant. Le tenant ajoute
l'attribut data-* sur ses containers existants — c'est tout.

Endpoints REST : voir bizzi.audience.routes (préfixe /api/audience).
Endpoints embed : voir bizzi.audience.embed (préfixe /embed/audience).
"""
from ._db import ensure_schema
from .storage import insert_report, get_report, list_reports
from .nlp.analyzer import analyze
from .nlp.cleaner import clean_and_anonymize
from .nlp.embedder import embed

__all__ = [
    "ensure_schema",
    "insert_report", "get_report", "list_reports",
    "analyze", "clean_and_anonymize", "embed",
]
