"""bizzi.data.connectors.google_sheets — Connecteur Google Sheets (stub Phase 0).

Phase 1 : intégrer gspread + service account JSON. Pour l'instant on lève
ConnectorError quand le tenant tente de l'utiliser, avec un message clair.
"""
from __future__ import annotations

from typing import Any, Optional

from .base import (
    DataConnector, ConnectorError, EntityRef, ViewQuery, WriteResult,
    ConnectorScope,
)


class GoogleSheetsConnector(DataConnector):
    """source_config attendu (Phase 1) :
        id:               "sheet_dossiers"
        type:             "google_sheets"
        scope:            "read_only"
        spreadsheet_id:   "1abcDEF..."
        service_account:  "env:GOOGLE_SA_JSON"   # JSON inline ou path fichier
        sheets:
          dossiers:
            tab:    "Dossiers"
            range:  "A1:Z"
            header_row: 1
    """

    def __init__(self, source_config: dict[str, Any]):
        super().__init__(source_config)
        self.spreadsheet_id = source_config.get("spreadsheet_id")

    def _stub(self) -> ConnectorError:
        return ConnectorError(
            "GoogleSheetsConnector non implémenté en Phase 0. "
            "Phase 1 : `pip install gspread google-auth` + impl read via "
            "spreadsheet_id + tab + range."
        )

    def read_entity(
        self,
        entity: EntityRef,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        raise self._stub()

    def query_view(self, query: ViewQuery) -> list[dict[str, Any]]:
        raise self._stub()

    def write_record(
        self,
        entity: EntityRef,
        data: dict[str, Any],
        scope: ConnectorScope = ConnectorScope.READ_ONLY,
    ) -> WriteResult:
        raise self._stub()
