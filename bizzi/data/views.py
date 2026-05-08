"""bizzi.data.views — Exécution des semantic_views.

Pipeline :
    tenant_slug + view_name + params
        → load_data_config(tenant_slug)
        → résoudre la source (view.source ou source par défaut)
        → instancier le DataConnector
        → construire ViewQuery (sql/graphql/rest selon ce que le connecteur supporte)
        → connector.query_view(query)
        → return list[dict]

La sécurité (read_only par défaut, masquage PII) est appliquée par le
connecteur, pas ici.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .connectors import get_connector
from .connectors.base import ConnectorError, ViewQuery
from .semantic import SemanticSchema, SemanticView, load_data_config


# Cache résultat très simple : (tenant, view, hashable_params) -> (ts, rows)
_RESULT_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def _cache_key(tenant: str, view: str, params: dict[str, Any]) -> tuple:
    try:
        items = tuple(sorted(params.items()))
    except Exception:  # noqa: BLE001
        items = (("__nohash__", str(params)),)
    return (tenant, view, items)


def _resolve_source_id(schema: SemanticSchema, view: SemanticView) -> str:
    """Détermine la source à utiliser pour une vue.

    Ordre de résolution :
      1. view.source explicite
      2. unique source si une seule déclarée
      3. erreur
    """
    if view.source:
        return view.source
    if len(schema.sources) == 1:
        return next(iter(schema.sources.keys()))
    raise ConnectorError(
        f"View {view.name!r} : pas de 'source' déclarée et plusieurs "
        f"data_sources existent. Précise `source: <id>` dans le YAML."
    )


def _build_view_query(view: SemanticView, params: dict[str, Any]) -> ViewQuery:
    return ViewQuery(
        name     = view.name,
        sql      = view.sql,
        graphql  = view.graphql,
        rest     = view.rest,
        sheet    = view.sheet,
        params   = params,
        pii_mask = list(view.pii_mask),
    )


def execute_view(
    tenant_slug: str,
    view_name:   str,
    params:      Optional[dict[str, Any]] = None,
    use_cache:   bool = True,
) -> list[dict[str, Any]]:
    """Exécute une vue sémantique pour un tenant.

    Lève ValueError si la vue n'existe pas, ConnectorError si la source
    est mal configurée ou si la requête échoue.
    """
    schema = load_data_config(tenant_slug)
    view = schema.view(view_name)
    if view is None:
        raise ValueError(
            f"View {view_name!r} inconnue pour tenant {tenant_slug!r}. "
            f"Vues disponibles : {sorted(schema.views.keys())}"
        )

    final_params = view.validate_params(params or {})

    # Cache lookup
    ck = _cache_key(tenant_slug, view_name, final_params)
    if use_cache and view.cache_ttl_sec > 0:
        hit = _RESULT_CACHE.get(ck)
        if hit and (time.time() - hit[0]) < view.cache_ttl_sec:
            return hit[1]

    src_id = _resolve_source_id(schema, view)
    src_cfg = schema.sources[src_id].to_connector_config()
    connector = get_connector(src_cfg)
    try:
        query = _build_view_query(view, final_params)
        rows = connector.query_view(query)
    finally:
        connector.close()

    if view.cache_ttl_sec > 0:
        _RESULT_CACHE[ck] = (time.time(), rows)
    return rows


def list_views(tenant_slug: str) -> list[dict[str, Any]]:
    """Lister les vues disponibles pour un tenant (introspection agent)."""
    schema = load_data_config(tenant_slug)
    return [
        {
            "name":        v.name,
            "description": v.description,
            "source":      v.source,
            "params": [
                {"name": p.name, "type": p.type,
                 "required": p.required, "default": p.default}
                for p in v.params
            ],
            "kind": (
                "sql"     if v.sql     else
                "graphql" if v.graphql else
                "rest"    if v.rest    else
                "sheet"   if v.sheet   else "unknown"
            ),
        }
        for v in schema.views.values()
    ]


def list_entities(tenant_slug: str) -> list[dict[str, Any]]:
    """Lister les entités déclarées (introspection agent)."""
    schema = load_data_config(tenant_slug)
    return [
        {
            "name":         e.name,
            "description":  e.description,
            "source":       e.source,
            "primary_key":  e.primary_key,
            "writable":     e.writable,
            "fields": [
                {"name": f.name, "type": f.type, "pii": f.pii,
                 "description": f.description}
                for f in e.fields
            ],
        }
        for e in schema.entities.values()
    ]


def invalidate_result_cache() -> None:
    _RESULT_CACHE.clear()
