"""bizzi.data — Module DATA / CONNECTORS du moteur Bizzi.

Découple les agents IA (phone, social, pipeline éditorial) des données métier
de chaque tenant. Chaque tenant peut brancher SES sources (Postgres, MySQL,
REST API, GraphQL, Google Sheets, Airtable, DB managée Bizzi, webhook pull)
et déclarer dans son YAML :

  - data_sources       → connecteurs vers ses systèmes
  - semantic_schema    → entités, fields, types, relations (compréhension)
  - semantic_views     → requêtes prédéfinies réutilisables
  - events             → publication d'events qui déclenchent des actions
  - memory             → RAG pgvector par tenant (transcripts, emails, notes)

Lecture seule par défaut. Toute écriture nécessite scope='read_write' explicite.

Endpoints REST : voir bizzi.data.routes (préfixe /api/data, à wirer manuellement
dans api/main.py après validation Pascal).
"""
from .connectors.base import (
    DataConnector, ConnectorScope, ConnectorError,
    EntityRef, ViewQuery, WriteResult,
)
from .semantic import (
    SemanticSchema, SemanticEntity, SemanticField, SemanticView,
    DataSourceConfig, load_data_config,
)
from .views import execute_view, list_views
from .memory_vector import (
    memory_search, memory_store, memory_status,
)
from .events import (
    publish as event_publish,
    subscribe as event_subscribe,
    list_events,
    list_kinds,
    process_event,
    replay_pending,
    configure_from_yaml as configure_events_from_yaml,
)

__all__ = [
    # connectors
    "DataConnector", "ConnectorScope", "ConnectorError",
    "EntityRef", "ViewQuery", "WriteResult",
    # semantic
    "SemanticSchema", "SemanticEntity", "SemanticField", "SemanticView",
    "DataSourceConfig", "load_data_config",
    # views
    "execute_view", "list_views",
    # memory
    "memory_search", "memory_store", "memory_status",
    # events
    "event_publish", "event_subscribe",
    "list_events", "list_kinds", "process_event", "replay_pending",
    "configure_events_from_yaml",
]
