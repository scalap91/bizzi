"""tenant_db/postgres.py — provider PostgreSQL via psycopg2.

Sécurité :
  - exécute uniquement des queries déclarées dans le YAML tenant (pas de SQL libre)
  - params passés en bind parameters (psycopg2 gère l'échappement)
  - LIMIT injecté automatiquement si la query retourne 'rows' et n'en a pas déjà un
"""
from __future__ import annotations
import logging
import re
from typing import Any
import psycopg2
import psycopg2.extras

from .base import TenantConfig, TenantDBProvider, QueryDef

logger = logging.getLogger("tenant_db.postgres")

_LIMIT_RE = re.compile(r"\blimit\s+\d+", re.IGNORECASE)


def _serialize(value: Any) -> Any:
    """Rend une valeur JSON-sérialisable (datetime/Decimal → str/float)."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return str(value)


class PostgresProvider(TenantDBProvider):
    def __init__(self, config: TenantConfig):
        super().__init__(config)
        self._conn = None

    def _conn_get(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.config.db_dsn)
            self._conn.set_session(readonly=False, autocommit=True)
        return self._conn

    def execute(self, query_name: str, params: dict[str, Any]) -> dict[str, Any]:
        qdef = self.config.queries.get(query_name)
        if not qdef:
            return {"error": f"query '{query_name}' not declared for tenant '{self.config.slug}'"}

        # check params
        missing = [p for p in qdef.params if p not in params]
        if missing:
            return {"error": f"missing params: {missing}"}
        bound = {p: params[p] for p in qdef.params}

        sql = qdef.sql
        if qdef.returns == "rows" and not _LIMIT_RE.search(sql):
            sql = f"{sql.rstrip(';')} LIMIT {qdef.max_rows}"

        try:
            conn = self._conn_get()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, bound)
                if qdef.returns == "rows":
                    rows = cur.fetchall()
                    return {"rows": [_serialize(dict(r)) for r in rows], "count": len(rows)}
                if qdef.returns == "row":
                    row = cur.fetchone()
                    return {"row": _serialize(dict(row)) if row else None}
                if qdef.returns == "scalar":
                    row = cur.fetchone()
                    if not row:
                        return {"value": None}
                    val = list(row.values())[0]
                    return {"value": _serialize(val)}
                if qdef.returns == "count":
                    return {"count": cur.rowcount}
                return {"error": f"unknown returns type: {qdef.returns}"}
        except Exception as e:
            logger.exception(f"[{self.config.slug}] query '{query_name}' failed")
            return {"error": f"db error: {type(e).__name__}: {e}"}

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
        self._conn = None


PROVIDERS = {"postgres": PostgresProvider}
