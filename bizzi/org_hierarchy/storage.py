"""bizzi.org_hierarchy.storage — CRUD bas niveau sur org_units et geo_mapping.

Pattern : psycopg2 direct, pas d'ORM (cohérent avec phone/social).
Toutes les fonctions ouvrent/ferment leur propre connexion.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ._db import get_conn


# ─── Org units ──────────────────────────────────────────────────────────────


def upsert_unit(
    tenant_id: int,
    external_id: str,
    level: str,
    level_order: int,
    name: str,
    parent_id: Optional[int] = None,
    geo_meta: Optional[dict[str, Any]] = None,
    contact_email: Optional[str] = None,
    responsible: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Upsert idempotent par (tenant_id, external_id). Retourne l'id."""
    # Note : l'unique index est partiel (WHERE external_id IS NOT NULL) ; il
    # faut donc répéter la clause WHERE dans ON CONFLICT pour que Postgres
    # matche l'inferred index. Sinon : "no unique constraint matching".
    sql = """
        INSERT INTO org_units (
            tenant_id, parent_id, level, level_order, name, external_id,
            geo_meta, contact_email, responsible, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
        ON CONFLICT (tenant_id, external_id) WHERE external_id IS NOT NULL
        DO UPDATE SET
            parent_id = EXCLUDED.parent_id,
            level = EXCLUDED.level,
            level_order = EXCLUDED.level_order,
            name = EXCLUDED.name,
            geo_meta = EXCLUDED.geo_meta,
            contact_email = EXCLUDED.contact_email,
            responsible = EXCLUDED.responsible,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                tenant_id,
                parent_id,
                level,
                level_order,
                name,
                external_id,
                json.dumps(geo_meta) if geo_meta else None,
                contact_email,
                responsible,
                json.dumps(metadata) if metadata else None,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


def get_unit(unit_id: int) -> Optional[dict[str, Any]]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM org_units WHERE id = %s", (unit_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_unit_by_external_id(tenant_id: int, external_id: str) -> Optional[dict[str, Any]]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM org_units WHERE tenant_id = %s AND external_id = %s",
            (tenant_id, external_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_units(tenant_id: int, level: Optional[str] = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM org_units WHERE tenant_id = %s"
    params: list[Any] = [tenant_id]
    if level:
        sql += " AND level = %s"
        params.append(level)
    sql += " ORDER BY level_order, name"
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def list_children(unit_id: int) -> list[dict[str, Any]]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM org_units WHERE parent_id = %s ORDER BY name",
            (unit_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_descendants(unit_id: int) -> list[dict[str, Any]]:
    """Retourne tous les descendants (récursif) via WITH RECURSIVE."""
    sql = """
        WITH RECURSIVE descendants AS (
            SELECT * FROM org_units WHERE parent_id = %s
            UNION ALL
            SELECT o.* FROM org_units o
            JOIN descendants d ON o.parent_id = d.id
        )
        SELECT * FROM descendants ORDER BY level_order, name
    """
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (unit_id,))
        return [dict(r) for r in cur.fetchall()]


def get_path(unit_id: int) -> list[dict[str, Any]]:
    """Chemin de la racine vers cet unit (inclus)."""
    sql = """
        WITH RECURSIVE ancestors AS (
            SELECT * FROM org_units WHERE id = %s
            UNION ALL
            SELECT o.* FROM org_units o
            JOIN ancestors a ON a.parent_id = o.id
        )
        SELECT * FROM ancestors ORDER BY level_order ASC
    """
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (unit_id,))
        return [dict(r) for r in cur.fetchall()]


# ─── Geo mapping ────────────────────────────────────────────────────────────


def upsert_geo_mapping(
    tenant_id: int,
    city: str,
    org_unit_id: int,
    postal_code: Optional[str] = None,
) -> int:
    sql = """
        INSERT INTO geo_mapping (tenant_id, city, postal_code, org_unit_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id, city) DO UPDATE SET
            postal_code = EXCLUDED.postal_code,
            org_unit_id = EXCLUDED.org_unit_id
        RETURNING id
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (tenant_id, city, postal_code, org_unit_id))
        row = cur.fetchone()
        conn.commit()
        return row[0]


