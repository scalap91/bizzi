"""Recalcul périodique des tendances (cron 1h, Phase 1).

Phase 0 : `storage.insert_report` met à jour des compteurs incrémentaux
24h/7d/30d sans décroissance. Ce fichier portera la vraie logique de
fenêtre glissante quand on branchera le scheduler.

API exposée :
- recompute_for_tenant(tenant_id) : recalcule les compteurs avec une vraie
  fenêtre temporelle SQL et l'évolution_pct_7d.
"""
from __future__ import annotations

import logging
from typing import Any

from ._db import get_conn

logger = logging.getLogger(__name__)


_SQL_AGG_NO_CITY = """
INSERT INTO audience_trends (tenant_id, category, city,
                             total_mentions_24h, total_mentions_7d,
                             total_mentions_30d, top_emotion, last_updated)
SELECT %s, category, NULL, c24, c7, c30, top_emotion, now()
FROM (
    SELECT
        unnest(categories) AS category,
        count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS c24,
        count(*) FILTER (WHERE created_at > now() - interval '7 days')   AS c7,
        count(*) FILTER (WHERE created_at > now() - interval '30 days')  AS c30,
        mode() WITHIN GROUP (ORDER BY emotion) AS top_emotion
    FROM audience_reports
    WHERE tenant_id = %s
    GROUP BY 1
) agg
ON CONFLICT (tenant_id, category) WHERE city IS NULL DO UPDATE SET
    total_mentions_24h = EXCLUDED.total_mentions_24h,
    total_mentions_7d  = EXCLUDED.total_mentions_7d,
    total_mentions_30d = EXCLUDED.total_mentions_30d,
    top_emotion        = EXCLUDED.top_emotion,
    last_updated       = now()
"""

_SQL_AGG_WITH_CITY = """
INSERT INTO audience_trends (tenant_id, category, city,
                             total_mentions_24h, total_mentions_7d,
                             total_mentions_30d, top_emotion, last_updated)
SELECT %s, category, city, c24, c7, c30, top_emotion, now()
FROM (
    SELECT
        unnest(categories) AS category,
        city,
        count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS c24,
        count(*) FILTER (WHERE created_at > now() - interval '7 days')   AS c7,
        count(*) FILTER (WHERE created_at > now() - interval '30 days')  AS c30,
        mode() WITHIN GROUP (ORDER BY emotion) AS top_emotion
    FROM audience_reports
    WHERE tenant_id = %s AND city IS NOT NULL
    GROUP BY 1, 2
) agg
ON CONFLICT (tenant_id, category, city) WHERE city IS NOT NULL DO UPDATE SET
    total_mentions_24h = EXCLUDED.total_mentions_24h,
    total_mentions_7d  = EXCLUDED.total_mentions_7d,
    total_mentions_30d = EXCLUDED.total_mentions_30d,
    top_emotion        = EXCLUDED.top_emotion,
    last_updated       = now()
"""


def recompute_for_tenant(tenant_id: int) -> dict[str, Any]:
    """Recalcule audience_trends pour un tenant (Phase 0 : suffisant pour dashboard)."""
    with get_conn() as c, c.cursor() as cur:
        cur.execute(_SQL_AGG_NO_CITY, (tenant_id, tenant_id))
        cur.execute(_SQL_AGG_WITH_CITY, (tenant_id, tenant_id))
        c.commit()
    return {"tenant_id": tenant_id, "ok": True}
