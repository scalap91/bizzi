"""bizzi.org_hierarchy.routes — Endpoints FastAPI /api/org/*.

Phase 0 : endpoints lecture (units, children, path, geo/resolve).
Phase 1 : aggregations, broadcasts, embed iframe avec JWT scoping.

Wiring dans api/main.py : à ajouter manuellement (validation Pascal pour restart).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from datetime import datetime

from . import audit, broadcast as broadcast_mod
from . import permissions, rollup, storage, yaml_loader
from .permissions import JWTError

router = APIRouter()


def _scope_from_request(request: Request, expected_tenant_id: Optional[int] = None):
    """Extrait + valide le JWT (Authorization: Bearer ...). Retourne le scope ou None."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else None
    if not token:
        return None
    try:
        scope = permissions.verify_jwt(token)
    except JWTError as e:
        raise HTTPException(401, f"Invalid JWT: {e}")
    if expected_tenant_id is not None and scope.tenant_id != expected_tenant_id:
        raise HTTPException(403, "JWT tenant_id mismatch")
    return scope


@router.get("/units")
def list_units(
    tenant_id: int = Query(..., description="ID du tenant"),
    level: Optional[str] = Query(None, description="Filtre optionnel sur le level"),
):
    """Liste plate des org_units du tenant. Pour la vue arbre côté frontend,
    reconstruire via parent_id."""
    units = storage.list_units(tenant_id, level=level)
    return {"tenant_id": tenant_id, "count": len(units), "units": units}


