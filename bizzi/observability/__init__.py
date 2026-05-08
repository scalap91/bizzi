import os
"""bizzi.observability — Logging usage par endpoint pour audit doublons et routes mortes.

Tables (créées via /tmp/bizzi_observability/migration.sql) :
  - module_usage_log         : log brut chaque request (rétention 90j)
  - module_usage_stats_30d   : view aggrégée 30j
  - routes_known             : routes déclarées (à populer si besoin)
  - dead_routes              : view jointure routes_known LEFT JOIN stats_30d

Usage :
    from bizzi.observability import UsageLoggerMiddleware
    app.add_middleware(UsageLoggerMiddleware, db_config={
        "host": "localhost", "database": "bizzi",
        "user": "bizzi_admin", "password": os.environ.get("DB_PASSWORD", ""),
    }, enabled=True)
"""
from .usage_logger import UsageLoggerMiddleware

__all__ = ["UsageLoggerMiddleware"]
