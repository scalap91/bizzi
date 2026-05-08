"""bizzi.data.connectors.bizzi_managed — Connecteur DB managée par Bizzi.

Pour les tenants qui ne possèdent pas leur propre DB. Bizzi héberge alors
des tables `bizzi_managed_<tenant_slug>_<entity>` dans la DB `bizzi` et
expose un connecteur SQL prêt à l'emploi sans config.

C'est le mode 'plug & play' : aucun système externe à brancher, le tenant
voit juste des entités logiques (clients, factures, projets…) et leur
schéma sémantique.

Implémentation : sous-classe de PostgresConnector qui injecte
automatiquement la DSN bizzi et préfixe les noms de tables.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from .base import EntityRef, ViewQuery, ConnectorError
from .postgresql import PostgresConnector


class BizziManagedConnector(PostgresConnector):
    """source_config :
        id:           "bizzi_managed"
        type:         "bizzi_managed"
        scope:        "read_write"          # par défaut RW, c'est notre DB
        tenant_slug:  "lesdemocrates"        # injecté par le loader
        # DSN auto-résolue depuis l'env DATABASE_URL si non fourni.
    """

    def __init__(self, source_config: dict[str, Any]):
        cfg = dict(source_config)
        cfg.setdefault("scope", "read_write")
        # Auto-résolution DSN depuis DATABASE_URL si absent.
        if "dsn" not in cfg and "host" not in cfg:
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                raise ConnectorError(
                    "BizziManagedConnector : ni dsn/host fournis, "
                    "ni DATABASE_URL en env"
                )
            cfg["dsn"] = db_url
        super().__init__(cfg)
        self.tenant_slug = cfg.get("tenant_slug")
        if not self.tenant_slug:
            raise ConnectorError("BizziManagedConnector : tenant_slug requis")

    def _physical_name(self, entity_name: str) -> str:
        # Convention : table physique = bizzi_mgd_<tenant_slug>_<entity>
        # avec normalisation des caractères non-[a-z0-9_].
        slug = "".join(
            c if c.isalnum() or c == "_" else "_"
            for c in self.tenant_slug.lower()
        )
        ent = "".join(
            c if c.isalnum() or c == "_" else "_"
            for c in entity_name.lower()
        )
        return f"bizzi_mgd_{slug}_{ent}"

    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        # Si physical_name pas fourni, on l'auto-construit depuis le slug+entity.
        if not entity.physical_name:
            entity = EntityRef(
                name=entity.name,
                physical_name=self._physical_name(entity.name),
                fields=entity.fields,
            )
        return super().read_entity(entity, filters=filters, limit=limit, offset=offset)
