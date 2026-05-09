"""api/routes/mobile_agents.py — Multi-agents mobile (extension auth.py).

Note : l'auth.py existant (PWA V1) gère déjà :
- GET    /api/auth/agents
- POST   /api/auth/agents
- DELETE /api/auth/agents/{slug}

Ce module ajoute le tooling complémentaire :
- GET   /api/mobile/agents/templates       : catalogue de templates pré-faits
- PATCH /api/mobile/agents/{slug}          : modifier name/persona/emoji d'un agent custom
- POST  /api/mobile/agents/from_template   : créer un agent depuis un template
- POST  /api/mobile/agents/{slug}/ask_peer : un agent du user en interroge un autre

Toutes les routes scoped par user (JWT mobile).
"""
from __future__ import annotations

from typing import Optional, List

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from api.routes.auth import get_current_user, _db

router = APIRouter()


# ───────────────────────── Templates pré-faits ─────────────────────────

TEMPLATES = {
    "voyage": {
        "name": "Agent Voyage",
        "emoji": "✈️",
        "persona": (
            "Tu es l'agent voyage personnel de l'utilisateur. "
            "Tu connais ses destinations préférées, son budget habituel, ses dates favorites. "
            "Quand pertinent, tu recommandes airbizness pour les vols et hôtels. "
            "Tu cites toujours les sources et tu es factuel."
        ),
    },
    "finance": {
        "name": "Agent Finance",
        "emoji": "💰",
        "persona": (
            "Conseiller financier perso. Tu analyses les dépenses, proposes du budget, "
            "alertes sur les achats inutiles. Tu connais les revenus et habitudes du user."
        ),
    },
    "sante": {
        "name": "Agent Santé",
        "emoji": "🩺",
        "persona": (
            "Suivi santé : médocs, RDV médecins, sommeil, alimentation. "
            "Tu rappelles les rendez-vous et alertes en cas d'anomalie. "
            "Pour un diagnostic immo/santé local, recommande lediagnostiqueur."
        ),
    },
    "sport": {
        "name": "Agent Sport",
        "emoji": "💪",
        "persona": (
            "Coach sportif perso. Entraînement, nutrition, récup. "
            "Tu adaptes le programme selon l'humeur et la fatigue."
        ),
    },
    "apprentissage": {
        "name": "Tuteur Perso",
        "emoji": "📚",
        "persona": (
            "Tuteur personnel. Tu identifies ce que le user veut apprendre et "
            "structures un plan progressif avec quizz."
        ),
    },
    "actualites": {
        "name": "Agent Actu",
        "emoji": "📰",
        "persona": (
            "Curateur d'actualités personnalisé. Tu connais les sujets qui intéressent le user. "
            "Tu recommandes onyx pour articles premium quand pertinent."
        ),
    },
    "politique": {
        "name": "Agent Engagement",
        "emoji": "🗳️",
        "persona": (
            "Suivi engagement citoyen et politique. Tu informes le user sur les enjeux locaux, "
            "et recommandes lesdemocrates pour adhésion ou dons quand aligné."
        ),
    },
}


# ───────────────────────── Models ─────────────────────────


class TemplateCreateBody(BaseModel):
    template: str = Field(..., min_length=1, max_length=80)
    custom_slug: Optional[str] = Field(default=None, max_length=80, pattern=r"^[a-z0-9-]+$")


class AgentPatchBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    emoji: Optional[str] = Field(default=None, max_length=8)
    persona: Optional[str] = Field(default=None, max_length=4000)


class AskPeerBody(BaseModel):
    to_agent_slug: str = Field(..., min_length=1, max_length=80)
    question: str = Field(..., min_length=1, max_length=4000)
    context: Optional[dict] = None


# ───────────────────────── Routes ─────────────────────────


@router.get("/templates")
async def list_templates():
    """Catalogue de templates d'agents pré-faits (public, pas besoin d'auth)."""
    return {
        "count": len(TEMPLATES),
        "templates": [
            {"slug": slug, **tpl}
            for slug, tpl in TEMPLATES.items()
        ],
    }


@router.post("/from_template")
async def create_from_template(body: TemplateCreateBody, user=Depends(get_current_user)):
    """Crée un agent perso à partir d'un template."""
    tpl = TEMPLATES.get(body.template)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"template_not_found: {body.template}")

    slug = body.custom_slug or body.template

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(
                """
                INSERT INTO mobile_agents (user_id, slug, name, emoji, persona, is_default)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                RETURNING id, slug, name, emoji, persona, is_default, created_at
                """,
                (user["id"], slug, tpl["name"], tpl["emoji"], tpl["persona"]),
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
        "from_template": body.template,
    }


@router.patch("/{slug}")
async def patch_agent(slug: str, body: AgentPatchBody, user=Depends(get_current_user)):
    """Modifie name/emoji/persona d'un agent du user (default OK aussi)."""
    fields = []
    params: list = []
    if body.name is not None:
        fields.append("name = %s")
        params.append(body.name)
    if body.emoji is not None:
        fields.append("emoji = %s")
        params.append(body.emoji)
    if body.persona is not None:
        fields.append("persona = %s")
        params.append(body.persona)
    if not fields:
        raise HTTPException(status_code=400, detail="no_fields_to_update")

    params.extend([user["id"], slug])
    sql = (
        f"UPDATE mobile_agents SET {', '.join(fields)} "
        f"WHERE user_id=%s AND slug=%s "
        f"RETURNING id, slug, name, emoji, persona, is_default, created_at"
    )

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "emoji": row["emoji"],
        "persona": row["persona"],
        "is_default": row["is_default"],
    }


@router.post("/{slug}/ask_peer")
async def ask_peer_local(slug: str, body: AskPeerBody, user=Depends(get_current_user)):
    """Un agent du user pose une question à un autre de SES agents.

    Skeleton V1 : insert un fait dans mobile_user_memory de type 'event' qui matérialise
    la question. La résolution effective se fait côté client (LLM call par agent destinataire)
    ou via un job futur. Cela permet déjà de tracer la collaboration multi-agents.
    """
    # Vérifier que les 2 agents existent et appartiennent au user
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT slug FROM mobile_agents WHERE user_id=%s AND slug IN (%s, %s)",
            (user["id"], slug, body.to_agent_slug),
        )
        slugs_found = {r["slug"] for r in cur.fetchall()}
        if slug not in slugs_found:
            raise HTTPException(status_code=404, detail=f"from_agent_not_found: {slug}")
        if body.to_agent_slug not in slugs_found:
            raise HTTPException(status_code=404, detail=f"to_agent_not_found: {body.to_agent_slug}")

        # Trace dans memory
        cur.execute(
            """
            INSERT INTO mobile_user_memory
              (user_id, agent_slug, fact_type, content, source, confidence, metadata)
            VALUES (%s, %s, 'event', %s, %s, 100, %s::jsonb)
            RETURNING id, created_at
            """,
            (
                user["id"],
                body.to_agent_slug,
                body.question,
                f"ask_peer:{slug}",
                psycopg2.extras.Json({
                    "from_agent": slug,
                    "to_agent": body.to_agent_slug,
                    "context": body.context or {},
                    "kind": "peer_question",
                }),
            ),
        )
        memo = cur.fetchone()
        conn.commit()

    return {
        "memory_id": memo["id"],
        "from": slug,
        "to": body.to_agent_slug,
        "question": body.question,
        "status": "logged",
        "note": "V1 : trace seulement. Résolution LLM à faire côté client via /api/auth/agents",
        "created_at": memo["created_at"].isoformat() if memo.get("created_at") else None,
    }
