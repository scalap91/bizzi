"""bizzi.org_hierarchy.models — Pydantic schemas pour la dimension hiérarchique organisationnelle.

Module GÉNÉRIQUE multi-tenant. Cas d'usage : parti politique, syndicat, franchise,
banque, ONG, hôpitaux, église — tous ont des niveaux (local → intermédiaire → global).

Les modèles Pydantic sont utilisés à la fois pour la validation API et la
sérialisation. Les tables réelles sont décrites dans migrations/001_org_hierarchy.sql.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ─── Niveaux configurables (depuis YAML tenant) ─────────────────────────────


class LevelDef(BaseModel):
    """Un niveau hiérarchique tel que défini dans le YAML du tenant.

    Exemple politique : section (0) → fédération (1) → région (2) → national (3).
    Exemple franchise : restaurant (0) → région (1) → pays (2) → siège (3).
    """

    id: str
    label: str
    order: int


# ─── Org units (les nœuds de l'arbre) ───────────────────────────────────────


class OrgUnitIn(BaseModel):
    tenant_id: int
    parent_external_id: Optional[str] = None
    level: str
    name: str
    external_id: str = Field(..., description="Stable ID dans le YAML tenant (ex: section_evry)")
    geo_meta: Optional[dict[str, Any]] = None
    contact_email: Optional[str] = None
    responsible: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class OrgUnit(BaseModel):
    id: int
    tenant_id: int
    parent_id: Optional[int] = None
    level: str
    level_order: int
    name: str
    external_id: Optional[str] = None
    geo_meta: Optional[dict[str, Any]] = None
    contact_email: Optional[str] = None
    responsible: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None


# ─── Geo mapping (résolution ville → org_unit) ──────────────────────────────


class GeoMappingIn(BaseModel):
    tenant_id: int
    city: str
    postal_code: Optional[str] = None
    org_unit_external_id: str


class GeoMapping(BaseModel):
    id: int
    tenant_id: int
    city: str
    postal_code: Optional[str] = None
    org_unit_id: int


# ─── Permissions / scoping JWT ──────────────────────────────────────────────


class JWTScope(BaseModel):
    """Scope extrait d'un JWT signé par le tenant et vérifié par Bizzi.

    Le tenant (ex: backend Fastify lesdemocrates) génère ce JWT pour chaque
    utilisateur connecté. Bizzi ne fait QUE vérifier la signature et appliquer
    le scope dans les queries.
    """

    tenant_id: int
    role: str
    org_unit_id: Optional[int] = None
    user_id: Optional[str] = None
    exp: Optional[int] = None


# ─── Aggregations (rollup local → global) ───────────────────────────────────


class OrgAggregation(BaseModel):
    id: int
    tenant_id: int
    org_unit_id: int
    category: str
    period: str
    total_mentions: Optional[int] = None
    trend_pct: Optional[float] = None
    top_keywords: Optional[list[str]] = None
    emotion_dom: Optional[str] = None
    computed_at: Optional[datetime] = None


# ─── Broadcasts (push global → local) ───────────────────────────────────────


class OrgBroadcastIn(BaseModel):
    tenant_id: int
    source_unit_external_id: str
    target_filter: dict[str, Any] = Field(
        default_factory=dict,
        description="ex: {level: 'section', region_id: 'idf'} ou {unit_external_ids: [...]}",
    )
    content_type: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)


class OrgBroadcast(BaseModel):
    id: int
    tenant_id: int
    source_unit_id: Optional[int] = None
    target_filter: Optional[dict[str, Any]] = None
    content_type: Optional[str] = None
    title: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
