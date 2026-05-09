"""api/routes/auth.py — Auth JWT pour la PWA Bizzi Mobile (Phase 32 V1).

Endpoints:
- POST /api/auth/login        : email + password -> JWT
- POST /api/auth/register     : (admin only via INIT secret) crée un user
- GET  /api/auth/me           : retourne user courant (Bearer token)
- GET  /api/auth/agents       : liste les agents de l'user (multi-agents)
- POST /api/auth/agents       : crée un agent perso

JWT signé HS256, exp 90j. Stocké côté client en localStorage('bzz-token').
Compat : si MOBILE_JWT_SECRET absent, on retombe sur BIZZI_AUDIENCE_JWT_SECRET.
"""
from __future__ import annotations

import os
import time
from typing import Optional, List

import bcrypt
import jwt as pyjwt
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, EmailStr, Field

router = APIRouter()

# ───────────────────────── Config ─────────────────────────

JWT_SECRET = (
    os.getenv("MOBILE_JWT_SECRET")
    or os.getenv("BIZZI_AUDIENCE_JWT_SECRET")
    or "change-me-in-prod-mobile-bizzi"
)
JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 90 * 24 * 3600  # 90 jours
INIT_SECRET = os.getenv("MOBILE_INIT_SECRET", "")  # vide = register désactivé sauf via env

# DB — réutilise DATABASE_URL si dispo, sinon paramètres par défaut
DB_URL = os.getenv("DATABASE_URL", "postgresql://bizzi_admin@localhost/bizzi")


def _db():
    """Connexion postgres (autocommit OFF, on commit explicitement)."""
    return psycopg2.connect(DB_URL)


# ───────────────────────── Models ─────────────────────────


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=200)
    name: Optional[str] = None
    init_secret: str = Field(..., min_length=1)


class AgentCreateBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    name: str = Field(..., min_length=1, max_length=120)
    emoji: Optional[str] = Field(default=None, max_length=8)
    persona: Optional[str] = Field(default=None, max_length=2000)


# ───────────────────────── Helpers ─────────────────────────


