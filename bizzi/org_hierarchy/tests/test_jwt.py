"""Smoke tests JWT minimal (pas de dépendance DB).

Lancer :
    cd /opt/bizzi && python -m bizzi.org_hierarchy.tests.test_jwt
"""
from __future__ import annotations

from bizzi.org_hierarchy import permissions
from bizzi.org_hierarchy.permissions import (
    JWTError,
    JWTScope,
    issue_jwt,
    verify_jwt,
)


def _expect_raises(fn, exc=JWTError, label=""):
    try:
        fn()
    except exc:
        print(f"  ✓ {label}")
        return
    raise AssertionError(f"expected {exc.__name__} for: {label}")


def test_roundtrip_basic():
    token = issue_jwt(tenant_id=4, role="secretaire_section", org_unit_id=12)
    scope = verify_jwt(token)
    assert isinstance(scope, JWTScope)
    assert scope.tenant_id == 4
    assert scope.role == "secretaire_section"
    assert scope.org_unit_id == 12
    print("  ✓ roundtrip basic")


def test_roundtrip_with_user_id():
    token = issue_jwt(tenant_id=4, role="instance_nationale", user_id="pascal")
    scope = verify_jwt(token)
    assert scope.user_id == "pascal"
    assert scope.org_unit_id is None
    print("  ✓ roundtrip with user_id, no org_unit")


def test_invalid_signature_rejected():
    token = issue_jwt(tenant_id=4, role="instance_nationale")
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    _expect_raises(lambda: verify_jwt(tampered), label="invalid signature rejected")


def test_malformed_rejected():
    _expect_raises(lambda: verify_jwt("a.b"), label="malformed (2 segments) rejected")
    _expect_raises(lambda: verify_jwt("missing-dots"), label="no dots rejected")


def test_expired_rejected():
    token = issue_jwt(tenant_id=4, role="local", ttl_seconds=-1)
    _expect_raises(lambda: verify_jwt(token), label="expired token rejected")


def test_secret_isolation():
    original = permissions.JWT_SECRET
    try:
        permissions.JWT_SECRET = "secret-A"
        token = issue_jwt(tenant_id=1, role="admin")
        permissions.JWT_SECRET = "secret-B"
        _expect_raises(lambda: verify_jwt(token), label="cross-secret signature rejected")
    finally:
        permissions.JWT_SECRET = original


if __name__ == "__main__":
    print("=== test_jwt ===")
    test_roundtrip_basic()
    test_roundtrip_with_user_id()
    test_invalid_signature_rejected()
    test_malformed_rejected()
    test_expired_rejected()
    test_secret_isolation()
    print("OK")
