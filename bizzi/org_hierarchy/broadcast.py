"""bizzi.org_hierarchy.broadcast — Push national/fédéral → sections.

Phase 1. Cas d'usage :
- Bureau National diffuse une consigne ou une note d'orientation à toutes
  les sections d'une région donnée.
- Une fédération diffuse à ses sections.

target_filter (JSONB) supporte :
  {"all": true}                                  → toutes units du tenant
  {"level": "section"}                           → toutes sections
  {"level": "section", "region_id": "idf"}       → sections IDF
  {"unit_external_ids": ["section_evry", ...]}   → liste explicite
  {"descendant_of": <unit_id>}                   → tous descendants

Permission : seul un scope avec can_broadcast=True (instance_nationale,
administrateur_autorise, ou admin générique) peut créer un broadcast.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ._db import get_conn
from . import storage


# ─── Résolution target_filter → set(org_unit_id) ────────────────────────────


def resolve_targets(tenant_id: int, target_filter: dict[str, Any]) -> list[int]:
    """Calcule la liste des org_unit_id qui matchent ce target_filter."""
    if not target_filter or target_filter.get("all"):
        return [u["id"] for u in storage.list_units(tenant_id)]

    if "unit_external_ids" in target_filter:
        ext_ids = target_filter["unit_external_ids"] or []
        out = []
        for ext in ext_ids:
            row = storage.get_unit_by_external_id(tenant_id, ext)
            if row:
                out.append(row["id"])
        return out

    if "descendant_of" in target_filter:
        anchor_id = int(target_filter["descendant_of"])
        descendants = storage.get_descendants(anchor_id)
        return [d["id"] for d in descendants]

    # Filtre niveau + région optionnel.
    # region_id : matche soit geo_meta.region_id direct, soit hérité d'un ancêtre.
    # Phase 1 : on remonte la chaîne parents pour trouver geo_meta.region_id.
    level = target_filter.get("level")
    region_id = target_filter.get("region_id")
    if level or region_id:
        units = storage.list_units(tenant_id, level=level)
        if region_id:
            all_by_id = {u["id"]: u for u in storage.list_units(tenant_id)}
            def has_region(u):
                cur = u
                while cur is not None:
                    meta = cur.get("geo_meta") or {}
                    if meta.get("region_id") == region_id:
                        return True
                    parent_id = cur.get("parent_id")
                    cur = all_by_id.get(parent_id) if parent_id else None
                return False
            units = [u for u in units if has_region(u)]
        return [u["id"] for u in units]

    return []


# ─── CRUD ───────────────────────────────────────────────────────────────────


def create_broadcast(
    tenant_id: int,
    source_unit_id: Optional[int],
    target_filter: dict[str, Any],
    content_type: str,
    title: str,
    payload: dict[str, Any],
) -> int:
    sql = """
        INSERT INTO org_broadcasts (
            tenant_id, source_unit_id, target_filter, content_type,
            title, payload, status
        ) VALUES (%s, %s, %s::jsonb, %s, %s, %s::jsonb, 'pending')
        RETURNING id
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                tenant_id,
                source_unit_id,
                json.dumps(target_filter or {}),
                content_type,
                title,
                json.dumps(payload or {}),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


def get_broadcast(broadcast_id: int) -> Optional[dict[str, Any]]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM org_broadcasts WHERE id = %s", (broadcast_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_received_for_unit(unit_id: int, status: Optional[str] = None) -> list[dict]:
    """Liste les broadcasts dont le target_filter inclut ce unit.

    Phase 1 : on calcule à la volée (sans cache). Phase 1.5 : table de jointure
    org_broadcast_targets pour pré-calculer.
    """
    unit = storage.get_unit(unit_id)
    if not unit:
        return []
    tenant_id = unit["tenant_id"]

    sql = "SELECT * FROM org_broadcasts WHERE tenant_id = %s"
    params: list = [tenant_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC"

    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        broadcasts = [dict(r) for r in cur.fetchall()]

    out = []
    for b in broadcasts:
        target_filter = b.get("target_filter") or {}
        targets = resolve_targets(tenant_id, target_filter)
        if unit_id in targets:
            out.append(b)
    return out


def update_status(broadcast_id: int, status: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE org_broadcasts SET status = %s WHERE id = %s",
            (status, broadcast_id),
        )
        conn.commit()
        return cur.rowcount > 0