@router.get("/units/tree")
def units_tree(tenant_id: int = Query(...)):
    """Hiérarchie complète sous forme d'arbre (racines avec children récursifs)."""
    units = storage.list_units(tenant_id)
    by_id: dict[int, dict] = {u["id"]: {**u, "children": []} for u in units}
    roots: list[dict] = []
    for u in units:
        node = by_id[u["id"]]
        if u.get("parent_id") and u["parent_id"] in by_id:
            by_id[u["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return {"tenant_id": tenant_id, "roots": roots}


@router.get("/units/{unit_id}")
def get_unit(unit_id: int):
    unit = storage.get_unit(unit_id)
    if not unit:
        raise HTTPException(404, f"org_unit {unit_id} introuvable")
    return unit


@router.get("/units/{unit_id}/children")
def get_children(unit_id: int):
    if not storage.get_unit(unit_id):
        raise HTTPException(404, f"org_unit {unit_id} introuvable")
    children = storage.list_children(unit_id)
    return {"unit_id": unit_id, "count": len(children), "children": children}


@router.get("/units/{unit_id}/descendants")
def get_descendants(unit_id: int):
    if not storage.get_unit(unit_id):
        raise HTTPException(404, f"org_unit {unit_id} introuvable")
    descendants = storage.get_descendants(unit_id)
    return {"unit_id": unit_id, "count": len(descendants), "descendants": descendants}


@router.get("/units/{unit_id}/aggregations")
def get_unit_aggregations(
    unit_id: int,
    period: Optional[str] = Query(None, description="24h | 7d | 30d"),
    category: Optional[str] = Query(None),
):
    if not storage.get_unit(unit_id):
        raise HTTPException(404, f"org_unit {unit_id} introuvable")
    rows = rollup.get_aggregations(unit_id, period=period, category=category)
    return {"unit_id": unit_id, "count": len(rows), "aggregations": rows}


@router.post("/rollup/run")
def run_rollup(
    tenant_id: int = Query(...),
    period: str = Query("30d", description="24h | 7d | 30d"),
):
    """Recompute org_aggregations pour le tenant (cron-able).

    Auto-discover les catégories depuis audience_reports récents.
    Cascade : feuilles (depuis audience_reports) → parents (somme enfants).
    """
    try:
        return rollup.run_rollup(tenant_id, period=period)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/units/{unit_id}/path")
def get_path(unit_id: int):
    if not storage.get_unit(unit_id):
        raise HTTPException(404, f"org_unit {unit_id} introuvable")
    path = storage.get_path(unit_id)
    return {"unit_id": unit_id, "path": path}


@router.get("/geo/resolve")
def geo_resolve(
    tenant_id: int = Query(...),
    city: str = Query(..., description="Nom de ville (ex: Évry-Courcouronnes)"),
    content: Optional[str] = Query(
        None,
        description="Contenu additionnel (titre + texte) pour le fallback region_detector",
    ),
    fallback: bool = Query(
        True,
        description="Si True, fallback sur tools.regions.region_detector si geo_mapping vide",
    ),
):
    """Résolution ville → org_unit.

    Stratégie (plan additif validé Pascal 2026-05-08) :
    1. geo_mapping (YAML tenant) = principal
    2. Si rien et fallback=True → tools.regions.region_detector (FR hardcoded)
    3. Match région détectée → org_unit dont geo_meta.region_id ou name matche

    Renvoie 404 uniquement si AUCUNE source ne donne de match.
    """
    if not fallback:
        match = storage.resolve_city(tenant_id, city)
        if not match:
            raise HTTPException(404, f"Aucun mapping pour {city!r} (tenant {tenant_id})")
        return {"match": match, "source": "geo_mapping", "detected_region": None}

    result = storage.resolve_city_with_fallback(tenant_id, city, content=content)
    if result["match"] is None:
        raise HTTPException(
            404,
            f"Aucun mapping pour {city!r} (tenant {tenant_id}, source tentée: {result['source']})",
        )
    return result


@router.post("/sync-from-yaml")
def sync_from_yaml(tenant_id: int = Query(...), slug: str = Query(...)):
    """Recharge la hiérarchie depuis le YAML tenant (idempotent)."""
    try:
        stats = yaml_loader.populate_from_yaml(tenant_id, slug)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", **stats}


# ─── Broadcasts ─────────────────────────────────────────────────────────────


class BroadcastBody(BaseModel):
    tenant_id: int
    source_unit_external_id: Optional[str] = None
    target_filter: dict[str, Any] = Field(
        default_factory=dict,
        description='ex: {"level":"section","region_id":"idf"} ou {"unit_external_ids":[...]} ou {"all":true}',
    )
    content_type: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/broadcast")
def create_broadcast(body: BroadcastBody, request: Request):
    """Création d'un broadcast. Permission : can_broadcast (instance_nationale,
    administrateur_autorise, ou admin)."""
    scope = _scope_from_request(request, expected_tenant_id=body.tenant_id)
    if scope is None:
        raise HTTPException(401, "JWT requis pour créer un broadcast")
    if not permissions.can_broadcast(scope):
        raise HTTPException(403, f"Le rôle {scope.role!r} n'a pas can_broadcast")

    source_unit_id = None
    if body.source_unit_external_id:
        u = storage.get_unit_by_external_id(body.tenant_id, body.source_unit_external_id)
        if not u:
            raise HTTPException(
                400, f"source_unit_external_id introuvable: {body.source_unit_external_id}"
            )
        source_unit_id = u["id"]

    targets = broadcast_mod.resolve_targets(body.tenant_id, body.target_filter)
    bid = broadcast_mod.create_broadcast(
        tenant_id=body.tenant_id,
        source_unit_id=source_unit_id,
        target_filter=body.target_filter,
        content_type=body.content_type,
        title=body.title,
        payload=body.payload,
    )
    return {
        "broadcast_id": bid,
        "source_unit_id": source_unit_id,
        "target_count": len(targets),
        "target_unit_ids": targets,
        "status": "pending",
    }


@router.get("/broadcasts/received")
def broadcasts_received(
    unit_id: int = Query(..., description="org_unit_id qui consulte ses broadcasts"),
    status: Optional[str] = Query(None, description="Filtre status (pending, published…)"),
):
    if not storage.get_unit(unit_id):
        raise HTTPException(404, f"org_unit {unit_id} introuvable")
    rows = broadcast_mod.list_received_for_unit(unit_id, status=status)
    return {"unit_id": unit_id, "count": len(rows), "broadcasts": rows}


@router.get("/broadcasts/{broadcast_id}")
def get_broadcast(broadcast_id: int):
    b = broadcast_mod.get_broadcast(broadcast_id)
    if not b:
        raise HTTPException(404, f"broadcast {broadcast_id} introuvable")
    targets = broadcast_mod.resolve_targets(b["tenant_id"], b.get("target_filter") or {})
    return {**b, "target_count": len(targets), "target_unit_ids": targets}


# ─── Audit (export + purge) ─────────────────────────────────────────────────


# Roles autorisés à exporter l'audit log (Phase 1).
_AUDIT_EXPORT_ROLES = {
    "responsable_territorial",
    "instance_nationale",
    "administrateur_autorise",
    "admin",
}


@router.get("/audit/export")
def audit_export(
    request: Request,
    tenant_id: int = Query(...),
    user_id: Optional[str] = Query(None),
    org_unit_id: Optional[int] = Query(None),
    since: Optional[datetime] = Query(None, description="ISO8601"),
    until: Optional[datetime] = Query(None, description="ISO8601"),
    limit: int = Query(1000, ge=1, le=10000),
):
    """Export d'audit pour conformité. Permission : responsable_territorial+."""
    scope = _scope_from_request(request, expected_tenant_id=tenant_id)
    if scope is None:
        raise HTTPException(401, "JWT requis")
    if scope.role not in _AUDIT_EXPORT_ROLES:
        raise HTTPException(403, f"role {scope.role!r} non autorisé pour audit export")

    rows = audit.export_logs(
        tenant_id=tenant_id,
        user_id=user_id,
        org_unit_id=org_unit_id,
        since=since,
        until=until,
        limit=limit,
    )
    return {"tenant_id": tenant_id, "count": len(rows), "logs": rows}


@router.post("/audit/purge")
def audit_purge(
    request: Request,
    retention_days: int = Query(90, ge=7, description="Retention min 7 jours (sécurité)"),
):
    """Purge des audit logs > retention_days. Admin uniquement."""
    scope = _scope_from_request(request)
    if scope is None or scope.role not in {"administrateur_autorise", "admin"}:
        raise HTTPException(403, "admin requis pour purge")
    deleted = audit.purge_old_logs(retention_days=retention_days)
    return {"deleted": deleted, "retention_days": retention_days}


@router.post("/broadcasts/{broadcast_id}/publish")
def publish_broadcast(broadcast_id: int, request: Request):
    b = broadcast_mod.get_broadcast(broadcast_id)
    if not b:
        raise HTTPException(404, f"broadcast {broadcast_id} introuvable")
    scope = _scope_from_request(request, expected_tenant_id=b["tenant_id"])
    if scope is None or not permissions.can_broadcast(scope):
        raise HTTPException(403, "permission can_broadcast requise")
    ok = broadcast_mod.update_status(broadcast_id, "published")
    return {"broadcast_id": broadcast_id, "status": "published" if ok else "unchanged"}
