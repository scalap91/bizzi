"""bizzi.org_hierarchy.permissions — JWT scope + helper get_visible_units.

Cœur de la coordination avec bizzi-audience : audience filtre toutes ses queries
audience_reports avec org_unit_id IN get_visible_units(scope).

Logique :
- Le tenant signe un JWT HS256 avec {tenant_id, role, org_unit_id, exp}.
- Bizzi vérifie la signature avec une clé partagée (BIZZI_JWT_SECRET).
- get_visible_units(scope) renvoie la liste des org_unit_id que ce scope peut voir.

Phase 0 : règles génériques basées sur le rôle.
Phase 1 : règles surchargeables par tenant via YAML org_hierarchy.permissions.

Implémentation JWT : HS256 minimal stdlib (hmac + hashlib + base64 + json) pour
éviter une dépendance PyJWT non installée. Compatible RFC 7519 (header.payload.sig).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Optional

from . import storage
from .models import JWTScope


JWT_SECRET = os.getenv("BIZZI_JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"


# ─── Roles génériques (les tenants peuvent en définir d'autres dans leur YAML) ─

# can_view : "own" → uniquement son org_unit
#            "own+descendants" → son org_unit + tous descendants
#            "all" → toutes les org_units du tenant
ROLE_RULES_DEFAULT: dict[str, str] = {
    # Politique — Les Démocrates
    "secretaire_section": "own",
    "secretaire_federal": "own+descendants",
    "responsable_territorial": "own+descendants",
    "instance_nationale": "all",
    "administrateur_autorise": "all",
    # Génériques cross-tenant
    "local": "own",
    "intermediate": "own+descendants",
    "global": "all",
    "admin": "all",
}


# ─── JWT minimal HS256 (RFC 7519 compatible) ────────────────────────────────


class JWTError(Exception):
    pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(message: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()


def issue_jwt(
    tenant_id: int,
    role: str,
    org_unit_id: Optional[int] = None,
    user_id: Optional[str] = None,
    ttl_seconds: int = 3600,
) -> str:
    """Helper utilitaire (tests, dev). En prod c'est le tenant qui signe ses JWTs."""
    payload: dict[str, Any] = {
        "tenant_id": int(tenant_id),
        "role": str(role),
        "exp": int(time.time()) + ttl_seconds,
    }
    if org_unit_id is not None:
        payload["org_unit_id"] = int(org_unit_id)
    if user_id is not None:
        payload["user_id"] = str(user_id)

    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    h_enc = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p_enc = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h_enc}.{p_enc}".encode("ascii")
    sig = _b64url_encode(_sign(signing_input, JWT_SECRET))
    return f"{h_enc}.{p_enc}.{sig}"


def verify_jwt(token: str) -> JWTScope:
    """Vérifie signature + exp et retourne le scope. Lève JWTError sinon."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed token (expected 3 segments)")
    h_enc, p_enc, sig_enc = parts

    try:
        header = json.loads(_b64url_decode(h_enc))
        payload = json.loads(_b64url_decode(p_enc))
        sig = _b64url_decode(sig_enc)
    except (ValueError, json.JSONDecodeError) as e:
        raise JWTError(f"decode error: {e}") from e

    if header.get("alg") != JWT_ALGORITHM:
        raise JWTError(f"unsupported alg: {header.get('alg')}")

    expected = _sign(f"{h_enc}.{p_enc}".encode("ascii"), JWT_SECRET)
    if not hmac.compare_digest(sig, expected):
        raise JWTError("invalid signature")

    exp = payload.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        raise JWTError("token expired")

    if "tenant_id" not in payload or "role" not in payload:
        raise JWTError("missing required claims (tenant_id, role)")

    return JWTScope(
        tenant_id=int(payload["tenant_id"]),
        role=str(payload["role"]),
        org_unit_id=payload.get("org_unit_id"),
        user_id=payload.get("user_id"),
        exp=payload.get("exp"),
    )


# ─── Visibility ─────────────────────────────────────────────────────────────


def get_visible_units(scope: JWTScope, role_rules: Optional[dict[str, str]] = None) -> list[int]:
    """Retourne les org_unit_id visibles pour ce scope.

    Utilisé par bizzi-audience dans toutes ses queries pour filtrer
    audience_reports.org_unit_id IN visible.

    Si le rôle est inconnu → liste vide (deny par défaut).
    """
    rules = role_rules or ROLE_RULES_DEFAULT
    rule = rules.get(scope.role)
    if rule is None:
        return []

    if rule == "all":
        return [u["id"] for u in storage.list_units(scope.tenant_id)]

    if scope.org_unit_id is None:
        return []

    if rule == "own":
        return [int(scope.org_unit_id)]

    if rule == "own+descendants":
        descendants = storage.get_descendants(int(scope.org_unit_id))
        return [int(scope.org_unit_id)] + [d["id"] for d in descendants]

    return []


def can_broadcast(scope: JWTScope) -> bool:
    """Phase 0 : seuls 'all' peuvent broadcast. Phase 1 : configurable par YAML."""
    rule = ROLE_RULES_DEFAULT.get(scope.role)
    return rule == "all"
