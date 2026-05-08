"""bizzi.data.connectors — Implémentations des DataConnector par type de source.

Pattern : `get_connector(source_config) -> DataConnector` charge dynamiquement
l'implémentation selon `source_config["type"]`.
"""
from __future__ import annotations

from typing import Any

from .base import DataConnector, ConnectorError, ConnectorScope


def get_connector(source_config: dict[str, Any]) -> DataConnector:
    """Instancie le connecteur correspondant au type déclaré.

    source_config = {
      "id":       "main_db",
      "type":     "postgresql" | "mysql" | "rest_api" | "graphql"
                 | "google_sheets" | "airtable" | "bizzi_managed" | "webhook_pull",
      "scope":    "read_only" (default) | "read_write",
      ... params spécifiques au type
    }
    """
    src_type = source_config.get("type")
    if not src_type:
        raise ConnectorError("source_config.type manquant")

    if src_type == "postgresql":
        from .postgresql import PostgresConnector
        return PostgresConnector(source_config)

    if src_type == "mysql":
        from .mysql import MySQLConnector
        return MySQLConnector(source_config)

    if src_type == "rest_api":
        from .rest_api import RestAPIConnector
        return RestAPIConnector(source_config)

    if src_type == "graphql":
        from .graphql import GraphQLConnector
        return GraphQLConnector(source_config)

    if src_type == "google_sheets":
        from .google_sheets import GoogleSheetsConnector
        return GoogleSheetsConnector(source_config)

    if src_type == "bizzi_managed":
        from .bizzi_managed import BizziManagedConnector
        return BizziManagedConnector(source_config)

    if src_type == "airtable":
        from .airtable import AirtableConnector
        return AirtableConnector(source_config)

    raise ConnectorError(f"Type de source inconnu : {src_type!r}")


__all__ = ["get_connector", "DataConnector", "ConnectorError", "ConnectorScope"]
