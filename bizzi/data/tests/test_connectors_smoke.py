"""Smoke tests bizzi.data — Phase 0.

Lancer :
    /opt/bizzi/bizzi/venv/bin/python -m data.tests.test_connectors_smoke

Tests no-deps (pas pytest requis).  Couvre :
  - PostgresConnector : connexion, read_entity, query_view, garde-fou scope
  - semantic.load_data_config : parsing YAML lesdemocrates.yaml
  - views.execute_view : exécution d'une view sur la DB bizzi
  - memory_vector : memory_status (ne nécessite pas pgvector pour passer)
"""
from __future__ import annotations

import os
import sys
import traceback

# Bootstrap PYTHONPATH si lancé en script direct.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

from data.connectors.base import EntityRef, ViewQuery, ConnectorError, ConnectorScope  # noqa: E402
from data.connectors.postgresql import PostgresConnector  # noqa: E402
from data import memory_vector, semantic, views  # noqa: E402


_PASS = 0
_FAIL = 0
_FAILS: list[str] = []


def _assert(cond, label: str):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {label}")
    else:
        _FAIL += 1
        _FAILS.append(label)
        print(f"  ✗ {label}")


def _run(name: str, fn):
    print(f"\n— {name} —")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        global _FAIL
        _FAIL += 1
        _FAILS.append(f"{name}: {e}")
        print(f"  ✗ EXCEPTION: {e}")
        traceback.print_exc()


# ── Tests ─────────────────────────────────────────────────────
def test_postgres_health():
    cfg = {
        "id": "main_db", "type": "postgresql", "scope": "read_only",
        "host": "localhost", "database": "bizzi",
        "user": "bizzi_admin", "password": os.environ.get("DB_PASSWORD", ""),
    }
    c = PostgresConnector(cfg)
    h = c.health_check()
    _assert(h["ok"] is True, "postgres health_check ok")
    _assert(h["scope"] == "read_only", "scope read_only par défaut")


def test_postgres_read_entity():
    cfg = {
        "id": "main_db", "type": "postgresql", "scope": "read_only",
        "host": "localhost", "database": "bizzi",
        "user": "bizzi_admin", "password": os.environ.get("DB_PASSWORD", ""),
    }
    c = PostgresConnector(cfg)
    rows = c.read_entity(
        EntityRef(name="bizzi_article", physical_name="bizzi_articles",
                  fields=["id", "tenant", "topic"]),
        filters={"tenant": "onyx-infos"}, limit=3,
    )
    _assert(isinstance(rows, list), "read_entity renvoie une list")
    _assert(len(rows) <= 3, "respect du limit=3")
    if rows:
        _assert("id" in rows[0] and "tenant" in rows[0],
                "fields demandés présents dans le résultat")


def test_postgres_scope_guard():
    cfg = {
        "id": "main_db", "type": "postgresql", "scope": "read_only",
        "host": "localhost", "database": "bizzi",
        "user": "bizzi_admin", "password": os.environ.get("DB_PASSWORD", ""),
    }
    c = PostgresConnector(cfg)
    raised = False
    try:
        c.query_view(ViewQuery(
            name="evil",
            sql="DELETE FROM bizzi_articles WHERE id=0",
        ))
    except ConnectorError:
        raised = True
    _assert(raised, "DELETE refusé en scope read_only")

    raised2 = False
    try:
        c.write_record(
            EntityRef(name="x", physical_name="bizzi_articles"),
            {"topic": "test"},
            scope=ConnectorScope.READ_WRITE,
        )
    except ConnectorError:
        raised2 = True
    _assert(raised2, "write_record refusé sur source read_only")


def test_invalid_identifier():
    cfg = {
        "id": "main_db", "type": "postgresql", "scope": "read_only",
        "host": "localhost", "database": "bizzi",
        "user": "bizzi_admin", "password": os.environ.get("DB_PASSWORD", ""),
    }
    c = PostgresConnector(cfg)
    raised = False
    try:
        c.read_entity(
            EntityRef(name="x", physical_name="bizzi_articles; DROP TABLE foo"),
        )
    except ConnectorError:
        raised = True
    _assert(raised, "physical_name avec injection SQL rejeté")


def test_semantic_load():
    schema = semantic.load_data_config("lesdemocrates", force_reload=True)
    _assert(schema.tenant_slug == "lesdemocrates", "schema chargé")
    _assert(len(schema.sources) >= 1, "au moins une data_source déclarée")
    _assert("articles_recent" in schema.views, "vue 'articles_recent' présente")


def test_views_execute():
    rows = views.execute_view(
        "lesdemocrates", "articles_recent",
        {"tenant_slug": "onyx-infos", "limit": 5},
    )
    _assert(isinstance(rows, list), "execute_view renvoie list")
    _assert(len(rows) <= 5, "limit=5 respecté")


def test_memory_status():
    st = memory_vector.memory_status(tenant_id=4)
    _assert(isinstance(st, dict), "memory_status dict")
    _assert("pgvector" in st, "champ pgvector présent")
    _assert("embed_provider" in st, "champ embed_provider présent")


# ── Main ──────────────────────────────────────────────────────
def main() -> int:
    print("== Smoke tests bizzi.data — Phase 0 ==")
    _run("postgres_health", test_postgres_health)
    _run("postgres_read_entity", test_postgres_read_entity)
    _run("postgres_scope_guard", test_postgres_scope_guard)
    _run("invalid_identifier", test_invalid_identifier)
    _run("semantic_load", test_semantic_load)
    _run("views_execute", test_views_execute)
    _run("memory_status", test_memory_status)

    print(f"\n== Résultat : {_PASS} OK / {_FAIL} KO ==")
    if _FAILS:
        print("Échecs :")
        for f in _FAILS:
            print(f"  - {f}")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
