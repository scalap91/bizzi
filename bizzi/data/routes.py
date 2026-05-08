"""bizzi.data.routes — Endpoints FastAPI /api/data/*.

Phase 0 : 5 endpoints minimaux. NON wirés dans api/main.py — Pascal valide
avant. Le wiring se fait par ajout d'une ligne dans api/main.py :

    from data import routes as data_routes
    app.include_router(data_routes.router, prefix="/api/data", tags=["Data"])

Tous les endpoints sont scope tenant : le caller passe `tenant_slug` en
query param. La sécurité (auth bearer) suit le pattern existant
get_tenant() — à brancher en Phase 1 quand le mapping token→slug sera
formalisé pour `lesdemocrates`.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import events as events_module, memory_vector, semantic, views as views_module
from .connectors.base import ConnectorError


router = APIRouter()


# ── Introspection ─────────────────────────────────────────────
@router.get("/schema")
def get_schema(tenant_slug: str = Query(..., description="Slug du tenant")):
    """Décrit le schéma sémantique complet pour ce tenant.

    Destiné à être injecté dans le system_prompt d'un agent IA.
    """
    try:
        return semantic.describe_schema(tenant_slug)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@router.get("/entities")
def get_entities(tenant_slug: str = Query(...)):
    try:
        return {"tenant": tenant_slug, "entities": views_module.list_entities(tenant_slug)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@router.get("/views")
def get_views(tenant_slug: str = Query(...)):
    try:
        return {"tenant": tenant_slug, "views": views_module.list_views(tenant_slug)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ── Exécution de vues ─────────────────────────────────────────
@router.get("/views/{view_name}")
def execute_view_endpoint(
    view_name:   str,
    tenant_slug: str = Query(...),
    limit:       Optional[int] = Query(None, ge=1, le=1000),
    # Tous les autres params arrivent en query string (FastAPI ne les capture
    # pas automatiquement → on lit via Query+kwargs Pydantic en Phase 1).
):
    """Exécute une semantic_view. Limite : pour Phase 0 on n'accepte que les
    params standards (`limit`). Pour passer plus de params, utiliser le POST.
    """
    params: dict[str, Any] = {}
    if limit is not None:
        params["limit"] = limit
    try:
        rows = views_module.execute_view(tenant_slug, view_name, params)
        return {"view": view_name, "tenant": tenant_slug,
                "rows": rows, "count": len(rows)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except ConnectorError as e:
        raise HTTPException(500, f"connector error: {e}")


class ExecuteViewBody(BaseModel):
    tenant_slug: str
    params:      dict[str, Any] = Field(default_factory=dict)


@router.post("/views/{view_name}")
def execute_view_post(view_name: str, body: ExecuteViewBody):
    """Variante POST pour passer des params arbitraires."""
    try:
        rows = views_module.execute_view(body.tenant_slug, view_name, body.params)
        return {"view": view_name, "tenant": body.tenant_slug,
                "rows": rows, "count": len(rows)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except ConnectorError as e:
        raise HTTPException(500, f"connector error: {e}")


# ── Memory RAG ────────────────────────────────────────────────
class MemoryStoreBody(BaseModel):
    tenant_id:  int
    text:       str
    agent_id:   Optional[int] = None
    kind:       str = "note"
    source_ref: Optional[str] = None
    metadata:   dict[str, Any] = Field(default_factory=dict)


class MemorySearchBody(BaseModel):
    tenant_id: int
    query:     str
    k:         int = 5
    kind:      Optional[str] = None
    agent_id:  Optional[int] = None


@router.post("/memory/store")
def memory_store_endpoint(body: MemoryStoreBody):
    try:
        mid = memory_vector.memory_store(
            tenant_id=body.tenant_id, text=body.text, agent_id=body.agent_id,
            kind=body.kind, source_ref=body.source_ref, metadata=body.metadata,
        )
        return {"memory_id": mid, "tenant_id": body.tenant_id}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/memory/search")
def memory_search_endpoint(body: MemorySearchBody):
    rows = memory_vector.memory_search(
        tenant_id=body.tenant_id, query=body.query, k=body.k,
        kind=body.kind, agent_id=body.agent_id,
    )
    return {"tenant_id": body.tenant_id, "k": body.k,
            "results": rows, "count": len(rows)}


@router.get("/memory/status")
def memory_status_endpoint(tenant_id: int = Query(..., ge=0)):
    return memory_vector.memory_status(tenant_id)


# ── Events bus ────────────────────────────────────────────────
class EventPublishBody(BaseModel):
    tenant_id:      int
    kind:           str = Field(..., min_length=1, max_length=200)
    payload:        dict[str, Any] = Field(default_factory=dict)
    source_module:  Optional[str] = None
    correlation_id: Optional[str] = None
    process_now:    bool = True


@router.post("/events/publish")
def events_publish(body: EventPublishBody):
    ev = events_module.publish(
        tenant_id=body.tenant_id, kind=body.kind, payload=body.payload,
        source_module=body.source_module, correlation_id=body.correlation_id,
        process_now=body.process_now,
    )
    return ev


@router.get("/events/recent")
def events_recent(
    tenant_id: int = Query(..., ge=0),
    kind: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return {
        "tenant_id": tenant_id,
        "events": events_module.list_events(
            tenant_id, kind=kind, status=status, limit=limit, offset=offset,
        ),
    }


@router.get("/events/kinds")
def events_kinds(tenant_id: int = Query(..., ge=0)):
    return {"tenant_id": tenant_id, "kinds": events_module.list_kinds(tenant_id)}


@router.post("/events/replay")
def events_replay(tenant_id: Optional[int] = None, limit: int = Query(100, ge=1, le=1000)):
    """Re-traite les events pending. Sans tenant_id = global (admin)."""
    return {"replayed": events_module.replay_pending(tenant_id=tenant_id, limit=limit)}


class EventConfigureBody(BaseModel):
    tenant_slug: str
    tenant_id:   int


@router.post("/events/configure")
def events_configure(body: EventConfigureBody):
    """Re-charge les handlers events_routes du YAML pour ce tenant."""
    n = events_module.configure_from_yaml(body.tenant_slug, body.tenant_id)
    return {"tenant_slug": body.tenant_slug, "tenant_id": body.tenant_id,
            "handlers_registered": n}
