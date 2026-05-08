"""bizzi.data.memory_vector — Memory RAG par tenant.

Stocke des éléments textuels (transcripts d'appels, emails, notes,
résumés de réunions…) avec embedding pgvector ; expose une recherche
sémantique top-k.

Conception Phase 0 :
  - Une table par tenant : `memory_<tenant_id>` (id, agent_id, kind, source_ref,
    text, embedding vector(1536), metadata jsonb, created_at).
  - L'extension pgvector est créée à la demande (CREATE EXTENSION IF NOT EXISTS).
  - Si pgvector n'est PAS disponible côté Postgres (pas le package OS), on
    bascule en fallback ILIKE plein-texte. La fonction memory_status() permet
    de détecter et de remonter l'état au caller.
  - Embedding : si OPENAI_API_KEY défini, on utilise text-embedding-3-small
    (1536 dim). Sinon fallback pseudo-embedding déterministe (hash) pour
    permettre le test e2e — clairement signalé par memory_status().
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor, Json

from ._db import DB_CONFIG  # même DB que phone/social — pattern existant


EMBED_DIM = 1536


# ── DB helpers ─────────────────────────────────────────────────
def _conn():
    return psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)


def _table_name(tenant_id: int) -> str:
    if not isinstance(tenant_id, int) or tenant_id < 0:
        raise ValueError(f"tenant_id invalide : {tenant_id!r}")
    return f"memory_{tenant_id}"


# ── pgvector availability ─────────────────────────────────────
_PG_VECTOR_AVAILABLE: Optional[bool] = None


def _check_pgvector() -> bool:
    """Vérifie si l'extension pgvector est installée OU installable.

    Effet secondaire : ne crée pas l'extension. Voir _ensure_table.
    """
    global _PG_VECTOR_AVAILABLE
    if _PG_VECTOR_AVAILABLE is not None:
        return _PG_VECTOR_AVAILABLE
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_extension WHERE extname='vector' "
                "UNION ALL SELECT 1 FROM pg_available_extensions WHERE name='vector'"
            )
            _PG_VECTOR_AVAILABLE = cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        _PG_VECTOR_AVAILABLE = False
    return _PG_VECTOR_AVAILABLE


def _ensure_table(tenant_id: int) -> bool:
    """Crée la table memoire si absente. Retourne True si pgvector actif."""
    table = _table_name(tenant_id)
    use_vec = _check_pgvector()

    with _conn() as c, c.cursor() as cur:
        if use_vec:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception:  # noqa: BLE001
                use_vec = False
        if use_vec:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id          SERIAL PRIMARY KEY,
                    agent_id    INT,
                    kind        TEXT,
                    source_ref  TEXT,
                    text        TEXT NOT NULL,
                    embedding   vector({EMBED_DIM}),
                    metadata    JSONB DEFAULT '{{}}'::jsonb,
                    created_at  TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_kind "
                f"ON {table}(kind)"
            )
            # ANN index : ivfflat coûte cher en build, on le crée seulement
            # si la table dépasse N rows (à vérifier lors d'un script ops).
        else:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id          SERIAL PRIMARY KEY,
                    agent_id    INT,
                    kind        TEXT,
                    source_ref  TEXT,
                    text        TEXT NOT NULL,
                    embedding   BYTEA,
                    metadata    JSONB DEFAULT '{{}}'::jsonb,
                    created_at  TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_text "
                f"ON {table} USING gin (to_tsvector('simple', text))"
            )
        c.commit()
    return use_vec


# ── Embedding ──────────────────────────────────────────────────
def _openai_embed(text: str) -> Optional[list[float]]:
    """Embedding via OpenAI si OPENAI_API_KEY présent. Sinon None."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import httpx
        with httpx.Client(timeout=15) as c:
            r = c.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "text-embedding-3-small", "input": text[:8000]},
            )
            r.raise_for_status()
            data = r.json()
        return data["data"][0]["embedding"]
    except Exception:  # noqa: BLE001
        return None


