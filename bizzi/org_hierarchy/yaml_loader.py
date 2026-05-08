"""bizzi.org_hierarchy.yaml_loader — Lit la section org_hierarchy: du YAML tenant
et populate org_units + geo_mapping.

Idempotent : peut être ré-exécuté sans dupliquer (upsert par external_id).

Usage :
    from bizzi.org_hierarchy import yaml_loader
    yaml_loader.populate_from_yaml(tenant_id=4, slug="lesdemocrates")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

from . import storage


DOMAINS_DIR = Path(os.getenv("BIZZI_DOMAINS_DIR", "/opt/bizzi/bizzi/domains"))


def load_yaml(slug: str) -> dict[str, Any]:
    path = DOMAINS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"YAML tenant introuvable : {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def get_org_hierarchy_section(slug: str) -> Optional[dict[str, Any]]:
    cfg = load_yaml(slug)
    section = cfg.get("org_hierarchy")
    if not section or not section.get("enabled", False):
        return None
    return section


def populate_from_yaml(tenant_id: int, slug: str) -> dict[str, int]:
    """Charge la section org_hierarchy: du YAML et populate les tables.

    Retourne un dict de stats : {units_upserted, geo_upserted, levels_count}.
    """
    section = get_org_hierarchy_section(slug)
    if section is None:
        return {"units_upserted": 0, "geo_upserted": 0, "levels_count": 0}

    levels = section.get("levels") or []
    level_order_by_id: dict[str, int] = {lv["id"]: int(lv["order"]) for lv in levels}

    # 1) Insert units, deux passes pour résoudre les parents (qui peuvent être
    #    déclarés dans n'importe quel ordre dans le YAML).
    units_in = section.get("units") or []
    external_to_db_id: dict[str, int] = {}

    # Passe 1 : créer toutes les units sans parent
    for u in units_in:
        ext_id = u["id"]
        level = u["level"]
        order = level_order_by_id.get(level)
        if order is None:
            raise ValueError(f"Unit {ext_id} référence un level inconnu: {level}")
        db_id = storage.upsert_unit(
            tenant_id=tenant_id,
            external_id=ext_id,
            level=level,
            level_order=order,
            name=u.get("name", ext_id),
            parent_id=None,
            geo_meta=u.get("geo_meta"),
            contact_email=u.get("contact_email"),
            responsible=u.get("responsible"),
            metadata=u.get("metadata"),
        )
        external_to_db_id[ext_id] = db_id

    # Passe 2 : résoudre et mettre à jour les parents
    for u in units_in:
        parent_ext = u.get("parent")
        if not parent_ext:
            continue
        parent_db_id = external_to_db_id.get(parent_ext)
        if parent_db_id is None:
            raise ValueError(
                f"Unit {u['id']} référence un parent inconnu: {parent_ext}"
            )
        order = level_order_by_id[u["level"]]
        storage.upsert_unit(
            tenant_id=tenant_id,
            external_id=u["id"],
            level=u["level"],
            level_order=order,
            name=u.get("name", u["id"]),
            parent_id=parent_db_id,
            geo_meta=u.get("geo_meta"),
            contact_email=u.get("contact_email"),
            responsible=u.get("responsible"),
            metadata=u.get("metadata"),
        )

    # 2) Geo mapping
    geo = section.get("geo_mapping") or {}
    geo_count = 0
    for city, ext_id in geo.items():
        db_id = external_to_db_id.get(ext_id)
        if db_id is None:
            existing = storage.get_unit_by_external_id(tenant_id, ext_id)
            if existing is None:
                raise ValueError(
                    f"geo_mapping {city!r} référence un org_unit inconnu: {ext_id}"
                )
            db_id = existing["id"]
        storage.upsert_geo_mapping(tenant_id=tenant_id, city=city, org_unit_id=db_id)
        geo_count += 1

    return {
        "units_upserted": len(units_in),
        "geo_upserted": geo_count,
        "levels_count": len(levels),
    }
