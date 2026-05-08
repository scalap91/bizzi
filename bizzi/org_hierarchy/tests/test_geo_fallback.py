"""Smoke tests resolve_city_with_fallback (storage mocké, region_detector réel).

Vérifie le wiring : geo_mapping principal, fallback region_detector si absent.

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_geo_fallback
"""
from __future__ import annotations

from unittest.mock import patch

from bizzi.org_hierarchy import storage


def test_geo_mapping_wins_when_present():
    fake_match = {
        "id": 1, "tenant_id": 4, "city": "Évry", "org_unit_id": 10,
        "unit_name": "Section Évry", "unit_level": "section",
        "unit_external_id": "section_evry",
    }
    with patch.object(storage, "resolve_city", return_value=fake_match) as m:
        result = storage.resolve_city_with_fallback(4, "Évry")
    assert result["source"] == "geo_mapping"
    assert result["match"] == fake_match
    assert result["detected_region"] is None
    m.assert_called_once_with(4, "Évry")
    print("  ✓ geo_mapping wins when present")


def test_region_detector_fallback_when_geo_mapping_misses():
    # Ville inconnue de geo_mapping mais détectable par region_detector.
    fake_unit = {
        "id": 99, "tenant_id": 4, "name": "Région Île-de-France",
        "level": "region", "external_id": "region_idf",
        "geo_meta": {"region_id": "Ile-de-France"},
    }
    with patch.object(storage, "resolve_city", return_value=None), patch.object(
        storage, "_find_unit_by_region", return_value=fake_unit
    ) as m:
        result = storage.resolve_city_with_fallback(
            4, "Versailles", content="Article sur Versailles, Yvelines"
        )
    assert result["source"] == "region_detector"
    assert result["detected_region"] == "Ile-de-France"
    assert result["match"] == fake_unit
    m.assert_called_once_with(4, "Ile-de-France")
    print("  ✓ region_detector fallback hits when geo_mapping misses")


def test_no_match_anywhere():
    with patch.object(storage, "resolve_city", return_value=None), patch.object(
        storage, "_find_unit_by_region", return_value=None
    ):
        result = storage.resolve_city_with_fallback(
            4, "Tombouctou", content="ville africaine"
        )
    assert result["match"] is None
    assert result["source"] in ("none", "region_detector")
    print("  ✓ no match anywhere → match=None graceful")


def test_region_detected_but_no_unit_with_that_region():
    """Région détectée par fallback, mais aucun org_unit du tenant ne couvre cette région."""
    with patch.object(storage, "resolve_city", return_value=None), patch.object(
        storage, "_find_unit_by_region", return_value=None
    ):
        result = storage.resolve_city_with_fallback(
            4, "Lille", content="Lille Hauts-de-France"
        )
    assert result["match"] is None
    assert result["source"] == "region_detector"
    assert result["detected_region"] == "Hauts-de-France"
    print("  ✓ region detected without matching unit → match=None, region preserved")


if __name__ == "__main__":
    print("=== test_geo_fallback ===")
    test_geo_mapping_wins_when_present()
    test_region_detector_fallback_when_geo_mapping_misses()
    test_no_match_anywhere()
    test_region_detected_but_no_unit_with_that_region()
    print("OK")
