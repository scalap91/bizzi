"""bizzi.data.integrations.audience — Bridge audience ↔ data.

Le module bizzi.audience a son propre event_bus in-memory + ring buffer
pour le live feed WebSocket. Cette intégration ajoute une persistance
cross-module : tout report.created peut être miroitée dans data_events
pour permettre à phone, social et autres handlers de réagir.

Direction principale : audience → data (audience est la source canonique
des signaux d'opinion).

Pour activer le bridge, appeler `enable_audience_bridge()` au boot.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .. import events, views


logger = logging.getLogger("bizzi.data.integrations.audience")


def report_to_event_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Extrait les champs pertinents d'un audience_report pour un event payload."""
    return {
        "report_id":      report.get("id"),
        "source":         report.get("source"),
        "platform":       report.get("platform"),
        "city":           report.get("city"),
        "categories":     report.get("categories") or [],
        "subcategory":    report.get("subcategory"),
        "emotion":        report.get("emotion"),
        "priority_score": report.get("priority_score") or 0,
        "language":       report.get("language"),
        "raw_message":    (report.get("raw_message") or "")[:500],
        # On ne pousse PAS le message complet ni les PII auteur
        # — l'agent peut requêter audience_reports si besoin via une view.
    }


def publish_audience_report(tenant_id: int, report: dict[str, Any]) -> dict[str, Any]:
    """Helper : un caller (ex: audience.storage) peut appeler ceci après
    insert_report() pour relayer dans le bus data.

    Cette fonction NE patche PAS audience — c'est à l'appelant de l'invoquer.
    """
    return events.publish(
        tenant_id=tenant_id,
        kind="audience.report.created",
        payload=report_to_event_payload(report),
        source_module="audience",
        correlation_id=f"audience_report:{report.get('id')}",
    )


def publish_audience_alert(tenant_id: int, alert: dict[str, Any]) -> dict[str, Any]:
    """Idem pour les alertes audience (sujet qui explose, etc.)."""
    return events.publish(
        tenant_id=tenant_id,
        kind="audience.alert.raised",
        payload={
            "alert_id":     alert.get("id"),
            "alert_type":   alert.get("alert_type"),
            "category":     alert.get("category"),
            "city":         alert.get("city"),
            "metric_value": alert.get("metric_value"),
            "threshold":    alert.get("threshold"),
            "title":        alert.get("title"),
            "description":  alert.get("description"),
        },
        source_module="audience",
        correlation_id=f"audience_alert:{alert.get('id')}",
    )


def enable_audience_bridge(tenant_id: Optional[int] = None) -> int:
    """Active le bridge in-memory : enregistre un handler data.events qui
    miroite vers audience.event_bus (l'inverse de _bridge automatique de
    publish() qui passe data → audience).

    Cette fonction est utile si on veut afficher TOUS les data_events
    (call.completed, social.post.published, …) dans le live feed du
    command center, pas seulement les audience reports.

    Retourne le nb de handlers enregistrés (1 par défaut).
    """
    try:
        from ...audience import event_bus as _audience_bus
    except Exception as e:  # noqa: BLE001
        logger.warning("audience.event_bus indisponible : %s", e)
        return 0

    def _mirror(ev: dict[str, Any]) -> dict[str, Any]:
        try:
            _audience_bus.publish(ev["tenant_id"], {
                "type": f"data.{ev['kind']}",
                "data": ev.get("payload") or {},
                "event_id": ev.get("id"),
            })
            return {"mirrored": True}
        except Exception as e:  # noqa: BLE001
            return {"mirrored": False, "error": str(e)}

    events.subscribe(_mirror, tenant_id=tenant_id, kind=None)
    return 1


def get_top_categories(tenant_slug: str, tenant_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
    """Helper agent : renvoie les top catégories d'opinion pour ce tenant.

    Dépend d'une semantic_view 'audience_top_categories' — fallback : query
    directe audience_trends si la view n'est pas déclarée.
    """
    try:
        return views.execute_view(
            tenant_slug, "audience_top_categories",
            {"tenant_id": tenant_id, "limit": limit},
        )
    except (ValueError, FileNotFoundError):
        # Fallback : on lit audience_trends directement.
        from .._db import get_conn
        from psycopg2.extras import RealDictCursor
        with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT category, total_mentions_24h, total_mentions_7d,
                          top_emotion, last_updated
                   FROM audience_trends
                   WHERE tenant_id = %s AND city IS NULL
                   ORDER BY total_mentions_24h DESC LIMIT %s""",
                (tenant_id, int(limit)),
            )
            return [dict(r) for r in cur.fetchall()]
