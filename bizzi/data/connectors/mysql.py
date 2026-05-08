"""bizzi.data.connectors.mysql — Connecteur MySQL (stub Phase 0).

Implémentation complète prévue Phase 1 quand un tenant en aura besoin.
La structure miroite PostgresConnector pour faciliter le port.
"""
from __future__ import annotations

from typing import Any, Optional

from .base import (
    DataConnector, ConnectorError, EntityRef, ViewQuery, WriteResult,
    ConnectorScope,
)


class MySQLConnector(DataConnector):
    @property
    def supports_sql(self) -> bool:
        return True

    def __init__(self, source_config: dict[str, Any]):
        super().__init__(source_config)

    def _need_driver(self) -> ConnectorError:
        return ConnectorError(
            "MySQLConnector non implémenté en Phase 0. "
            "Ajouter PyMySQL/aiomysql + impl SELECT-only à la postgresql.py."
        )

    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        raise self._need_driver()

    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        raise self._need_driver()

    def write_record(
        self,
        entity: EntityRef,
        data: dict[str, Any],
        scope: ConnectorScope = ConnectorScope.READ_ONLY,
    ) -> WriteResult:
        raise self._need_driver()
