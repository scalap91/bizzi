"""Smoke tests yaml_loader (storage mocké, pas de DB).

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_yaml_loader
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from bizzi.org_hierarchy import yaml_loader


SAMPLE = {
    "org_hierarchy": {
        "enabled": True,
        "levels": [
            {"id": "section", "label": "Section locale", "order": 0},
            {"id": "federation", "label": "Fédération", "order": 1},
            {"id": "national", "label": "National", "order": 3},
        ],
        "units": [
            {"id": "national_root", "level": "national", "name": "BN"},
            {"id": "fed_91", "level": "federation", "name": "Essonne", "parent": "national_root"},
            {"id": "section_evry", "level": "section", "name": "Évry", "parent": "fed_91"},
        ],
        "geo_mapping": {
            "Évry": "section_evry",
        },
    }
}


def _write(tmp: Path, slug: str, data: dict) -> None:
    (tmp / f"{slug}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_populate_two_passes(tmp: Path):
    _write(tmp, "demo", SAMPLE)
    upserts: list[dict] = []
    geo_calls: list[dict] = []

    def fake_upsert(**kwargs):
        upserts.append(kwargs)
        return abs(hash(kwargs["external_id"])) % 10_000

    def fake_geo(**kwargs):
        geo_calls.append(kwargs)
        return 1

    with patch.object(yaml_loader, "DOMAINS_DIR", tmp), patch(
        "bizzi.org_hierarchy.yaml_loader.storage.upsert_unit", side_effect=fake_upsert
    ), patch(
        "bizzi.org_hierarchy.yaml_loader.storage.upsert_geo_mapping", side_effect=fake_geo
    ):
        stats = yaml_loader.populate_from_yaml(tenant_id=4, slug="demo")

    assert stats == {"units_upserted": 3, "geo_upserted": 1, "levels_count": 3}
    # Passe 1 : 3 units sans parent_id ; Passe 2 : 2 units avec parent (national_root racine).
    assert len(upserts) == 5
    pass1 = upserts[:3]
    assert {u["external_id"] for u in pass1} == {"national_root", "fed_91", "section_evry"}
    assert all(u["parent_id"] is None for u in pass1)
    pass2 = upserts[3:]
    assert {u["external_id"] for u in pass2} == {"fed_91", "section_evry"}
    assert all(u["parent_id"] is not None for u in pass2)
    assert geo_calls[0]["city"] == "Évry"
    print("  ✓ populate two passes (3 + 2 upserts, parents resolved)")


def test_disabled_section(tmp: Path):
    _write(tmp, "off", {"org_hierarchy": {"enabled": False, "levels": [], "units": []}})
    with patch.object(yaml_loader, "DOMAINS_DIR", tmp):
        stats = yaml_loader.populate_from_yaml(tenant_id=1, slug="off")
    assert stats == {"units_upserted": 0, "geo_upserted": 0, "levels_count": 0}
    print("  ✓ disabled section returns zero")


def test_unknown_parent_raises(tmp: Path):
    _write(tmp, "bad", {
        "org_hierarchy": {
            "enabled": True,
            "levels": [{"id": "section", "label": "S", "order": 0}],
            "units": [{"id": "section_a", "level": "section", "name": "A", "parent": "ghost"}],
        }
    })
    with patch.object(yaml_loader, "DOMAINS_DIR", tmp), patch(
        "bizzi.org_hierarchy.yaml_loader.storage.upsert_unit", return_value=1
    ), patch(
        "bizzi.org_hierarchy.yaml_loader.storage.get_unit_by_external_id",
        return_value=None,
    ):
        try:
            yaml_loader.populate_from_yaml(tenant_id=1, slug="bad")
        except ValueError as e:
            assert "ghost" in str(e)
            print("  ✓ unknown parent raises ValueError")
            return
    raise AssertionError("expected ValueError for unknown parent")


def test_unknown_level_raises(tmp: Path):
    _write(tmp, "bad2", {
        "org_hierarchy": {
            "enabled": True,
            "levels": [{"id": "section", "label": "S", "order": 0}],
            "units": [{"id": "x", "level": "phantom_level", "name": "X"}],
        }
    })
    with patch.object(yaml_loader, "DOMAINS_DIR", tmp):
        try:
            yaml_loader.populate_from_yaml(tenant_id=1, slug="bad2")
        except ValueError as e:
            assert "phantom_level" in str(e)
            print("  ✓ unknown level raises ValueError")
            return
    raise AssertionError("expected ValueError for unknown level")


if __name__ == "__main__":
    print("=== test_yaml_loader ===")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_populate_two_passes(tmp)
    with tempfile.TemporaryDirectory() as d:
        test_disabled_section(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_unknown_parent_raises(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_unknown_level_raises(Path(d))
    print("OK")
