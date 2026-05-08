"""Connexion DB partagée + schéma idempotent pour bizzi.audience.

Engine UNIVERSEL — aucune table ni colonne sectorielle. Les catégories,
seuils et types de propositions sont entièrement portés par le YAML tenant.

Pattern aligné sur phone/_db.py, social/_db.py, data/_db.py.

pgvector n'est pas requis : si l'extension n'est pas dispo (vérifié au
boot), on bascule sur un embedding BYTEA (4 bytes/float, big-endian) —
même fallback que data.memory_vector. Le code détecte automatiquement
le passage à pgvector et utilise alors la colonne `vector(N)`.
"""
from __future__ import annotations

import os
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = dict(
    host="localhost",
    database="bizzi",
    user="bizzi_admin",
    password=os.environ.get("DB_PASSWORD", ""),
)

EMBED_DIM = 1536


def get_conn(dict_rows: bool = False):
    conn = psycopg2.connect(**DB_CONFIG)
    if dict_rows:
        conn.cursor_factory = RealDictCursor
    return conn


_pgvector_checked: bool | None = None


def pgvector_available() -> bool:
    global _pgvector_checked
    if _pgvector_checked is not None:
        return _pgvector_checked
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_extension WHERE extname='vector' "
                "UNION ALL SELECT 1 FROM pg_available_extensions WHERE name='vector'"
            )
            _pgvector_checked = cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        _pgvector_checked = False
    return _pgvector_checked


# ── DDL ──────────────────────────────────────────────────────────
# `categories` est TEXT[] : un message peut relever de 1 à 3 catégories
# du YAML tenant. `author_name` est le nom déclaré (ou anonymisé), et
# `author_external_id` un identifiant stable de la plateforme source
# (FB user id hashé, email hashé, session id chatbot, ticket id, ...).

_DDL_REPORTS_BYTEA = f"""
CREATE TABLE IF NOT EXISTS audience_reports (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INT  NOT NULL,
    source              TEXT NOT NULL,
    platform            TEXT,
    author_name         TEXT,
    author_external_id  TEXT,
    city                TEXT,
    org_unit_id         INT,
    raw_message         TEXT NOT NULL,
    cleaned_message     TEXT,
    categories          TEXT[] DEFAULT '{{}}',
    subcategory         TEXT,
    emotion             TEXT,
    keywords            TEXT[] DEFAULT '{{}}',
    priority_score      INT DEFAULT 0,
    language            TEXT,
    embedding           BYTEA,
    metadata            JSONB DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT now()
)
"""

_DDL_REPORTS_PGVECTOR = f"""
CREATE TABLE IF NOT EXISTS audience_reports (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INT  NOT NULL,
    source              TEXT NOT NULL,
    platform            TEXT,
    author_name         TEXT,
    author_external_id  TEXT,
    city                TEXT,
    org_unit_id         INT,
    raw_message         TEXT NOT NULL,
    cleaned_message     TEXT,
    categories          TEXT[] DEFAULT '{{}}',
    subcategory         TEXT,
    emotion             TEXT,
    keywords            TEXT[] DEFAULT '{{}}',
    priority_score      INT DEFAULT 0,
    language            TEXT,
    embedding           vector({EMBED_DIM}),
    metadata            JSONB DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT now()
)
"""

# Migration : ajoute org_unit_id si la table existait déjà sans ce champ.
_DDL_REPORTS_MIGRATE = [
    "ALTER TABLE audience_reports ADD COLUMN IF NOT EXISTS org_unit_id INT",
]

