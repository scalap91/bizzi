"""bizzi.data.connectors.rest_api — Connecteur REST API générique (httpx sync).

Pour les ERP / CRM maison qui exposent une API REST. La config liste les
endpoints par entité ; les semantic_views référencent ces endpoints.

Authentification supportée Phase 0 :
  - bearer  (token statique env:VAR_NAME)
  - basic   (user/password)
  - apikey  (header configurable)
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
    """Résout 'env:VAR_NAME' -> os.environ['VAR_NAME'], sinon retourne tel quel."""
    if isinstance(value, str) and value.startswith("env:"):
        env_var = value[4:]
        resolved = os.environ.get(env_var)
        if resolved is None:
            raise ConnectorError(f"Variable d'env {env_var!r} non définie")
        return resolved
    return value


class RestAPIConnector(DataConnector):
    """Connecteur REST.

    source_config :
        id:        "erp_main"
        type:      "rest_api"
        scope:     "read_only"
        base_url:  "https://erp.cabinet.fr/api"
        auth:
          type:    "bearer"
          token:   "env:CABINET_ERP_TOKEN"
        timeout_sec: 15
        # Mapping entité logique -> endpoint REST (utilisé par read_entity)
        entities:
          dossier:
            path:    "/dossiers"
            list_method: "GET"
            list_root_field: "items"   # où sont les rows dans la réponse JSON
    """

    @property
    def supports_rest(self) -> bool:
        return True

    def __init__(self, source_config: dict[str, Any]):
        super().__init__(source_config)
        self.base_url = source_config.get("base_url", "").rstrip("/")
        if not self.base_url:
            raise ConnectorError(f"REST source {self.source_id!r} : base_url manquant")
        self.timeout = float(source_config.get("timeout_sec", 15))
        self.entities_map: dict[str, dict[str, Any]] = source_config.get("entities", {}) or {}

        auth_cfg = source_config.get("auth") or {}
        self._auth_headers: dict[str, str] = {}
        if auth_cfg:
            atype = auth_cfg.get("type")
            if atype == "bearer":
                token = _resolve_secret(auth_cfg.get("token", ""))
                self._auth_headers["Authorization"] = f"Bearer {token}"
            elif atype == "apikey":
                header = auth_cfg.get("header", "X-API-Key")
                self._auth_headers[header] = _resolve_secret(auth_cfg.get("value", ""))
            elif atype == "basic":
                # httpx gère via auth=, on stocke pour le client
                self._basic_auth = (
                    auth_cfg.get("user", ""),
                    _resolve_secret(auth_cfg.get("password", "")),
                )
            else:
                raise ConnectorError(f"auth.type inconnu : {atype!r}")
        self._basic_auth = getattr(self, "_basic_auth", None)

    def _client(self) -> httpx.Client:
        kw: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout":  self.timeout,
            "headers":  self._auth_headers or None,
        }
        if self._basic_auth:
            kw["auth"] = self._basic_auth
        return httpx.Client(**{k: v for k, v in kw.items() if v is not None})

    @staticmethod
    def _extract_rows(payload: Any, root: Optional[str]) -> list[dict[str, Any]]:
        if root and isinstance(payload, dict):
            return list(payload.get(root, []))
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return [payload]
        return []

    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        spec = self.entities_map.get(entity.name) or {}
        path = spec.get("path") or entity.physical_name
        if not path:
            raise ConnectorError(
                f"REST source {self.source_id!r} : pas de path pour {entity.name!r} "
                f"(ni dans entities_map, ni en physical_name)"
            )
        method = (spec.get("list_method") or "GET").upper()
        root = spec.get("list_root_field")
        params = dict(filters or {})
        params.setdefault("limit", limit)
        params.setdefault("offset", offset)

        with self._client() as c:
            r = c.request(method, path, params=params)
            r.raise_for_status()
            return self._extract_rows(r.json(), root)

    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        rest = query.rest or {}
        if not rest:
            raise ConnectorError(
                f"View {query.name!r} : champ 'rest' requis pour RestAPIConnector"
            )
        method = (rest.get("method") or "GET").upper()
        path = rest.get("path")
        if not path:
            raise ConnectorError(f"View {query.name!r} : rest.path manquant")
        # Substitution des paramètres dans path et body via .format(**params).
        try:
            path = path.format(**(query.params or {}))
        except KeyError as e:
            raise ConnectorError(f"View {query.name!r} : param manquant {e}")
        body = rest.get("body")
        params = rest.get("query_params") or {}
        # Substitution simple dans query_params
        params = {k: (v.format(**(query.params or {})) if isinstance(v, str) else v)
                  for k, v in params.items()}
        root = rest.get("root_field")

        # Garde-fou scope
        if self.scope == ConnectorScope.READ_ONLY and method not in ("GET", "HEAD"):
            raise ConnectorError(
                f"View {query.name!r} : méthode {method} interdite "
                f"(source {self.source_id!r} en read_only)"
            )

        with self._client() as c:
            r = c.request(method, path, params=params, json=body)
            r.raise_for_status()
            rows = self._extract_rows(r.json(), root)
        if query.pii_mask:
            rows = self.apply_pii_mask(rows, query.pii_mask)
        return rows

    def health_check(self) -> dict[str, Any]:
        try:
            with self._client() as c:
                # GET sur base_url ou /health si déclaré.
                hp = self.source_config.get("health_path", "/")
                r = c.get(hp)
            return {"source_id": self.source_id, "type": "rest_api",
                    "ok": r.status_code < 500, "status": r.status_code}
        except Exception as e:  # noqa: BLE001
            return {"source_id": self.source_id, "type": "rest_api",
                    "ok": False, "error": str(e)}
