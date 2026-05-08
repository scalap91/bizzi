"""Détection alertes priorité (Phase 1) + bridge vers tools/escalation.

Règles universelles paramétrées par YAML tenant :
- explosion       : evolution_pct_7d > threshold (default 30%)
- priority_keyword: message reçu avec priority_score >= 8
- anomaly         : trend_score s'écarte de baseline (Phase 2)

═══════════════════════════════════════════════════════════════════
Coordination Pascal — bridge `signal_critical` → escalation_engine
═══════════════════════════════════════════════════════════════════
`bizzi/tools/escalation/escalation_engine.py` (in-memory) RESTE en
production. Quand audience détecte une alerte de niveau critique, elle
publie un event `signal_critical` ; escalation_engine s'inscrit comme
listener et convertit l'event en `Signal` qu'il route via ses propres
seuils/scopes (commune/dept/région/national).

Phase 0 : registry d'event listeners en mémoire, signature figée.
Phase 1 : escalation_engine appellera `register_listener("signal_critical", handler)`
au boot et consommera les payloads.

Format event publié :
{
  "type":         "signal_critical" | "alert_explosion" | "alert_anomaly",
  "tenant_id":    int,
  "tenant_slug":  str,
  "alert_id":     int | None,
  "category":     str,
  "city":         str | None,
  "org_unit_id":  int | None,
  "priority":     int,           # 0..10
  "title":        str,
  "description":  str,
  "report_ids":   [int, ...],
  "created_at":   ISO8601,
}
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from psycopg2.extras import Json

from ._db import get_conn

logger = logging.getLogger(__name__)


# ── Event bus listener registry (audience → escalation, etc.) ────
EventHandler = Callable[[dict[str, Any]], None]
_LISTENERS: dict[str, list[EventHandler]] = {}


def register_listener(event_type: str, handler: EventHandler) -> None:
    """Enregistre un handler pour un type d'event.

    Utilisé par tools/escalation/escalation_engine pour consommer les
    `signal_critical` quand bizzi-audience démarre. Cohabite avec le
    bus interne event_bus.py (qui lui sert aux WebSockets UI).
    """
    _LISTENERS.setdefault(event_type, []).append(handler)


def unregister_listener(event_type: str, handler: EventHandler) -> None:
    if event_type in _LISTENERS:
        try:
            _LISTENERS[event_type].remove(handler)
        except ValueError:
            pass


def publish_event(event_type: str, payload: dict[str, Any]) -> int:
    """Diffuse l'event aux listeners enregistrés. Retourne le nb de listeners notifiés.

    Les handlers sont appelés synchronement (Phase 0) ; en Phase 1 si on
    monte une queue celery/redis, on publiera ici.
    """
    enriched = dict(payload)
    enriched.setdefault("type", event_type)
    enriched.setdefault("created_at", datetime.utcnow().isoformat() + "Z")

    handlers = list(_LISTENERS.get(event_type, []))
    for h in handlers:
        try:
            h(enriched)
        except Exception as e:  # noqa: BLE001
            logger.warning("audience.alerts listener %s failed: %s", event_type, e)
    return len(handlers)


# ── Persistence alertes ──────────────────────────────────────────
def create_alert(
    tenant_id: int,
    *,
    alert_type: str,
    title: str,
    description: str,
    category: Optional[str] = None,
    city: Optional[str] = None,
    metric_value: Optional[float] = None,
    threshold: Optional[float] = None,
    proposals: Optional[list[dict[str, Any]]] = None,
    publish: bool = True,
    org_unit_id: Optional[int] = None,
    report_ids: Optional[list[int]] = None,
    tenant_slug: Optional[str] = None,
) -> int:
    """Crée une alerte en DB et publie un event vers les listeners.

    `publish=True` : émet `signal_critical` si priority/threshold mérite escalade ;
    sinon `alert_explosion` ou `alert_anomaly` selon `alert_type`.
    """
    with get_conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO audience_alerts
               (tenant_id, alert_type, category, city, metric_value, threshold,
                title, description, generated_content_proposals)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (tenant_id, alert_type, category, city, metric_value, threshold,
             title, description, Json(proposals or [])),
        )
        new_id = int(cur.fetchone()[0])
        c.commit()

    if publish:
        ev_type = "signal_critical" if alert_type in {"explosion", "priority_keyword"} else f"alert_{alert_type}"
        publish_event(ev_type, {
            "tenant_id": tenant_id,
            "tenant_slug": tenant_slug,
            "alert_id": new_id,
            "category": category,
            "city": city,
            "org_unit_id": org_unit_id,
            "priority": int(round(metric_value)) if isinstance(metric_value, (int, float)) else None,
            "title": title,
            "description": description,
            "report_ids": list(report_ids or []),
        })
    return new_id


def detect_explosions(tenant_id: int, threshold_pct: float = 30.0) -> list[int]:
    """TODO Phase 1 : à brancher sur le scheduler après recompute_for_tenant."""
    return []
