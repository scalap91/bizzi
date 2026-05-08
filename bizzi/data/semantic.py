"""bizzi.data.semantic — Schéma sémantique tenant.

Charge les sections data_sources / semantic_schema / semantic_views
du YAML d'un tenant, et expose des dataclasses utilisables par les agents
et le moteur de vues.

Format YAML attendu (extension du yaml tenant existant) :

    data_sources:
      - id: main_db
        type: postgresql
        scope: read_only
        host: localhost
        database: bizzi
        user: bizzi_admin
        password: env:BIZZI_DB_PWD
        # ou dsn: env:DATABASE_URL

      - id: erp
        type: rest_api
        base_url: https://erp.cabinet.fr/api
        auth: { type: bearer, token: env:ERP_TOKEN }

    semantic_schema:
      article:
        source:        main_db
        physical_name: bizzi_articles
        primary_key:   id
        description:   "Article éditorial publié sur le site"
        fields:
          - { name: id,        type: int,      pk: true }
          - { name: title,     type: text,     description: "Titre" }
          - { name: tenant,    type: text,     filterable: true }
          - { name: status,    type: enum,     values: [draft, published] }
          - { name: created_at, type: datetime }
        relations:
          - { name: scores, target: article_score, kind: one_to_many, on: article_id }

    semantic_views:
      articles_recent:
        description: "Articles publiés des 7 derniers jours pour ce tenant"
        params:
          - { name: tenant_slug, type: text, required: true }
          - { name: limit,       type: int,  default: 50 }
        sql: |
          SELECT id, title, slug, created_at, category
          FROM bizzi_articles
          WHERE tenant = %(tenant_slug)s
            AND created_at > now() - interval '7 days'
          ORDER BY created_at DESC
          LIMIT %(limit)s

      dossiers_a_relancer:
        description: "Dossiers en attente client > 14 jours"
        source: erp
        rest:
          method: GET
          path:   /dossiers
          query_params:
            status: pending
            since:  "{since_iso}"
        params:
          - { name: since_iso, type: text, required: true }
        pii_mask: [client_phone, client_email]
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# ── Path helpers ───────────────────────────────────────────────
# Env var BIZZI_DOMAINS_DIR override le chemin (utile pour tests).
_DOMAINS_DIR = Path(
    os.environ.get("BIZZI_DOMAINS_DIR")
    or Path(__file__).resolve().parents[1] / "domains"
)


@dataclass
class DataSourceConfig:
    id:     str
    type:   str
    scope:  str = "read_only"
    raw:    dict[str, Any] = field(default_factory=dict)

    def to_connector_config(self) -> dict[str, Any]:
        """Renvoie la config consommable par get_connector()."""
        cfg = dict(self.raw)
        cfg.setdefault("id", self.id)
        cfg.setdefault("type", self.type)
        cfg.setdefault("scope", self.scope)
        return cfg


@dataclass
class SemanticField:
    name:        str
    type:        str = "text"          # text|int|float|bool|datetime|enum|json
    description: Optional[str] = None
    pk:          bool = False
    filterable:  bool = False
    pii:         bool = False
    values:      list[str] = field(default_factory=list)   # pour enum


@dataclass
class SemanticRelation:
    name:   str
    target: str
    kind:   str = "one_to_many"        # one_to_one|one_to_many|many_to_many
    on:     Optional[str] = None       # FK column


@dataclass
class SemanticEntity:
    name:           str
    source:         str                # id de la data_source
    physical_name:  Optional[str] = None
    primary_key:    str = "id"
    description:    Optional[str] = None
    writable:       bool = False
    fields:         list[SemanticField] = field(default_factory=list)
    relations:      list[SemanticRelation] = field(default_factory=list)

    def field(self, name: str) -> Optional[SemanticField]:
        return next((f for f in self.fields if f.name == name), None)

    def pii_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.pii]


@dataclass
class SemanticViewParam:
    name:     str
    type:     str = "text"
    required: bool = False
    default:  Any = None


@dataclass
class SemanticView:
    name:         str
    description:  Optional[str] = None
    source:       Optional[str] = None        # défaut : source de l'entity ciblée
    params:       list[SemanticViewParam] = field(default_factory=list)
    sql:          Optional[str] = None
    graphql:      Optional[str] = None
    rest:         Optional[dict[str, Any]] = None
    sheet:        Optional[dict[str, Any]] = None
    pii_mask:     list[str] = field(default_factory=list)
    cache_ttl_sec: int = 0   # 0 = pas de cache

    def validate_params(self, given: dict[str, Any]) -> dict[str, Any]:
        """Vérifie les params requis, applique les defaults, retourne le dict final."""
        out = dict(given or {})
        for p in self.params:
            if p.name not in out:
                if p.required:
                    raise ValueError(f"View {self.name!r} : param requis {p.name!r}")
                if p.default is not None:
                    out[p.name] = p.default
        return out


@dataclass
class SemanticSchema:
    tenant_slug:  str
    sources:      dict[str, DataSourceConfig] = field(default_factory=dict)
    entities:     dict[str, SemanticEntity]   = field(default_factory=dict)
    views:        dict[str, SemanticView]     = field(default_factory=dict)

    def entity(self, name: str) -> Optional[SemanticEntity]:
        return self.entities.get(name)

    def view(self, name: str) -> Optional[SemanticView]:
        return self.views.get(name)


# ── YAML loader ────────────────────────────────────────────────
def _resolve_secret_in_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Résolution récursive 'env:VAR_NAME' -> os.environ['VAR_NAME']."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str) and v.startswith("env:"):
            out[k] = os.environ.get(v[4:], v)  # garde 'env:VAR' si non défini
        elif isinstance(v, dict):
            out[k] = _resolve_secret_in_dict(v)
        else:
            out[k] = v
    return out


def _parse_field(d: dict[str, Any]) -> SemanticField:
    return SemanticField(
        name        = d["name"],
        type        = d.get("type", "text"),
        description = d.get("description"),
        pk          = bool(d.get("pk", False)),
        filterable  = bool(d.get("filterable", False)),
        pii         = bool(d.get("pii", False)),
        values      = list(d.get("values") or []),
    )


def _parse_relation(d: dict[str, Any]) -> SemanticRelation:
    return SemanticRelation(
        name   = d["name"],
        target = d["target"],
        kind   = d.get("kind", "one_to_many"),
        on     = d.get("on"),
    )


def _parse_entity(name: str, d: dict[str, Any]) -> SemanticEntity:
    return SemanticEntity(
        name          = name,
        source        = d["source"],
        physical_name = d.get("physical_name"),
        primary_key   = d.get("primary_key", "id"),
        description   = d.get("description"),
        writable      = bool(d.get("writable", False)),
        fields        = [_parse_field(f) for f in (d.get("fields") or [])],
        relations     = [_parse_relation(r) for r in (d.get("relations") or [])],
    )


def _parse_param(d: dict[str, Any]) -> SemanticViewParam:
    return SemanticViewParam(
        name     = d["name"],
        type     = d.get("type", "text"),
        required = bool(d.get("required", False)),
        default  = d.get("default"),
    )


def _parse_view(name: str, d: dict[str, Any]) -> SemanticView:
    return SemanticView(
        name         = name,
        description  = d.get("description"),
        source       = d.get("source"),
        params       = [_parse_param(p) for p in (d.get("params") or [])],
        sql          = d.get("sql"),
        graphql      = d.get("graphql"),
        rest         = d.get("rest"),
        sheet        = d.get("sheet"),
        pii_mask     = list(d.get("pii_mask") or []),
        cache_ttl_sec= int(d.get("cache_ttl_sec", 0)),
    )


def _yaml_path_for_tenant(tenant_slug: str) -> Path:
    # Direct .yaml
    p = _DOMAINS_DIR / f"{tenant_slug}.yaml"
    if p.exists():
        return p
    raise FileNotFoundError(
        f"Tenant {tenant_slug!r} : aucun YAML trouvé sous {_DOMAINS_DIR}/"
    )


# ── Cache mémoire (invalidé manuellement) ──────────────────────
_SCHEMA_CACHE: dict[str, SemanticSchema] = {}


def load_data_config(tenant_slug: str, force_reload: bool = False) -> SemanticSchema:
    """Charge le schéma sémantique d'un tenant depuis son YAML.

    Idempotent + caché en mémoire. Pour invalider : force_reload=True.
    """
    if not force_reload and tenant_slug in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[tenant_slug]

    path = _yaml_path_for_tenant(tenant_slug)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # ── data_sources ─────────────────────────────────────────
    sources: dict[str, DataSourceConfig] = {}
    for s in (data.get("data_sources") or []):
        if not s.get("id") or not s.get("type"):
            raise ValueError(
                f"data_source invalide dans {path.name} : id+type requis"
            )
        resolved_raw = _resolve_secret_in_dict(s)
        sources[s["id"]] = DataSourceConfig(
            id    = s["id"],
            type  = s["type"],
            scope = s.get("scope", "read_only"),
            raw   = resolved_raw,
        )

    # ── semantic_schema ──────────────────────────────────────
    entities: dict[str, SemanticEntity] = {}
    schema_block = data.get("semantic_schema") or {}
    for ename, edef in schema_block.items():
        if not isinstance(edef, dict):
            continue
        if "source" not in edef:
            raise ValueError(
                f"Entity {ename!r} dans {path.name} : champ 'source' requis"
            )
        if edef["source"] not in sources:
            raise ValueError(
                f"Entity {ename!r} référence source {edef['source']!r} "
                f"non déclarée dans data_sources"
            )
        entities[ename] = _parse_entity(ename, edef)

    # ── semantic_views ───────────────────────────────────────
    views: dict[str, SemanticView] = {}
    views_block = data.get("semantic_views") or {}
    for vname, vdef in views_block.items():
        if not isinstance(vdef, dict):
            continue
        v = _parse_view(vname, vdef)
        if v.source and v.source not in sources:
            raise ValueError(
                f"View {vname!r} référence source {v.source!r} non déclarée"
            )
        views[vname] = v

    schema = SemanticSchema(
        tenant_slug=tenant_slug,
        sources=sources,
        entities=entities,
        views=views,
    )
    _SCHEMA_CACHE[tenant_slug] = schema
    return schema


def invalidate_cache(tenant_slug: Optional[str] = None) -> None:
    if tenant_slug is None:
        _SCHEMA_CACHE.clear()
    else:
        _SCHEMA_CACHE.pop(tenant_slug, None)


# ── Vue d'ensemble pour l'agent (introspection) ───────────────
def describe_schema(tenant_slug: str) -> dict[str, Any]:
    """Retourne une description JSON-friendly du schéma — destinée à être
    injectée dans le system_prompt d'un agent IA pour qu'il sache quelles
    données il peut interroger.
    """
    s = load_data_config(tenant_slug)
    return {
        "tenant": tenant_slug,
        "sources": [
            {"id": s.id, "type": s.type, "scope": s.scope}
            for s in s.sources.values()
        ],
        "entities": [
            {
                "name":          e.name,
                "description":   e.description,
                "source":        e.source,
                "writable":      e.writable,
                "primary_key":   e.primary_key,
                "fields": [
                    {"name": f.name, "type": f.type,
                     "description": f.description, "pii": f.pii}
                    for f in e.fields
                ],
                "relations": [
                    {"name": r.name, "target": r.target, "kind": r.kind}
                    for r in e.relations
                ],
            }
            for e in s.entities.values()
        ],
        "views": [
            {
                "name":        v.name,
                "description": v.description,
                "params": [
                    {"name": p.name, "type": p.type,
                     "required": p.required, "default": p.default}
                    for p in v.params
                ],
            }
            for v in s.views.values()
        ],
    }
