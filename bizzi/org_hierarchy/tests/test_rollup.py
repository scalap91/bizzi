"""Tests rollup — pure logique, DB mockée.

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_rollup
"""
from __future__ import annotations

from unittest.mock import patch

from bizzi.org_hierarchy import rollup


def test_invalid_period_raises():
    try:
        rollup.run_rollup(4, period="1y")
    except ValueError as e:
        assert "1y" in str(e)
        print("  ✓ invalid period raises ValueError")
        return
    raise AssertionError("expected ValueError")


def test_no_categories_returns_zero():
    with patch.object(rollup, "_categories_in_use", return_value=[]):
        out = rollup.run_rollup(4, period="30d")
    assert out["aggregations_written"] == 0
    assert out["categories_count"] == 0
    print("  ✓ no categories in use → zero aggregations")


def test_cascade_orders_leaves_first():
    units = [
        {"id": 1, "level": "national",   "level_order": 3, "parent_id": None},
        {"id": 2, "level": "federation", "level_order": 1, "parent_id": 1},
        {"id": 3, "level": "section",    "level_order": 0, "parent_id": 2},
        {"id": 4, "level": "section",    "level_order": 0, "parent_id": 2},
    ]
    written_order: list[int] = []

    def fake_upsert(tenant_id, org_unit_id, category, period, agg):
        written_order.append(org_unit_id)
        return 1

    def fake_compute_leaf(tenant_id, org_unit_id, category, period):
        return {"total_mentions": 5, "top_keywords": ["a"], "emotion_dom": "neutre", "trend_pct": None}

    def fake_aggregate_children(tenant_id, parent_id, category, period):
        return {"total_mentions": 10, "top_keywords": ["a"], "emotion_dom": "neutre", "trend_pct": None}

    with patch.object(rollup, "storage") as st_mock, patch.object(
        rollup, "_compute_leaf", side_effect=fake_compute_leaf
    ), patch.object(rollup, "_aggregate_children", side_effect=fake_aggregate_children), patch.object(
        rollup, "_upsert_aggregation", side_effect=fake_upsert
    ), patch.object(rollup, "_categories_in_use", return_value=["securite"]):
        st_mock.list_units.return_value = units
        rollup.run_rollup(4, period="30d")

    # Sections (3, 4) doivent être écrites avant la fédération (2) et le national (1).
    assert written_order.index(3) < written_order.index(2)
    assert written_order.index(4) < written_order.index(2)
    assert written_order.index(2) < written_order.index(1)
    print("  ✓ cascade order: sections → fédération → national")


if __name__ == "__main__":
    print("=== test_rollup ===")
    test_invalid_period_raises()
    test_no_categories_returns_zero()
    test_cascade_orders_leaves_first()
    print("OK")
