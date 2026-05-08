"""bizzi.org_hierarchy.rollup — Agrégation org_aggregations.

Phase 1. Cron-able. Cascade :
1. LEAVES (level_order minimum, ex: section) : compute depuis audience_reports
2. PARENTS : remontée par somme (mentions) + recalcul (top_keywords, emotion_dom)

Périodes supportées : '24h', '7d', '30d'.

Pour chaque (org_unit, category, period) on calcule :
  - total_mentions : COUNT(audience_reports)
  - top_keywords  : top-N keywords agrégés
  - emotion_dom   : émotion dominante (mode)
  - trend_pct     : variation vs période précédente (Phase 1.5 — None Phase 1)

Stockage : table org_aggregations, UNIQUE (tenant_id, org_unit_id, category, period).
ON CONFLICT → DO UPDATE (recompute).

Coordination avec bizzi-audience : on lit audience_reports.org_unit_id.
Si une row a org_unit_id=NULL (ville inconnue / pas encore résolue), elle
est exclue du rollup. bizzi-audience devra utiliser
storage.resolve_city_with_fallback() à l'ingestion pour remplir org_unit_id.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from ._db import get_conn
from . import storage


PERIOD_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


# ─── Compute leaves depuis audience_reports ────────────────────────────────


def _compute_leaf(
    tenant_id: int, org_unit_id: int, category: str, period: str
) -> dict:
    """Récupère et agrège les audience_reports pour cette feuille."""
    delta = PERIOD_DELTAS[period]
    since = datetime.utcnow() - delta

    sql = """
        SELECT keywords, emotion
        FROM audience_reports
        WHERE tenant_id = %s
          AND org_unit_id = %s
          AND %s = ANY(categories)
          AND created_at >= %s
    """
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (tenant_id, org_unit_id, category, since))
        rows = cur.fetchall()

    total = len(rows)
    kw_counter: Counter = Counter()
    emo_counter: Counter = Counter()
    for r in rows:
        for kw in (r.get("keywords") or []):
            kw_counter[kw] += 1
        if r.get("emotion"):
            emo_counter[r["emotion"]] += 1

    return {
        "total_mentions": total,
        "top_keywords": [k for k, _ in kw_counter.most_common(5)],
        "emotion_dom": emo_counter.most_common(1)[0][0] if emo_counter else None,
        "trend_pct": None,  # Phase 1.5 (compare vs période précédente)
    }


def _aggregate_children(
    tenant_id: int, parent_id: int, category: str, period: str
) -> dict:
    """Somme les org_aggregations des enfants directs pour ce parent."""
    sql = """
        SELECT total_mentions, top_keywords, emotion_dom
        FROM org_aggregations
        WHERE tenant_id = %s
          AND org_unit_id IN (
            SELECT id FROM org_units WHERE parent_id = %s
          )
          AND category = %s
          AND period = %s
    """
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (tenant_id, parent_id, category, period))
        rows = cur.fetchall()

    total = sum((r.get("total_mentions") or 0) for r in rows)
    kw_counter: Counter = Counter()
    emo_counter: Counter = Counter()
    for r in rows:
        for kw in (r.get("top_keywords") or []):
            # On ré-additionne sans poids exact ; Phase 1 = approximation suffisante
            # (suite Phase 1.5 : stocker keyword_counts JSONB pour pondération vraie).
            kw_counter[kw] += 1
        if r.get("emotion_dom"):
            emo_counter[r["emotion_dom"]] += 1

    return {
        "total_mentions": total,
        "top_keywords": [k for k, _ in kw_counter.most_common(5)],
        "emotion_dom": emo_counter.most_common(1)[0][0] if emo_counter else None,
        "trend_pct": None,
    }


# ─── Persistance ────────────────────────────────────────────────────────────


def _upsert_aggregation(
    tenant_id: int, org_unit_id: int, category: str, period: str, agg: dict
) -> int:
    sql = """
        INSERT INTO org_aggregations (
            tenant_id, org_unit_id, category, period,
            total_mentions, trend_pct, top_keywords, emotion_dom, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (tenant_id, org_unit_id, category, period) DO UPDATE SET
            total_mentions = EXCLUDED.total_mentions,
            trend_pct = EXCLUDED.trend_pct,
            top_keywords = EXCLUDED.top_keywords,
            emotion_dom = EXCLUDED.emotion_dom,
            computed_at = NOW()
        RETURNING id
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                tenant_id,
                org_unit_id,
                category,
                period,
                agg["total_mentions"],
                agg["trend_pct"],
                agg["top_keywords"],
                agg["emotion_dom"],
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


# ─── Discovery des catégories actives ───────────────────────────────────────


def _categories_in_use(tenant_id: int, since: datetime) -> list[str]:
    """Liste des catégories rencontrées dans audience_reports récents."""
    sql = """
        SELECT DISTINCT unnest(categories) AS cat
        FROM audience_reports
        WHERE tenant_id = %s AND created_at >= %s AND categories IS NOT NULL
    """
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (tenant_id, since))
        return [r["cat"] for r in cur.fetchall() if r["cat"]]


# ─── Orchestration principale ───────────────────────────────────────────────


def run_rollup(
    tenant_id: int,
    period: str = "30d",
    categories: Optional[list[str]] = None,
) -> dict:
    """Recompute org_aggregations pour ce tenant, cette période, ces catégories.

    Cascade : trie les org_units par level_order ASC (leaves first, racine last).
    - Pour les leaves (sans enfants) : compute depuis audience_reports
    - Pour les parents : somme des enfants

    Si categories=None → autodiscover depuis audience_reports récents.

    Retour : {tenant_id, period, processed_units, categories_count, aggregations_written}
    """
    if period not in PERIOD_DELTAS:
        raise ValueError(f"period invalide : {period}. Attendu: {list(PERIOD_DELTAS)}")

    delta = PERIOD_DELTAS[period]
    since = datetime.utcnow() - delta

    if categories is None:
        categories = _categories_in_use(tenant_id, since)
    if not categories:
        return {
            "tenant_id": tenant_id, "period": period,
            "processed_units": 0, "categories_count": 0, "aggregations_written": 0,
        }

    # On trie les units par level_order ASC pour traiter feuilles en premier.
    # Note : level_order=0 est convention "le plus local" dans le YAML lesdemocrates
    # (section). Mais pour le cascade rollup il faut commencer par les leaves
    # (= units sans children) — qu'on identifie via un set parent_ids.
    units = storage.list_units(tenant_id)
    parent_ids = {u["parent_id"] for u in units if u.get("parent_id")}
    has_children = lambda u: u["id"] in parent_ids

    # Tri : leaves d'abord, puis parents par level_order croissant.
    units_sorted = sorted(units, key=lambda u: (has_children(u), u["level_order"]))

    written = 0
    for u in units_sorted:
        for cat in categories:
            if has_children(u):
                agg = _aggregate_children(tenant_id, u["id"], cat, period)
            else:
                agg = _compute_leaf(tenant_id, u["id"], cat, period)
            _upsert_aggregation(tenant_id, u["id"], cat, period, agg)
            written += 1

    return {
        "tenant_id": tenant_id,
        "period": period,
        "processed_units": len(units_sorted),
        "categories_count": len(categories),
        "aggregations_written": written,
    }


def get_aggregations(
    org_unit_id: int,
    period: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Lit les agrégations pour un unit (avec filtres optionnels)."""
    sql = "SELECT * FROM org_aggregations WHERE org_unit_id = %s"
    params: list = [org_unit_id]
    if period:
        sql += " AND period = %s"
        params.append(period)
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY category, period"
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
