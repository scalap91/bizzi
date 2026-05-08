"""bizzi.data.connectors.airtable — Connecteur Airtable (REST officiel).

Cas d'usage typique : un parti politique ou une PME qui gère ses
membres / militants / dossiers / contacts dans Airtable. Le tenant
déclare son base_id + table_name dans data_sources, et bizzi.data
expose les enregistrements comme une entité standard.

API officielle : https://airtable.com/developers/web/api/list-records
Auth : PAT (Personal Access Token) via header Authorization Bearer.
Rate limit : 5 requêtes/sec/base. Ce connecteur ne gère pas le retry —
le caller doit gérer la 429 (Phase 2).
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

import httpx

from .base import (
    DataConnector, ConnectorError, EntityRef, ViewQuery, WriteResult,
    ConnectorScope,
)


def _resolve_secret(value: str) -> str:
    if isinstance(value, str) and value.startswith("env:"):
        v = os.environ.get(value[4:])
        if v is None:
            raise ConnectorError(f"env var {value[4:]!r} non définie")
        return v
    return value


_BASE_ID_RE  = re.compile(r"^app[a-zA-Z0-9]{14,}$")


class AirtableConnector(DataConnector):
    """source_config :
        id:            "airtable_membres"
        type:          "airtable"
        scope:         "read_only"
        base_id:       "appXXXXXXXXXXXXXX"
        token:         "env:AIRTABLE_PAT"
        timeout_sec:   15
        # Mapping entité → nom de table Airtable
        entities:
          membre:
            table_name: "Membres"
          dossier:
            table_name: "Dossiers"
            view: "Active"           # filtrer par vue Airtable côté serveur
    """

    @property
    def supports_rest(self) -> bool:
        return True

    def __init__(self, source_config: dict[str, Any]):
        super().__init__(source_config)
        self.base_id = source_config.get("base_id", "")
        if not _BASE_ID_RE.match(self.base_id):
            raise ConnectorError(
                f"Airtable source {self.source_id!r} : base_id invalide {self.base_id!r}"
            )
        self.token = _resolve_secret(source_config.get("token", ""))
        if not self.token:
            raise ConnectorError(f"Airtable source {self.source_id!r} : token manquant")
        self.timeout = float(source_config.get("timeout_sec", 15))
        self.entities_map: dict[str, dict[str, Any]] = source_config.get("entities", {}) or {}
        self.api_base = "https://api.airtable.com/v0"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=f"{self.api_base}/{self.base_id}",
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    @staticmethod
    def _flatten(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convertit la réponse Airtable en rows plats : {id: ..., ...fields}."""
        out = []
        for r in records:
            row = dict(r.get("fields") or {})
            row["_airtable_id"] = r.get("id")
            row["_created_time"] = r.get("createdTime")
            out.append(row)
        return out

    def _table_for_entity(self, entity: EntityRef) -> tuple[str, dict[str, Any]]:
        spec = self.entities_map.get(entity.name) or {}
        table = spec.get("table_name") or entity.physical_name
        if not table:
            raise ConnectorError(
                f"Airtable {self.source_id!r} : pas de table_name pour {entity.name!r}"
            )
        return table, spec

    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        table, spec = self._table_for_entity(entity)
        params: dict[str, Any] = {"pageSize": min(int(limit), 100)}
        if spec.get("view"):
            params["view"] = spec["view"]
        if filters:
            # Airtable filterByFormula : on construit une AND() simple.
            # AND({field}='value', {f2}=42)
            clauses = []
            for k, v in filters.items():
                if isinstance(v, str):
                    safe = v.replace("'", "\\'")
                    clauses.append(f"{{{k}}}='{safe}'")
                elif isinstance(v, bool):
                    clauses.append(f"{{{k}}}={'TRUE()' if v else 'FALSE()'}")
                elif isinstance(v, (int, float)):
                    clauses.append(f"{{{k}}}={v}")
                else:
                    clauses.append(f"{{{k}}}='{str(v)}'")
            if clauses:
                params["filterByFormula"] = "AND(" + ",".join(clauses) + ")"

        # Airtable n'a pas d'offset numérique : il utilise un cursor `offset`
        # opaque. Pour Phase 1 on fait au plus 1 page (jusqu'à 100 records).
        with self._client() as c:
            r = c.get(f"/{table}", params=params)
            r.raise_for_status()
            data = r.json()
        rows = self._flatten(data.get("records", []))
        # Champs demandés ?
        if entity.fields:
            wanted = set(entity.fields)
            wanted.update({"_airtable_id"})
            rows = [{k: v for k, v in r.items() if k in wanted} for r in rows]
        return rows[: int(limit)]

    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        """Une view Airtable = `rest:{path: '/Table?filterByFormula=...'}`.

        Phase 1 : on accepte `query.rest` au format identique au RestAPIConnector.
        """
        rest = query.rest or {}
        if not rest:
            raise ConnectorError(
                f"View {query.name!r} : champ 'rest' requis pour AirtableConnector"
            )
        method = (rest.get("method") or "GET").upper()
        if self.scope == ConnectorScope.READ_ONLY and method not in ("GET", "HEAD"):
            raise ConnectorError(
                f"View {query.name!r} : méthode {method} interdite (read_only)"
            )
        path = rest.get("path")
        if not path:
            raise ConnectorError(f"View {query.name!r} : rest.path manquant")
        try:
            path = path.format(**(query.params or {}))
        except KeyError as e:
            raise ConnectorError(f"View {query.name!r} : param manquant {e}")
        params = rest.get("query_params") or {}
        params = {k: (v.format(**(query.params or {})) if isinstance(v, str) else v)
                  for k, v in params.items()}

        with self._client() as c:
            r = c.request(method, path, params=params)
            r.raise_for_status()
            data = r.json()
        rows = self._flatten(data.get("records", [])) if isinstance(data, dict) else []
        if query.pii_mask:
            rows = self.apply_pii_mask(rows, query.pii_mask)
        return rows

    def health_check(self) -> dict[str, Any]:
        try:
            # Ping : on récupère la 1ère table connue ou base meta.
            entity_name = next(iter(self.entities_map.keys()), None)
            if not entity_name:
                return {"source_id": self.source_id, "type": "airtable",
                        "ok": True, "warning": "aucune entité déclarée"}
            with self._client() as c:
                spec = self.entities_map[entity_name]
                table = spec.get("table_name") or entity_name
                r = c.get(f"/{table}", params={"maxRecords": 1})
            return {"source_id": self.source_id, "type": "airtable",
                    "ok": r.status_code < 500, "status": r.status_code}
        except Exception as e:  # noqa: BLE001
            return {"source_id": self.source_id, "type": "airtable",
                    "ok": False, "error": str(e)}