def resolve_city(tenant_id: int, city: str) -> Optional[dict[str, Any]]:
    """Résolution ville → org_unit pour un tenant. Match exact + casefold fallback."""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.*, o.name AS unit_name, o.level AS unit_level, o.external_id AS unit_external_id
            FROM geo_mapping g
            JOIN org_units o ON o.id = g.org_unit_id
            WHERE g.tenant_id = %s AND LOWER(g.city) = LOWER(%s)
            """,
            (tenant_id, city),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def resolve_city_with_fallback(
    tenant_id: int,
    city: str,
    content: Optional[str] = None,
) -> dict[str, Any]:
    """Résolution ville → org_unit avec fallback region_detector.

    Stratégie (validée Pascal — plan additif 2026-05-08) :
    1. geo_mapping (configuré par tenant via YAML) = source PRINCIPALE
    2. Si rien → fallback sur tools.regions.region_detector (FR hardcoded)
    3. Si region détectée → on cherche un org_unit du tenant dont
       geo_meta.region_id (ou name) matche cette région
    4. Sinon → renvoie un dict avec match=None mais avec les indices détectés

    Cohérent avec audit doublons : region_detector RESTE comme fallback
    (FR hardcoded), geo_mapping prend le relais principal.

    Retour :
        {
          "match": <dict org_unit_resolved | None>,
          "source": "geo_mapping" | "region_detector" | "none",
          "detected_region": <str | None>,
        }
    """
    primary = resolve_city(tenant_id, city)
    if primary:
        return {"match": primary, "source": "geo_mapping", "detected_region": None}

    # Fallback FR hardcoded — import lazy. Deux chemins possibles selon le PYTHONPATH :
    # - api/main.py démarre depuis /opt/bizzi/bizzi → 'tools.regions...'
    # - tests démarrent depuis /opt/bizzi → 'bizzi.tools.regions...'
    detected_region: Optional[str] = None
    detect_fn = None
    try:
        from tools.regions.region_detector import detect_region_by_content as detect_fn  # type: ignore
    except ImportError:
        try:
            from bizzi.tools.regions.region_detector import detect_region_by_content as detect_fn  # type: ignore
        except ImportError:
            detect_fn = None
    if detect_fn is not None:
        try:
            detected_region = detect_fn(city, content or "")
        except Exception:
            detected_region = None

    if not detected_region:
        return {"match": None, "source": "none", "detected_region": None}

    # Cherche un org_unit du tenant dont la région correspond.
    candidate = _find_unit_by_region(tenant_id, detected_region)
    if candidate:
        return {
            "match": candidate,
            "source": "region_detector",
            "detected_region": detected_region,
        }
    return {"match": None, "source": "region_detector", "detected_region": detected_region}


# Mapping canonique des libellés region_detector → ids YAML compacts (lesdemocrates).
# Cohérent avec la liste regions: du domains/lesdemocrates.yaml. Phase 1 : générique
# (couvre toutes régions FR métropolitaines + DOM-TOM + alias usuels).
REGION_ALIASES: dict[str, list[str]] = {
    # canonical region_detector label → list of acceptable matchers (id court, label long, alias)
    "Ile-de-France": ["idf", "ile-de-france", "île-de-france", "region ile-de-france", "région île-de-france"],
    "PACA": ["paca", "provence-alpes-cote-d-azur", "provence-alpes-côte-d'azur"],
    "Auvergne-Rhône-Alpes": ["ara", "auvergne-rhone-alpes", "auvergne-rhône-alpes"],
    "Occitanie": ["occ", "occitanie"],
    "Nouvelle-Aquitaine": ["naq", "nouvelle-aquitaine"],
    "Hauts-de-France": ["hdf", "hauts-de-france"],
    "Grand-Est": ["ge", "grand-est", "grand est"],
    "Bretagne": ["bre", "bretagne"],
    "Pays-de-la-Loire": ["pdl", "pays-de-la-loire", "pays de la loire"],
    "Normandie": ["nor", "normandie"],
    "Centre-Val de Loire": ["cvl", "centre-val-de-loire", "centre-val de loire"],
    "Bourgogne-Franche-Comté": ["bfc", "bourgogne-franche-comte", "bourgogne-franche-comté"],
    "Corse": ["cor", "corse"],
    "DOM-TOM": ["drom", "dom-tom", "outre-mer"],
}


def _strip_diacritics(s: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    ).lower()


def _aliases_for(region_label: str) -> list[str]:
    """Retourne tous les matchers acceptables pour ce label (insensible accents/casse)."""
    out = {region_label, _strip_diacritics(region_label)}
    out.update(REGION_ALIASES.get(region_label, []))
    out.update(_strip_diacritics(a) for a in REGION_ALIASES.get(region_label, []))
    return list(out)


def _find_unit_by_region(tenant_id: int, region_label: str) -> Optional[dict[str, Any]]:
    """Trouve un org_unit dont geo_meta.region_id, geo_meta.region_label ou name
    matche le label détecté (avec aliases canoniques + diacritics-insensitive).
    """
    aliases = _aliases_for(region_label)
    if not aliases:
        return None

    # Construit dynamiquement une clause IN avec autant de placeholders que d'aliases.
    placeholders = ",".join(["%s"] * len(aliases))
    # Fonction Postgres unaccent() peut ne pas être installée → on compare en lower
    # côté SQL et on a déjà fait strip_diacritics côté Python pour les aliases.
    sql = f"""
        SELECT * FROM org_units
        WHERE tenant_id = %s
          AND (
            LOWER(name) IN ({placeholders})
            OR LOWER(COALESCE(geo_meta->>'region_id', '')) IN ({placeholders})
            OR LOWER(COALESCE(geo_meta->>'region_label', '')) IN ({placeholders})
          )
        ORDER BY level_order DESC
        LIMIT 1
    """
    params = [tenant_id] + [a.lower() for a in aliases] * 3
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