_DDL_TRENDS = """
CREATE TABLE IF NOT EXISTS audience_trends (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INT NOT NULL,
    category            TEXT NOT NULL,
    city                TEXT,
    total_mentions_24h  INT DEFAULT 0,
    total_mentions_7d   INT DEFAULT 0,
    total_mentions_30d  INT DEFAULT 0,
    trend_score         REAL DEFAULT 0,
    evolution_pct_7d    REAL DEFAULT 0,
    top_keywords        TEXT[] DEFAULT '{}',
    top_emotion         TEXT,
    last_updated        TIMESTAMPTZ DEFAULT now()
)
"""
# Unique partiel : (tenant_id, category, city) avec city pouvant être NULL.
# Postgres ne traite pas NULL comme une valeur unique en UNIQUE classique,
# d'où deux index uniques distincts.
_DDL_TRENDS_UNIQUE = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_audience_trends_with_city "
    "ON audience_trends(tenant_id, category, city) WHERE city IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_audience_trends_no_city "
    "ON audience_trends(tenant_id, category) WHERE city IS NULL",
]

_DDL_ALERTS = """
CREATE TABLE IF NOT EXISTS audience_alerts (
    id                          SERIAL PRIMARY KEY,
    tenant_id                   INT NOT NULL,
    alert_type                  TEXT NOT NULL,
    category                    TEXT,
    city                        TEXT,
    metric_value                REAL,
    threshold                   REAL,
    title                       TEXT NOT NULL,
    description                 TEXT,
    status                      TEXT DEFAULT 'pending',
    generated_content_proposals JSONB DEFAULT '[]'::jsonb,
    created_at                  TIMESTAMPTZ DEFAULT now(),
    updated_at                  TIMESTAMPTZ DEFAULT now()
)
"""

_DDL_EMBED_AUDIT = """
CREATE TABLE IF NOT EXISTS audience_embed_audit (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL,
    endpoint    TEXT NOT NULL,
    org_unit_id INT,
    role        TEXT,
    user_ref    TEXT,
    visible_units INT[] DEFAULT '{}',
    ip          INET,
    user_agent  TEXT,
    request_id  TEXT,
    status_code INT,
    created_at  TIMESTAMPTZ DEFAULT now()
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audience_reports_tenant_created ON audience_reports(tenant_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audience_reports_categories ON audience_reports USING gin (categories)",
    "CREATE INDEX IF NOT EXISTS idx_audience_reports_city ON audience_reports(tenant_id, city)",
    "CREATE INDEX IF NOT EXISTS idx_audience_reports_org_unit ON audience_reports(tenant_id, org_unit_id)",
    "CREATE INDEX IF NOT EXISTS idx_audience_reports_priority ON audience_reports(tenant_id, priority_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audience_trends_tenant ON audience_trends(tenant_id, last_updated DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audience_alerts_status ON audience_alerts(tenant_id, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audience_embed_audit_tenant_created ON audience_embed_audit(tenant_id, created_at DESC)",
]


_schema_ensured = False


def ensure_schema(force: bool = False) -> dict:
    """Crée les 3 tables audience si absentes. Idempotent.

    Retourne un dict de statut (pgvector actif ou non, embed_dim).
    """
    global _schema_ensured
    if _schema_ensured and not force:
        return {"already_ensured": True}

    use_vec = pgvector_available()
    with get_conn() as c, c.cursor() as cur:
        if use_vec:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception:  # noqa: BLE001
                use_vec = False
        cur.execute(_DDL_REPORTS_PGVECTOR if use_vec else _DDL_REPORTS_BYTEA)
        for ddl in _DDL_REPORTS_MIGRATE:
            cur.execute(ddl)
        cur.execute(_DDL_TRENDS)
        for ddl in _DDL_TRENDS_UNIQUE:
            cur.execute(ddl)
        cur.execute(_DDL_ALERTS)
        cur.execute(_DDL_EMBED_AUDIT)
        for ddl in _DDL_INDEXES:
            cur.execute(ddl)
        c.commit()

    _schema_ensured = True
    return {
        "tables": ["audience_reports", "audience_trends", "audience_alerts", "audience_embed_audit"],
        "pgvector": use_vec,
        "embed_dim": EMBED_DIM,
        "embedding_storage": "vector" if use_vec else "bytea",
    }
