"""tools/peer/peer_bus.py — bus messages inter-agents (Phase 3 métacognition).

Chaque message porte impérativement un `tenant` (clé d'isolation stricte).
Aucune fonction ne doit jamais permettre une lecture cross-tenant.

Workflow :
    1. ask_peer(tenant, from_agent, to_agent, question, context) → message_id
    2. get_inbox(tenant, agent) → messages pending pour cet agent
    3. answer_peer(message_id, answer) → marque le message comme answered
    4. get_thread(tenant, message_id) → conversation complète (un seul tour pour V1)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv("/opt/bizzi/bizzi/.env")

logger = logging.getLogger("tools.peer")

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    logger.warning("peer_bus: DATABASE_URL not set in environment")


def _conn():
    return psycopg2.connect(DB_URL)


def ask_peer(
    tenant: str,
    from_agent: str,
    to_agent: str,
    question: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crée un message pending de from_agent vers to_agent (même tenant)."""
    if not tenant or not from_agent or not to_agent or not question:
        return {"error": "missing_required_field"}
    ctx_json = json.dumps(context or {}, ensure_ascii=False, default=str)
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (tenant, from_agent, to_agent, question, context)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING id, created_at, status
            """,
            (tenant, from_agent, to_agent, question, ctx_json),
        )
        row = cur.fetchone()
    return {
        "message_id": row["id"],
        "status": row["status"],
        "tenant": tenant,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def get_inbox(
    tenant: str,
    agent: str,
    status: str = "pending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Liste les messages d'un agent pour un tenant donné. STRICTEMENT scoped."""
    if not tenant or not agent:
        return []
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, tenant, from_agent, to_agent, question, answer, status,
                   created_at, answered_at, context
            FROM agent_messages
            WHERE tenant = %s AND to_agent = %s AND status = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (tenant, agent, status, limit),
        )
        rows = cur.fetchall()
    return [_serialize(r) for r in rows]


def answer_peer(message_id: int, answer: str) -> dict[str, Any]:
    """Répond à un message pending. Idempotent (refuse si déjà answered)."""
    if not answer:
        return {"error": "missing_answer"}
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE agent_messages
            SET answer = %s, status = 'answered', answered_at = NOW()
            WHERE id = %s AND status = 'pending'
            RETURNING id, tenant, from_agent, to_agent, status, answered_at
            """,
            (answer, message_id),
        )
        row = cur.fetchone()
    if not row:
        return {"error": "message_not_found_or_already_answered"}
    return {
        "message_id": row["id"],
        "tenant": row["tenant"],
        "status": row["status"],
        "answered_at": row["answered_at"].isoformat() if row["answered_at"] else None,
    }


def get_thread(tenant: str, message_id: int) -> dict[str, Any] | None:
    """Récupère un message COMPLET (avec answer si présent), scoped par tenant."""
    if not tenant or not message_id:
        return None
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, tenant, from_agent, to_agent, question, answer, status,
                   created_at, answered_at, context
            FROM agent_messages
            WHERE tenant = %s AND id = %s
            """,
            (tenant, message_id),
        )
        row = cur.fetchone()
    return _serialize(row) if row else None


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if out.get("created_at"):
        out["created_at"] = out["created_at"].isoformat()
    if out.get("answered_at"):
        out["answered_at"] = out["answered_at"].isoformat()
    return out
