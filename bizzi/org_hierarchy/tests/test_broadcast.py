"""Tests broadcast resolve_targets — pure logique, storage mocké.

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_broadcast
"""
from __future__ import annotations

from unittest.mock import patch

from bizzi.org_hierarchy import broadcast


_TENANT = 4

_FAKE_UNITS = [
    {"id": 1, "tenant_id": 4, "level": "national",   "external_id": "national_bn",     "geo_meta": None,                  "parent_id": None, "level_order": 3, "name": "BN"},
    {"id": 2, "tenant_id": 4, "level": "region",     "external_id": "region_idf",      "geo_meta": {"region_id": "idf"},  "parent_id": 1,    "level_order": 2, "name": "IDF"},
    {"id": 3, "tenant_id": 4, "level": "federation", "external_id": "fed_91",          "geo_meta": {"region_id": "idf"},  "parent_id": 2,    "level_order": 1, "name": "F91"},
    {"id": 4, "tenant_id": 4, "level": "section",    "external_id": "section_evry",    "geo_meta": {"region_id": "idf"},  "parent_id": 3,    "level_order": 0, "name": "Evry"},
    {"id": 5, "tenant_id": 4, "level": "section",    "external_id": "section_lisses",  "geo_meta": {"region_id": "idf"},  "parent_id": 3,    "level_order": 0, "name": "Lisses"},
]


def _list_units_mock(tenant_id, level=None):
    if level:
        return [u for u in _FAKE_UNITS if u["level"] == level]
    return list(_FAKE_UNITS)


def test_all_filter():
    with patch("bizzi.org_hierarchy.broadcast.storage.list_units", side_effect=_list_units_mock):
        ids = broadcast.resolve_targets(_TENANT, {"all": True})
    assert sorted(ids) == [1, 2, 3, 4, 5]
    print("  ✓ {'all': true} returns all tenant units")


def test_level_filter():
    with patch("bizzi.org_hierarchy.broadcast.storage.list_units", side_effect=_list_units_mock):
        ids = broadcast.resolve_targets(_TENANT, {"level": "section"})
    assert sorted(ids) == [4, 5]
    print("  ✓ {'level': 'section'} returns only sections")


def test_level_plus_region_filter():
    with patch("bizzi.org_hierarchy.broadcast.storage.list_units", side_effect=_list_units_mock):
        ids = broadcast.resolve_targets(_TENANT, {"level": "section", "region_id": "idf"})
    assert sorted(ids) == [4, 5]
    # Avec region_id qui ne matche aucun unit → vide
    with patch("bizzi.org_hierarchy.broadcast.storage.list_units", side_effect=_list_units_mock):
        ids = broadcast.resolve_targets(_TENANT, {"level": "section", "region_id": "ghost"})
    assert ids == []
    print("  ✓ {'level','region_id'} filtre correctement (et région inconnue → vide)")


def test_unit_external_ids_filter():
    def get_by_ext(tenant_id, ext_id):
        for u in _FAKE_UNITS:
            if u["external_id"] == ext_id:
                return u
        return None
    with patch("bizzi.org_hierarchy.broadcast.storage.get_unit_by_external_id", side_effect=get_by_ext):
        ids = broadcast.resolve_targets(
            _TENANT, {"unit_external_ids": ["section_evry", "fed_91", "ghost"]}
        )
    assert sorted(ids) == [3, 4]
    print("  ✓ unit_external_ids resolves ids, skips unknown")


def test_descendant_of_filter():
    fake_descendants = [
        {"id": 3}, {"id": 4}, {"id": 5},
    ]
    with patch(
        "bizzi.org_hierarchy.broadcast.storage.get_descendants",
        return_value=fake_descendants,
    ):
        ids = broadcast.resolve_targets(_TENANT, {"descendant_of": 2})
    assert sorted(ids) == [3, 4, 5]
    print("  ✓ descendant_of resolves subtree")


def test_empty_filter_returns_empty():
    """Filtre vide non interpreté comme 'all' → vide (sécurité)."""
    ids = broadcast.resolve_targets(_TENANT, {"unit_external_ids": []})
    assert ids == []
    print("  ✓ empty unit_external_ids → empty (no fallthrough to all)")


if __name__ == "__main__":
    print("=== test_broadcast ===")
    test_all_filter()
    test_level_filter()
    test_level_plus_region_filter()
    test_unit_external_ids_filter()
    test_descendant_of_filter()
    test_empty_filter_returns_empty()
    print("OK")