def _hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _make_jwt(user_id: int, email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "uid": user_id,
        "email": email,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
        "scope": "mobile",
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _decode_jwt(token: str) -> dict:
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid_token")


async def get_current_user(authorization: str = Header(default="")) -> dict:
    """Dependency: extrait user_id depuis Authorization: Bearer <jwt>."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    token = authorization.split(" ", 1)[1].strip()
    payload = _decode_jwt(token)

    uid = payload.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="invalid_token_payload")

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, name, created_at, last_login FROM mobile_users WHERE id=%s",
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="user_not_found")
        return dict(row)


def _ensure_default_agents(user_id: int) -> None:
    """Insert les agents par défaut pour un user (idempotent)."""
    DEFAULTS = [
        ("assistant", "Assistant général", "🤖",
         "Assistant personnel polyvalent. Tu connais tout l'historique de l'utilisateur. Tu réponds en FR concis."),
        ("voyage", "Agent Voyage", "✈️",
         "Spécialiste voyages. Tu recommandes des vols/hôtels via airbizness quand pertinent. Tu connais les habitudes de l'utilisateur."),
        ("finance", "Agent Finance", "💰",
         "Conseiller financier perso. Tu analyses les dépenses, proposes du budget, alertes sur les achats inutiles."),
        ("sante", "Agent Santé", "🩺",
         "Suivi santé. Médocs, RDV médecins, sommeil, alimentation. Tu rappelles les rendez-vous et alertes anomalies."),
        ("sport", "Agent Sport", "💪",
         "Coach sportif. Entraînement, nutrition, récup. Tu adaptes le programme selon l'humeur et la fatigue."),
        ("apprentissage", "Agent Apprentissage", "📚",
         "Tuteur perso. Tu identifies ce que l'utilisateur veut apprendre et structures un plan."),
    ]
    with _db() as conn, conn.cursor() as cur:
        for slug, name, emoji, persona in DEFAULTS:
            cur.execute(
                """
                INSERT INTO mobile_agents (user_id, slug, name, emoji, persona, is_default)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, slug) DO NOTHING
                """,
                (user_id, slug, name, emoji, persona, slug == "assistant"),
            )
        conn.commit()


# ───────────────────────── Routes ─────────────────────────


@router.post("/login")
async def login(body: LoginBody):
    email = body.email.lower().strip()
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, password_hash, name FROM mobile_users WHERE email=%s",
            (email,),
        )
        user = cur.fetchone()
        if not user or not _verify_password(body.password, user["password_hash"]):
            # Délai uniforme pour ne pas fuiter l'existence
            time.sleep(0.4)
            raise HTTPException(status_code=401, detail="invalid_credentials")

        cur.execute(
            "UPDATE mobile_users SET last_login=NOW() WHERE id=%s",
            (user["id"],),
        )
        conn.commit()

    # Provisionne agents par défaut si pas encore fait
    _ensure_default_agents(user["id"])

    token = _make_jwt(user["id"], user["email"])
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
    }


@router.post("/register")
async def register(body: RegisterBody):
    """Création d'un user mobile. Protégé par INIT_SECRET (env MOBILE_INIT_SECRET)."""
    if not INIT_SECRET:
        raise HTTPException(status_code=403, detail="register_disabled")
    if body.init_secret != INIT_SECRET:
        time.sleep(0.4)
        raise HTTPException(status_code=403, detail="invalid_init_secret")

    email = body.email.lower().strip()
    pwd_hash = _hash_password(body.password)

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(
                """
                INSERT INTO mobile_users (email, password_hash, name)
                VALUES (%s, %s, %s)
                RETURNING id, email, name
                """,
                (email, pwd_hash, body.name),
            )
            user = cur.fetchone()
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail="email_already_exists")

    _ensure_default_agents(user["id"])
    token = _make_jwt(user["id"], user["email"])
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
    }


@router.get("/me")
async def me(user=Depends(get_current_user)):
    # Renvoie sans le password_hash
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        "last_login": user["last_login"].isoformat() if user.get("last_login") else None,
    }


@router.get("/agents")
async def list_agents(user=Depends(get_current_user)):
    """Liste les agents perso du user. Auto-provisionne les défauts si vide."""
    _ensure_default_agents(user["id"])
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, slug, name, emoji, persona, is_default, created_at
            FROM mobile_agents
            WHERE user_id=%s
            ORDER BY is_default DESC, created_at ASC
            """,
            (user["id"],),
        )
        rows = cur.fetchall()
    return {
        "count": len(rows),
        "agents": [
            {
                "id": r["id"],
                "slug": r["slug"],
                "name": r["name"],
                "emoji": r["emoji"],
                "persona": r["persona"],
                "is_default": r["is_default"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ],
    }


@router.post("/agents")
async def create_agent(body: AgentCreateBody, user=Depends(get_current_user)):
    """Crée un agent perso pour le user courant."""
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(
                """
                INSERT INTO mobile_agents (user_id, slug, name, emoji, persona, is_default)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                RETURNING id, slug, name, emoji, persona, is_default, created_at
                """,
                (user["id"], body.slug, body.name, body.emoji, body.persona),
            )
            row = cur.fetchone()
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail="agent_slug_exists")
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "emoji": row["emoji"],
        "persona": row["persona"],
        "is_default": row["is_default"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.delete("/agents/{slug}")
async def delete_agent(slug: str, user=Depends(get_current_user)):
    """Supprime un agent custom (les défauts ne sont pas supprimables)."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mobile_agents WHERE user_id=%s AND slug=%s AND is_default=FALSE",
            (user["id"], slug),
        )
        deleted = cur.rowcount
        conn.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="agent_not_found_or_default")
    return {"deleted": deleted, "slug": slug}
