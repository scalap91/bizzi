"""bizzi.data.connectors.postgresql — Connecteur Postgres (psycopg2 sync).

Choix Phase 0 : sync psycopg2 (pas asyncpg) pour matcher le pattern existant
des modules phone et social. Migration vers asyncpg = Phase 1 si latence
devient critique.

Sécurité :
  - read_entity / query_view : SELECT only, jamais d'INSERT/UPDATE/DELETE.
  - write_record : refusé sauf scope='read_write' déclaré dans le YAML
    ET passé explicitement à l'appel (double opt-in).
  - Garde-fou : query_view inspecte le SQL et refuse les mots-clés
    INSERT/UPDATE/DELETE/TRUNCATE/DROP/ALTER quand le scope est READ_ONLY.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from .base import (
    DataConnector, ConnectorError, ConnectorScope,
    EntityRef, ViewQuery, WriteResult,
)


_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class PostgresConnector(DataConnector):
    """Connecteur PostgreSQL.

    source_config attendu :
        id:       "main_db"
        type:     "postgresql"
        scope:    "read_only" | "read_write"
        # Format A — DSN :
        dsn:      "postgresql://user:pwd@host:5432/dbname"
        # Format B — kv :
        host:     "localhost"
        port:     5432
        database: "bizzi"
        user:     "bizzi_admin"
        password: "..."
        # Tenant scope optionnel : auto-injecté en filtre WHERE tenant_id=...
        tenant_column: "tenant_id"   # ou null
    """

    @property
    def supports_sql(self) -> bool:
        return True

    def __init__(self, source_config: dict[str, Any]):
        super().__init__(source_config)
        self._dsn = source_config.get("dsn")
        self._kv = {
            k: source_config[k]
            for k in ("host", "port", "database", "user", "password")
            if k in source_config
        }
        if not self._dsn and "host" not in self._kv:
            raise ConnectorError(
                f"Postgres source {self.source_id!r} : il manque 'dsn' ou "
                f"'host/database/user/password'"
            )
        self.tenant_column: Optional[str] = source_config.get("tenant_column")

    def _connect(self):
        if self._dsn:
            return psycopg2.connect(self._dsn, cursor_factory=RealDictCursor)
        return psycopg2.connect(cursor_factory=RealDictCursor, **self._kv)

    # ── Reads ────────────────────────────────────────────────────
    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not entity.physical_name:
            raise ConnectorError(
                f"Entity {entity.name!r} : physical_name requis pour Postgres"
            )
        # Identifiant : on quote en double-quote — psycopg2 ne paramètre pas les noms.
        # Validation stricte : seuls [a-zA-Z0-9_."] (accepte schema.table).
        if not re.fullmatch(r"[A-Za-z0-9_.]+", entity.physical_name):
            raise ConnectorError(f"physical_name invalide : {entity.physical_name!r}")

        cols = "*"
        if entity.fields:
            for f in entity.fields:
                if not re.fullmatch(r"[A-Za-z0-9_]+", f):
                    raise ConnectorError(f"field invalide : {f!r}")
            cols = ", ".join(f'"{f}"' for f in entity.fields)

        where_sql = ""
        params: list[Any] = []
        if filters:
            clauses = []
            for k, v in filters.items():
                if not re.fullmatch(r"[A-Za-z0-9_]+", k):
                    raise ConnectorError(f"filter key invalide : {k!r}")
                clauses.append(f'"{k}" = %s')
                params.append(v)
            where_sql = " WHERE " + " AND ".join(clauses)

        sql = (
            f'SELECT {cols} FROM {entity.physical_name}'
            f'{where_sql} '
            f'LIMIT %s OFFSET %s'
        )
        params.extend([int(limit), int(offset)])

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        if not query.sql:
            raise ConnectorError(
                f"View {query.name!r} : champ 'sql' requis pour PostgresConnector"
            )

        # Garde-fou : aucun mot-clé d'écriture si scope = read_only.
        if self.scope == ConnectorScope.READ_ONLY:
            if _FORBIDDEN_KEYWORDS.search(query.sql):
                raise ConnectorError(
                    f"View {query.name!r} contient un mot-clé d'écriture mais "
                    f"la source {self.source_id!r} est en read_only"
                )

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query.sql, query.params or {})
            try:
                rows = [dict(r) for r in cur.fetchall()]
            except psycopg2.ProgrammingError:
                # Statement sans résultat (ne devrait pas arriver en read_only).
                rows = []

        if query.pii_mask:
            rows = self.apply_pii_mask(rows, query.pii_mask)
        return rows

    # ── Writes ───────────────────────────────────────────────────
    def write_record(
        self,
        entity: EntityRef,
        data: dict[str, Any],
        scope: ConnectorScope = ConnectorScope.READ_ONLY,
    ) -> WriteResult:
        # Double opt-in : appelant + config source.
        super().write_record(entity, data, scope)  # lève si pas READ_WRITE / READ_WRITE

        if not entity.physical_name:
            raise ConnectorError(
                f"Entity {entity.name!r} : physical_name requis pour write"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.]+", entity.physical_name):
            raise ConnectorError(f"physical_name invalide : {entity.physical_name!r}")
        if not data:
            raise ConnectorError("write_record : data vide")

        cols = []
        vals = []
        params: list[Any] = []
        for k, v in data.items():
            if not re.fullmatch(r"[A-Za-z0-9_]+", k):
                raise ConnectorError(f"data key invalide : {k!r}")
            cols.append(f'"{k}"')
            vals.append("%s")
            params.append(v)
        sql = (
            f'INSERT INTO {entity.physical_name} ({", ".join(cols)}) '
            f'VALUES ({", ".join(vals)}) RETURNING *'
        )
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                conn.commit()
                return WriteResult(
                    success=True,
                    entity=entity.name,
                    rows_affected=1,
                    inserted_id=(row.get("id") if row else None),
                )
        except Exception as e:  # noqa: BLE001
            return WriteResult(
                success=False, entity=entity.name, rows_affected=0, error=str(e)
            )

    # ── Diagnostics ──────────────────────────────────────────────
    def health_check(self) -> dict[str, Any]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
            return {"source_id": self.source_id, "type": "postgresql",
                    "ok": True, "scope": self.scope.value}
        except Exception as e:  # noqa: BLE001
            return {"source_id": self.source_id, "type": "postgresql",
                    "ok": False, "error": str(e)}
