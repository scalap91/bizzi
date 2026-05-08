"""bizzi.social.scheduler — Tick worker pour publier les posts approuvés.

Phase 0 : skeleton uniquement. Le tick lit social_posts (status='approved' AND
shadow=false AND scheduled_at <= now()) et délègue au provider correspondant.
Le wiring cron par tenant viendra avec les credentials réels.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ._db import get_conn
from .publisher import PostProvider, PostRequest
from .social_log import attach_provider_post, update_status


def claim_due_posts(now: Optional[datetime] = None, limit: int = 10) -> list[dict]:
    """Sélectionne les posts approuvés et dus. Pas de SKIP LOCKED ici (Phase 0)."""
    now = now or datetime.utcnow()
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM social_posts
               WHERE status = 'approved' AND shadow = FALSE
                 AND (scheduled_at IS NULL OR scheduled_at <= %s)
               ORDER BY scheduled_at NULLS FIRST
               LIMIT %s""",
            (now, limit),
        )
        return [dict(r) for r in cur.fetchall()]


async def publish_due(providers: dict[str, PostProvider]) -> list[dict]:
    """Pour chaque post dû, déclenche les providers configurés. Retourne un résumé."""
    results = []
    for post in claim_due_posts():
        update_status(post["id"], "posting")
        post_results = []
        had_error = False
        for network in post["networks"]:
            provider = providers.get(network)
            if not provider:
                post_results.append({"network": network, "status": "failed",
                                     "error": f"No provider configured for {network}"})
                had_error = True
                continue
            req = PostRequest(
                tenant_id=post["tenant_id"],
                agent_id=post.get("agent_id"),
                networks=[network],
                caption=post.get("caption", ""),
                video_path=post.get("video_url"),
                hashtags=post.get("hashtags") or [],
            )
            try:
                res = await provider.publish(req)
                post_results.append(res.__dict__)
                if res.provider_post_id:
                    attach_provider_post(post["id"], network, res.provider_post_id, res.post_url)
                if res.status == "failed":
                    had_error = True
            except Exception as e:
                post_results.append({"network": network, "status": "failed", "error": str(e)})
                had_error = True
        update_status(post["id"], "failed" if had_error else "posted",
                      error="; ".join(r.get("error") for r in post_results if r.get("error")) or None)
        results.append({"post_id": post["id"], "results": post_results})
    return results
