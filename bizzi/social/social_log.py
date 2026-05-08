"""bizzi.social.social_log — CRUD social_posts (queue + logs).

Schéma DB : voir migrations/001_social_posts.sql (à appliquer manuellement).

Statuts :
  pending    — créé, en attente de validation Pascal (shadow mode)
  approved   — validé, prêt à publier
  posting    — en cours de publication via provider
  posted     — publié avec succès
  failed     — erreur provider
  rejected   — refusé par Pascal
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from ._db import get_conn

VALID_STATUSES = {"pending", "approved", "posting", "posted", "failed", "rejected"}


def enqueue_post(
    tenant_id: int,
    networks: list[str],
    caption: str,
    video_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    hashtags: Optional[list[str]] = None,
    template_id: Optional[str] = None,
    context: Optional[dict] = None,
    agent_id: Optional[int] = None,
    scheduled_at: Optional[datetime] = None,
    shadow: bool = True,
    created_by: Optional[str] = None,
) -> int:
    """Insère un post en queue. Retourne post_id."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO social_posts
                 (tenant_id, agent_id, networks, video_url, thumbnail_url, caption,
                  hashtags, template_id, context, status, shadow, scheduled_at, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id""",
            (
                tenant_id, agent_id, networks, video_url, thumbnail_url, caption,
                hashtags or [], template_id, json.dumps(context or {}),
                "pending", shadow, scheduled_at, created_by,
            ),
        )
        post_id = cur.fetchone()[0]
        conn.commit()
        return post_id


def get_post(post_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM social_posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_pending(tenant_id: int, limit: int = 50) -> list[dict]:
    """Posts en attente de validation (shadow mode)."""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, networks, video_url, caption, hashtags, template_id,
                      scheduled_at, created_at, created_by
               FROM social_posts
               WHERE tenant_id = %s AND status = 'pending'
               ORDER BY scheduled_at NULLS LAST, created_at DESC
               LIMIT %s""",
            (tenant_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_agent_posts(agent_id: int, limit: int = 50) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, tenant_id, networks, video_url, caption, status, posted_at,
                      views, likes, comments, shares, post_urls, created_at
               FROM social_posts
               WHERE agent_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (agent_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_tenant_posts(
    tenant_id: int,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    sql = (
        "SELECT id, agent_id, networks, video_url, caption, status, posted_at, "
        "views, likes, post_urls, created_at FROM social_posts WHERE tenant_id = %s"
    )
    args: list[Any] = [tenant_id]
    if status:
        sql += " AND status = %s"
        args.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    args.append(limit)
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args))
        return [dict(r) for r in cur.fetchall()]


def get_calendar(
    tenant_id: int,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict]:
    """Posts planifiés ou publiés dans [from_dt, to_dt]."""
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, agent_id, networks, caption, status,
                      COALESCE(posted_at, scheduled_at) AS at, views, likes
               FROM social_posts
               WHERE tenant_id = %s
                 AND COALESCE(posted_at, scheduled_at) BETWEEN %s AND %s
               ORDER BY at""",
            (tenant_id, from_dt, to_dt),
        )
        return [dict(r) for r in cur.fetchall()]


def update_status(
    post_id: int,
    status: str,
    error: Optional[str] = None,
    approved_by: Optional[str] = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Statut invalide : {status}")
    with get_conn() as conn, conn.cursor() as cur:
        if status == "approved":
            cur.execute(
                """UPDATE social_posts
                     SET status=%s, approved_by=%s, approved_at=now(), updated_at=now()
                   WHERE id=%s""",
                (status, approved_by, post_id),
            )
        elif status == "posted":
            cur.execute(
                """UPDATE social_posts
                     SET status=%s, posted_at=now(), error=NULL, updated_at=now()
                   WHERE id=%s""",
                (status, post_id),
            )
        elif status == "failed":
            cur.execute(
                "UPDATE social_posts SET status=%s, error=%s, updated_at=now() WHERE id=%s",
                (status, error, post_id),
            )
        else:
            cur.execute(
                "UPDATE social_posts SET status=%s, updated_at=now() WHERE id=%s",
                (status, post_id),
            )
        conn.commit()


def attach_provider_post(
    post_id: int,
    network: str,
    provider_post_id: str,
    post_url: Optional[str] = None,
) -> None:
    """Enregistre l'ID retourné par le provider (par network)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE social_posts
                 SET provider_post_ids = COALESCE(provider_post_ids, '{}'::jsonb)
                                          || jsonb_build_object(%s, %s),
                     post_urls         = COALESCE(post_urls, '{}'::jsonb)
                                          || jsonb_build_object(%s, %s),
                     updated_at = now()
               WHERE id = %s""",
            (network, provider_post_id, network, post_url or "", post_id),
        )
        conn.commit()


def update_metrics(
    post_id: int,
    views: Optional[int] = None,
    likes: Optional[int] = None,
    comments: Optional[int] = None,
    shares: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    sets, args = [], []
    if views is not None:
        sets.append("views=%s"); args.append(views)
    if likes is not None:
        sets.append("likes=%s"); args.append(likes)
    if comments is not None:
        sets.append("comments=%s"); args.append(comments)
    if shares is not None:
        sets.append("shares=%s"); args.append(shares)
    if extra:
        sets.append("metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb")
        args.append(json.dumps(extra))
    if not sets:
        return
    sets.append("updated_at=now()")
    args.append(post_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE social_posts SET {', '.join(sets)} WHERE id = %s", tuple(args))
        conn.commit()
