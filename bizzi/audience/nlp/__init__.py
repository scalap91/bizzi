"""bizzi.audience.nlp — Pipeline texte universel.

cleaner   : strip URLs + masquage PII (email, tél intl, IBAN, carte)
analyzer  : gpt-4o-mini avec liste de catégories injectée depuis YAML
embedder  : wrapper de bizzi.data.memory_vector._embed (réutilisation)
"""
from .cleaner import clean_and_anonymize
from .analyzer import analyze
from .embedder import embed

__all__ = ["clean_and_anonymize", "analyze", "embed"]
