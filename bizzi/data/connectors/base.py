"""bizzi.data.connectors.base — Interface DataConnector abstraite.

Toute source de données (Postgres, MySQL, REST, GraphQL, Sheets, Airtable…)
implémente cette interface. Les agents Bizzi parlent UNIQUEMENT à cette
interface ; ils ignorent la nature physique du stockage du tenant.

Sécurité : par défaut un connecteur est en `ConnectorScope.READ_ONLY`.
write_record() lève ConnectorError si scope=read_only. Le tenant doit
déclarer explicitement `scope: read_write` dans son YAML pour autoriser
les écritures, et même là chaque entité doit être listée dans
`semantic_schema.<entity>.writable: true`.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


class ConnectorScope(str, enum.Enum):
    READ_ONLY  = "read_only"
    READ_WRITE = "read_write"


class ConnectorError(Exception):
    """Erreur côté connecteur (config, scope, requête malformée, etc.)."""


@dataclass
class EntityRef:
    """Référence à une entité du schéma sémantique du tenant.

    name = nom logique déclaré dans semantic_schema (ex: 'article', 'dossier')
    physical_name = nom physique dans la source (table, sheet tab, endpoint REST)
    """
    name: str
    physical_name: Optional[str] = None
    fields: list[str] = field(default_factory=list)


@dataclass
class ViewQuery:
    """Représentation neutre d'une requête.

    Le YAML du tenant peut déclarer une view de plusieurs façons :
      - sql:        requête SQL paramétrée (Postgres / MySQL / bizzi_managed)
      - graphql:    requête GraphQL paramétrée
      - rest:       chemin + méthode + body template
      - sheet:      onglet + range + filtre

    Le connecteur regarde le champ qu'il sait exécuter et ignore les autres.
    """
    name: str
    sql: Optional[str] = None
    graphql: Optional[str] = None
    rest: Optional[dict[str, Any]] = None
    sheet: Optional[dict[str, Any]] = None
    params: dict[str, Any] = field(default_factory=dict)
    pii_mask: list[str] = field(default_factory=list)  # champs à masquer dans le résultat


@dataclass
class WriteResult:
    success: bool
    entity:  str
    rows_affected: int = 0
    inserted_id: Optional[Any] = None
    error: Optional[str] = None


class DataConnector(ABC):
    """Contrat commun à tous les connecteurs de bizzi.data.

    Conventions :
      - Toutes les méthodes peuvent lever ConnectorError.
      - Les retours sont des `list[dict[str, Any]]` plats (pas d'ORM).
      - PII masking est appliqué par le connecteur après exécution si la
        ViewQuery liste des champs sensibles.
    """

    def __init__(self, source_config: dict[str, Any]):
        self.source_config = source_config
        self.source_id = source_config.get("id", "unnamed")
        scope_raw = source_config.get("scope", ConnectorScope.READ_ONLY.value)
        try:
            self.scope = ConnectorScope(scope_raw)
        except ValueError:
            raise ConnectorError(
                f"scope invalide pour source {self.source_id!r} : {scope_raw!r} "
                f"(attendu : 'read_only' ou 'read_write')"
            )

    # ── Capabilities ─────────────────────────────────────────────
    @property
    def supports_sql(self) -> bool:
        return False

    @property
    def supports_graphql(self) -> bool:
        return False

    @property
    def supports_rest(self) -> bool:
        return False

    # ── Lecture ──────────────────────────────────────────────────
    @abstractmethod
    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lit N enregistrements d'une entité. Filtres simples key=value (AND).

        Implémentations doivent traduire `entity.physical_name` vers leur
        adressage natif (table SQL, onglet Sheets, endpoint REST...).
        """

    @abstractmethod
    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        """Exécute une vue prédéfinie (cf. semantic_views du YAML)."""

    # ── Écriture ─────────────────────────────────────────────────
    def write_record(
        self,
        entity: EntityRef,
        data: dict[str, Any],
        scope: ConnectorScope = ConnectorScope.READ_ONLY,
    ) -> WriteResult:
        """Écriture optionnelle. Le caller DOIT passer scope=READ_WRITE
        explicitement, et le connecteur DOIT être configuré en read_write.

        Override dans les connecteurs qui implémentent l'écriture.
        """
        if scope != ConnectorScope.READ_WRITE:
            raise ConnectorError(
                f"write_record sur entité {entity.name!r} refusé : "
                f"scope explicite requis (READ_WRITE)"
            )
        if self.scope != ConnectorScope.READ_WRITE:
            raise ConnectorError(
                f"Source {self.source_id!r} configurée en READ_ONLY — "
                f"write_record interdit"
            )
        raise NotImplementedError(
            f"Connecteur {type(self).__name__} ne supporte pas l'écriture"
        )

    # ── Diagnostics ──────────────────────────────────────────────
    def health_check(self) -> dict[str, Any]:
        """Ping de la source. Override conseillé."""
        return {"source_id": self.source_id, "type": type(self).__name__, "ok": True}

    def close(self) -> None:
        """Libère les ressources (pool, fichier, etc.). No-op par défaut."""

    # ── Helpers ──────────────────────────────────────────────────
    @staticmethod
    def apply_pii_mask(rows: list[dict[str, Any]], mask_fields: list[str]) -> list[dict[str, Any]]:
        """Remplace les champs sensibles par '***' dans la sortie.

        Masquage simple — pour tokenization sérieuse, prévoir un module
        bizzi.data.privacy en Phase 1.
        """
        if not mask_fields:
            return rows
        masked = []
        for r in rows:
            r2 = dict(r)
            for f in mask_fields:
                if f in r2 and r2[f] is not None:
                    r2[f] = "***"
            masked.append(r2)
        return masked
