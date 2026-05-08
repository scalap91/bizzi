"""bizzi.phone.memory — recall/store sur agent_memories (sans pgvector pour Phase 0).

Table agent_memories(id, tenant_id, agent_id, scope, memory_type, key, title, content,
                     tags jsonb, importance, related_production_id, expires_at, ...).

Phase 0 : recall = full-text ILIKE + tag match + tri par importance/recency.
Phase 1 : pgvector embedding sur content.
"""
import json
from typing import Optional
from ._db import get_conn

ALLOWED_TYPES = {"note", "fact", "source", "contact", "style", "rule", "event"}
ALLOWED_SCOPES = {"private", "shared", "team", "global"}


def store_memory(
    tenant_id: int,
    agent_id: Optional[int],
    content: str,
    memory_type: str = "note",
    scope: str = "private",
    title: Optional[str] = None,
    key: Optional[str] = None,
    tags: Optional[list] = None,
    importance: int = 50,
) -> int:
    if memory_type not in ALLOWED_TYPES:
        memory_type = "note"
    if scope not in ALLOWED_SCOPES:
        scope = "private"
    importance = max(0, min(100, importance))
    tags_json = json.dumps(tags or [])
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO agent_memories (tenant_id, agent_id, scope, memory_type,
                 key, title, content, tags, importance)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
               RETURNING id""",
            (tenant_id, agent_id, scope, memory_type, key, title, content,
             tags_json, importance),
        )
        mid = cur.fetchone()[0]
        conn.commit()
        return mid


def recall_for_agent(
    tenant_id: int,
    agent_id: int,
    query: Optional[str] = None,
    memory_types: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Recall mémoire pour un agent : ses mémoires privées + shared/team/global du tenant."""
    sql = """SELECT id, agent_id, scope, memory_type, title, content, tags, importance,
                    created_at
             FROM agent_memories
             WHERE tenant_id = %s
               AND (agent_id = %s OR scope IN ('shared', 'team', 'global'))
               AND (expires_at IS NULL OR expires_at > now())"""
    args: list = [tenant_id, agent_id]
    if memory_types:
        sql += " AND memory_type = ANY(%s)"
        args.append(memory_types)
    if query:
        sql += " AND (content ILIKE %s OR title ILIKE %s)"
        pattern = f"%{query}%"
        args.extend([pattern, pattern])
    sql += " ORDER BY importance DESC, created_at DESC LIMIT %s"
    args.append(limit)
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def search_memory(tenant_id: int, query: str, limit: int = 20) -> list[dict]:
    pattern = f"%{query}%"
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, scope, memory_type, title, content, importance, created_at
               FROM agent_memories
               WHERE tenant_id = %s AND (content ILIKE %s OR title ILIKE %s)
               ORDER BY importance DESC, created_at DESC LIMIT %s""",
            (tenant_id, pattern, pattern, limit),
        )
        return [dict(r) for r in cur.fetchall()]
