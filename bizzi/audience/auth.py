"""JWT HS256 minimal — sans dépendance externe (PyJWT non installable
sans sudo dans ce venv). Implémentation conforme RFC 7519 / 7515.

Le tenant forge un JWT via son propre backend en utilisant la clé partagée
`BIZZI_AUDIENCE_JWT_SECRET` (env). Bizzi vérifie signature + exp + scope.

Payload attendu :
{
  "tenant_id":   int,
  "role":        "admin" | "secretaire_section" | "membre" | ...,
  "org_unit_id": int | null,
  "user_ref":    str (id stable opaque, optionnel),
  "exp":         int unix timestamp,
  "iat":         int unix timestamp (optionnel),
  "iss":         str (optionnel)
}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _secret_bytes() -> bytes:
    s = os.environ.get("BIZZI_AUDIENCE_JWT_SECRET", "")
    if not s:
        # Dev/test only : clé instable. La signature ne sera valide que
        # tant que le process tourne. En prod, BIZZI_AUDIENCE_JWT_SECRET
        # DOIT être défini.
        s = "DEV_INSECURE_BIZZI_AUDIENCE_SECRET_DO_NOT_USE_IN_PROD"
    return s.encode("utf-8")


class JWTError(Exception):
    pass


@dataclass(frozen=True)
class JWTClaims:
    tenant_id: int
    role: str
    org_unit_id: Optional[int]
    user_ref: Optional[str]
    exp: int
    iat: Optional[int] = None
    iss: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


def encode_jwt(payload: dict[str, Any], *, exp_seconds: int = 3600) -> str:
    """Forge un JWT HS256 — utilisé surtout en test ; en prod le tenant signe."""
    p = dict(payload)
    p.setdefault("iat", int(time.time()))
    p.setdefault("exp", int(time.time()) + int(exp_seconds))

    header = {"alg": "HS256", "typ": "JWT"}
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64url_encode(json.dumps(p, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(_secret_bytes(), signing_input, hashlib.sha256).digest()
    s_b64 = _b64url_encode(sig)
    return f"{h_b64}.{p_b64}.{s_b64}"


def decode_jwt(token: str) -> JWTClaims:
    if not token or token.count(".") != 2:
        raise JWTError("malformed token")
    h_b64, p_b64, s_b64 = token.split(".")
    try:
        header = json.loads(_b64url_decode(h_b64))
        payload = json.loads(_b64url_decode(p_b64))
        sig = _b64url_decode(s_b64)
    except Exception as e:  # noqa: BLE001
        raise JWTError(f"decode error: {e}") from None

    if header.get("alg") != "HS256":
        raise JWTError(f"unsupported alg: {header.get('alg')}")
    if header.get("typ") not in (None, "JWT"):
        raise JWTError(f"unsupported typ: {header.get('typ')}")

    expected = hmac.new(
        _secret_bytes(),
        f"{h_b64}.{p_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(sig, expected):
        raise JWTError("signature mismatch")

    exp = int(payload.get("exp", 0))
    if exp and exp < int(time.time()):
        raise JWTError("token expired")

    tid = payload.get("tenant_id")
    if not isinstance(tid, int):
        raise JWTError("tenant_id missing or not int")
    role = payload.get("role")
    if not isinstance(role, str) or not role:
        raise JWTError("role missing")

    return JWTClaims(
        tenant_id=tid,
        role=role,
        org_unit_id=payload.get("org_unit_id"),
        user_ref=payload.get("user_ref"),
        exp=exp,
        iat=payload.get("iat"),
        iss=payload.get("iss"),
        raw=payload,
    )


def claims_from_request_token(token: Optional[str]) -> JWTClaims:
    """Helper : lève HTTPException-friendly via JWTError. Le caller mappe."""
    if not token:
        raise JWTError("missing token")
    return decode_jwt(token.strip())
