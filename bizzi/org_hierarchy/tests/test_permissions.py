"""Smoke tests get_visible_units (storage mocké, pas de DB).

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_permissions
"""
from __future__ import annotations

from unittest.mock import patch

from bizzi.org_hierarchy.models import JWTScope
from bizzi.org_hierarchy.permissions import get_visible_units


def _scope(role: str, org_unit_id=None, tenant_id=4) -> JWTScope:
    return JWTScope(tenant_id=tenant_id, role=role, org_unit_id=org_unit_id)


def test_unknown_role_denies():
    assert get_visible_units(_scope("alien_role")) == []
    print("  ✓ unknown role denies (deny by default)")


def test_role_all_returns_all_tenant_units():
    fake_units = [{"id": 1}, {"id": 2}, {"id": 3}]
    with patch("bizzi.org_hierarchy.permissions.storage.list_units", return_value=fake_units):
        result = get_visible_units(_scope("instance_nationale"))
    assert sorted(result) == [1, 2, 3]
    print("  ✓ role 'all' returns every tenant unit")


def test_role_own_returns_only_self():
    result = get_visible_units(_scope("secretaire_section", org_unit_id=12))
    assert result == [12]
    print("  ✓ role 'own' returns only self")


def test_role_own_without_org_unit_id_denies():
    """Sécurité : un rôle 'own' sans org_unit_id ne voit rien."""
    result = get_visible_units(_scope("secretaire_section", org_unit_id=None))
    assert result == []
    print("  ✓ role 'own' without org_unit_id denies (security)")


def test_role_own_descendants_returns_self_plus_descendants():
    descendants = [{"id": 20}, {"id": 21}, {"id": 22}]
    with patch(
        "bizzi.org_hierarchy.permissions.storage.get_descendants",
        return_value=descendants,
    ):
        result = get_visible_units(_scope("secretaire_federal", org_unit_id=10))
    assert sorted(result) == [10, 20, 21, 22]
    print("  ✓ role 'own+descendants' returns self plus subtree")


def test_custom_role_rules_override():
    descendants = [{"id": 99}]
    with patch(
        "bizzi.org_hierarchy.permissions.storage.get_descendants",
        return_value=descendants,
    ):
        result = get_visible_units(
            _scope("custom_x", org_unit_id=5),
            role_rules={"custom_x": "own+descendants"},
        )
    assert sorted(result) == [5, 99]
    print("  ✓ custom role_rules override works")


if __name__ == "__main__":
    print("=== test_permissions ===")
    test_unknown_role_denies()
    test_role_all_returns_all_tenant_units()
    test_role_own_returns_only_self()
    test_role_own_without_org_unit_id_denies()
    test_role_own_descendants_returns_self_plus_descendants()
    test_custom_role_rules_override()
    print("OK")
