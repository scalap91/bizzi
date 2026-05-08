"""bizzi.observability.usage_logger — middleware FastAPI pour logger l'usage de chaque endpoint.

Objectif :
- Identifier les endpoints jamais appelés (candidats à archive après 90j)
- Détecter les doublons d'usage (2 endpoints qui font le même travail)
- Stats agrégées top routes / routes mortes

Performance : insertion non-bloquante (fire-and-forget via asyncio.create_task)
qui exécute le SQL via asyncio.to_thread. Aucune dépendance asyncpg ajoutée :
on réutilise le pattern psycopg2 sync du reste du projet (phone, social, data,
audience). Coût d'1 insert : ~1-3ms, et le caller voit duration_ms
DÉJÀ inclus l'overhead de l'INSERT (mais l'insert s'exécute APRÈS la
réponse — fire-and-forget).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import psycopg2

logger = logging.getLogger(__name__)

# Routes à NE PAS logger (statiques, healthchecks, openapi)
_SKIP_PATHS = {"/", "/openapi.json", "/docs", "/redoc", "/health", "/ping",
               "/favicon.ico", "/api/status"}
_SKIP_PREFIXES = ("/static/", "/assets/")


class UsageLoggerMiddleware(BaseHTTPMiddleware):
    """Logge chaque request dans la table module_usage_log.

    Args:
        app:        l'app FastAPI (passé par add_middleware)
        db_config:  dict psycopg2-style {host, database, user, password}
                    OU callable() -> connection (laissé pour compat).
                    Le pool n'est pas géré ici — chaque insert ouvre/ferme
                    une connection. À ~50-100 req/s c'est OK ; au-delà,
                    introduire un pool (psycopg2.pool.ThreadedConnectionPool).
        enabled:    désactive complètement le middleware (no-op total).
    """

    def __init__(
        self,
        app,
        db_config: Optional[dict[str, Any]] = None,
        enabled: bool = True,
        # Compat : si l'appelant passe `db_pool=...` (asyncpg-style),
        # on l'ignore avec un warning et on tombe sur db_config.
        db_pool: Any = None,
    ):
        super().__init__(app)
        self.enabled = enabled
        if db_pool is not None and db_config is None:
            logger.warning(
                "UsageLoggerMiddleware: paramètre db_pool reçu mais asyncpg "
                "n'est pas utilisé dans ce projet. Passe db_config={host,...} "
                "à la place. Logger désactivé jusqu'à correction."
            )
            self.enabled = False
        self.db_config = db_config or {}

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.enabled:
            return await call_next(request)

        start = time.perf_counter()
        path = request.url.path
        method = request.method

        # Ne pas logger les statiques/health
        if path in _SKIP_PATHS or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        tenant_id = self._extract_tenant_id(request)

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            module = self._infer_module(path)
            # Fire-and-forget : on ne bloque PAS la réponse.
            try:
                asyncio.create_task(self._log_async(
                    module, path, method, status_code, tenant_id, duration_ms,
                ))
            except RuntimeError:
                # Pas d'event loop : on skip plutôt que crash.
                pass

    def _infer_module(self, path: str) -> str:
        """Infère le module depuis le path. Ex: /api/phone/calls → 'phone'."""
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "api":
            return parts[1]  # phone, social, audience, comms, org, data, tools, etc.
        if len(parts) >= 2 and parts[0] == "embed":
            return f"embed/{parts[1]}"
        if len(parts) >= 1 and parts[0] == "iframe":
            return "iframe"
        return "other"

    def _extract_tenant_id(self, request: Request) -> Optional[int]:
        """Extrait tenant_id depuis header X-Tenant-ID, query, ou request.state.scope."""
        tid = request.headers.get("X-Tenant-ID")
        if tid:
            try:
                return int(tid)
            except ValueError:
                pass
        tid = request.query_params.get("tenant_id")
        if tid:
            try:
                return int(tid)
            except ValueError:
                pass
        # Fallback : récupérer depuis JWT scope si dispo
        if hasattr(request.state, "scope") and request.state.scope:
            return getattr(request.state.scope, "tenant_id", None)
        return None

    async def _log_async(
        self,
        module: str,
        endpoint: str,
        method: str,
        status_code: int,
        tenant_id: Optional[int],
        duration_ms: float,
    ) -> None:
        """Insert non-bloquant via asyncio.to_thread + psycopg2 sync."""
        if not self.db_config:
            return
        try:
            await asyncio.to_thread(
                self._log_sync,
                module, endpoint, method, status_code, tenant_id, duration_ms,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("usage_logger insert failed: %s", e)

    def _log_sync(
        self,
        module: str,
        endpoint: str,
        method: str,
        status_code: int,
        tenant_id: Optional[int],
        duration_ms: float,
    ) -> None:
        with psycopg2.connect(**self.db_config) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO module_usage_log
                   (module, endpoint, method, status_code, tenant_id, duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (module, endpoint, method, status_code, tenant_id, duration_ms),
            )
            conn.commit()
