"""bizzi.data.connectors.graphql — Connecteur GraphQL générique (httpx sync, stub).

Phase 0 : implémentation minimale POST { query, variables }. Pas de
validation de schéma. Suffit pour la plupart des CRM modernes (Hasura,
Strapi, GitHub API, etc.).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from .base import (
    DataConnector, ConnectorError, EntityRef, ViewQuery, WriteResult,
    ConnectorScope,
)


def _resolve_secret(value: str) -> str:
    if isinstance(value, str) and value.startswith("env:"):
        env_var = value[4:]
        v = os.environ.get(env_var)
        if v is None:
            raise ConnectorError(f"Variable d'env {env_var!r} non définie")
        return v
    return value


class GraphQLConnector(DataConnector):
    """source_config :
        id:        "hasura_main"
        type:      "graphql"
        scope:     "read_only"
        endpoint:  "https://hasura.tenant.com/v1/graphql"
        headers:
          x-hasura-admin-secret: "env:HASURA_ADMIN_SECRET"
        timeout_sec: 15
    """

    @property
    def supports_graphql(self) -> bool:
        return True

    def __init__(self, source_config: dict[str, Any]):
        super().__init__(source_config)
        self.endpoint = source_config.get("endpoint") or source_config.get("url")
        if not self.endpoint:
            raise ConnectorError(f"GraphQL source {self.source_id!r} : endpoint manquant")
        self.timeout = float(source_config.get("timeout_sec", 15))
        raw_headers = source_config.get("headers") or {}
        self.headers = {k: _resolve_secret(v) for k, v in raw_headers.items()}

    def _post(self, query: str, variables: Optional[dict[str, Any]] = None) -> Any:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                self.endpoint,
                headers=self.headers,
                json={"query": query, "variables": variables or {}},
            )
            r.raise_for_status()
            data = r.json()
        if "errors" in data:
            raise ConnectorError(f"GraphQL errors: {data['errors']}")
        return data.get("data", {})

    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        # Convention : la query est `query GetX($limit: Int, $offset: Int) { x { ... } }`
        # En Phase 0 on ne propose pas d'auto-build : passer par query_view.
        raise ConnectorError(
            "GraphQLConnector.read_entity non auto-implémenté en Phase 0. "
            "Déclarer une semantic_view avec champ 'graphql' à la place."
        )

    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        if not query.graphql:
            raise ConnectorError(
                f"View {query.name!r} : champ 'graphql' requis pour GraphQLConnector"
            )
        if self.scope == ConnectorScope.READ_ONLY:
            # mutation interdite si read_only
            stripped = query.graphql.lstrip()
            if stripped.lower().startswith("mutation"):
                raise ConnectorError(
                    f"View {query.name!r} : mutation GraphQL interdite "
                    f"(source {self.source_id!r} en read_only)"
                )
        data = self._post(query.graphql, query.params)
        rows: list[dict[str, Any]]
        if isinstance(data, dict) and len(data) == 1:
            sole = next(iter(data.values()))
            rows = sole if isinstance(sole, list) else [sole] if sole is not None else []
        elif isinstance(data, list):
            rows = data
        else:
            rows = [data] if data else []
        if query.pii_mask:
            rows = self.apply_pii_mask(rows, query.pii_mask)
        return rows

    def health_check(self) -> dict[str, Any]:
        try:
            self._post("query { __typename }")
            return {"source_id": self.source_id, "type": "graphql", "ok": True}
        except Exception as e:  # noqa: BLE001
            return {"source_id": self.source_id, "type": "graphql",
                    "ok": False, "error": str(e)}
