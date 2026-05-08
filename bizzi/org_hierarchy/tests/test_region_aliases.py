"""Tests _aliases_for et matching tolérant régions.

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_region_aliases
"""
from __future__ import annotations

from bizzi.org_hierarchy.storage import _aliases_for, _strip_diacritics, REGION_ALIASES


def test_strip_diacritics():
    assert _strip_diacritics("Île-de-France") == "ile-de-france"
    assert _strip_diacritics("Côte-d'Azur") == "cote-d'azur"
    print("  ✓ strip_diacritics")


def test_idf_aliases_include_idf_short_id():
    aliases = _aliases_for("Ile-de-France")
    assert "idf" in aliases
    assert "ile-de-france" in aliases  # lower + no diacritics
    print("  ✓ Ile-de-France aliases contain 'idf' (yaml short id)")


def test_unknown_region_keeps_self():
    aliases = _aliases_for("Mars")
    assert "Mars" in aliases or "mars" in aliases
    print("  ✓ unknown region keeps self as alias")


def test_all_regions_have_aliases():
    for label, aliases in REGION_ALIASES.items():
        assert len(aliases) >= 1, f"{label} has no aliases"
    print(f"  ✓ all {len(REGION_ALIASES)} regions have aliases")


if __name__ == "__main__":
    print("=== test_region_aliases ===")
    test_strip_diacritics()
    test_idf_aliases_include_idf_short_id()
    test_unknown_region_keeps_self()
    test_all_regions_have_aliases()
    print("OK")