def _pseudo_embed(text: str) -> list[float]:
    """Pseudo-embedding déterministe pour fallback test (hash MD5 répété).

    NE PAS utiliser en prod : signalé par memory_status(). Utile uniquement
    pour valider les chemins d'écriture/lecture sans clé OpenAI.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    while len(out) < EMBED_DIM:
        for i in range(0, len(h), 4):
            v = struct.unpack(">I", h[i:i + 4])[0]
            out.append((v / 2**32) - 0.5)
            if len(out) >= EMBED_DIM:
                break
        h = hashlib.sha256(h).digest()
    return out[:EMBED_DIM]


def _embed(text: str) -> tuple[list[float], str]:
    """Retourne (vector, mode) où mode = 'openai' | 'pseudo'."""
    v = _openai_embed(text)
    if v is not None and len(v) == EMBED_DIM:
        return v, "openai"
    return _pseudo_embed(text), "pseudo"


def _vec_to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _vec_to_bytes(vec: list[float]) -> bytes:
    return b"".join(struct.pack(">f", x) for x in vec)


# ── Public API ─────────────────────────────────────────────────
def memory_status(tenant_id: int) -> dict[str, Any]:
    """État du backend memory pour un tenant."""
    pgvec = _check_pgvector()
    has_oai = bool(os.environ.get("OPENAI_API_KEY"))
    table = _table_name(tenant_id)
    table_exists = False
    row_count = 0
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_class WHERE relname=%s",
                (table,),
            )
            table_exists = cur.fetchone() is not None
            if table_exists:
                cur.execute(f"SELECT count(*) AS n FROM {table}")
                row_count = cur.fetchone()["n"]
    except Exception:  # noqa: BLE001
        pass
    return {
        "tenant_id":      tenant_id,
        "table":          table,
        "table_exists":   table_exists,
        "row_count":      row_count,
        "pgvector":       pgvec,
        "embed_provider": "openai" if has_oai else "pseudo (hash)",
        "embed_dim":      EMBED_DIM,
        "warning": (
            None if pgvec and has_oai else
            "memory en mode dégradé : "
            + ("pas de pgvector, " if not pgvec else "")
            + ("pas de OPENAI_API_KEY (embeddings pseudo)" if not has_oai else "")
        ),
    }


def memory_store(
    tenant_id: int,
    text: str,
    agent_id: Optional[int] = None,
    kind: str = "note",
    source_ref: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Stocke un élément texte + embedding. Retourne l'ID inséré."""
    if not text or not text.strip():
        raise ValueError("text vide")
    use_vec = _ensure_table(tenant_id)
    vec, _mode = _embed(text)
    table = _table_name(tenant_id)

    with _conn() as c, c.cursor() as cur:
        if use_vec:
            cur.execute(
                f"""INSERT INTO {table}
                    (agent_id, kind, source_ref, text, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s::vector, %s)
                    RETURNING id""",
                (agent_id, kind, source_ref, text,
                 _vec_to_pg(vec), Json(metadata or {})),
            )
        else:
            cur.execute(
                f"""INSERT INTO {table}
                    (agent_id, kind, source_ref, text, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id""",
                (agent_id, kind, source_ref, text,
                 psycopg2.Binary(_vec_to_bytes(vec)), Json(metadata or {})),
            )
        row = cur.fetchone()
        c.commit()
    return row["id"]


def memory_search(
    tenant_id: int,
    query: str,
    k: int = 5,
    kind: Optional[str] = None,
    agent_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Recherche top-k. En mode pgvector : similarité cosine. Sinon : ILIKE."""
    if not query or not query.strip():
        return []
    use_vec = _ensure_table(tenant_id)
    table = _table_name(tenant_id)

    extras_sql = []
    extras_p:   list[Any] = []
    if kind:
        extras_sql.append("kind = %s")
        extras_p.append(kind)
    if agent_id is not None:
        extras_sql.append("agent_id = %s")
        extras_p.append(agent_id)
    where_clause = (" WHERE " + " AND ".join(extras_sql)) if extras_sql else ""

    with _conn() as c, c.cursor() as cur:
        if use_vec:
            qvec, _mode = _embed(query)
            cur.execute(
                f"""SELECT id, agent_id, kind, source_ref, text, metadata,
                           created_at,
                           1 - (embedding <=> %s::vector) AS score
                    FROM {table}
                    {where_clause}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s""",
                [_vec_to_pg(qvec), *extras_p, _vec_to_pg(qvec), int(k)],
            )
        else:
            # Fallback : full-text ILIKE — ranking grossier par longueur du texte
            like = f"%{query}%"
            clauses = ["text ILIKE %s", *extras_sql]
            params: list[Any] = [like, like, *extras_p, int(k)]
            #                    ^score   ^WHERE      ^extras    ^limit
            cur.execute(
                f"""SELECT id, agent_id, kind, source_ref, text, metadata, created_at,
                           CASE WHEN text ILIKE %s THEN 1.0 ELSE 0.0 END AS score
                    FROM {table}
                    WHERE {' AND '.join(clauses)}
                    ORDER BY length(text) ASC
                    LIMIT %s""",
                params,
            )
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def memory_delete(tenant_id: int, memory_id: int) -> bool:
    table = _table_name(tenant_id)
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE id = %s", (memory_id,))
        c.commit()
        return cur.rowcount > 0
