"""bizzi.data.integrations.social — Helpers data pour module bizzi.social.

Cas d'usage :
  1. Avant publication, le module social peut transformer une
     semantic_view en context dict directement utilisable par un
     template (lesdemocrates_article, airbizness_deal, ...).

  2. Après publication, indexer le post en memory_vector pour analytics
     rétroactives ("quels posts ont marché ces 30 derniers jours sur le
     thème écologie ?").

  3. Publier l'event 'social.post.published' pour permettre aux autres
     modules de réagir (ex: audience écoute → bump trend, data écoute →
     log structuré).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .. import events, memory_vector, views


logger = logging.getLogger("bizzi.data.integrations.social")


def view_to_template_context(
    tenant_slug: str,
    view_name: str,
    params: Optional[dict[str, Any]] = None,
    *,
    field_map: Optional[dict[str, str]] = None,
    pick_first: bool = True,
) -> Optional[dict[str, Any]]:
    """Exécute une semantic_view et formate la première ligne pour un template.

    `field_map` permet de renommer les champs pour matcher les variables
    attendues par un template, ex:
        field_map = {"topic": "title", "category": "subtitle"}

    Retourne None si la vue ne renvoie aucune ligne.
    """
    rows = views.execute_view(tenant_slug, view_name, params or {})
    if not rows:
        return None
    if not pick_first and len(rows) > 1:
        # Phase 1 : pick_first=False est réservé aux carrousels — on retourne
        # une liste de contexts. Pour template uni-ligne on utilise la 1ère.
        return [_apply_field_map(r, field_map) for r in rows]
    return _apply_field_map(rows[0], field_map)


def _apply_field_map(
    row: dict[str, Any],
    field_map: Optional[dict[str, str]],
) -> dict[str, Any]:
    if not field_map:
        return dict(row)
    out = dict(row)
    for src, dst in field_map.items():
        if src in row:
            out[dst] = row[src]
    return out


def index_post_published(
    tenant_id: int,
    post_id: int,
    *,
    networks: list[str],
    caption: str,
    template_id: Optional[str] = None,
    article_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Indexe un post publié dans memory_vector kind='social_post'."""
    md = dict(metadata or {})
    md.update({
        "post_id":     post_id,
        "networks":    networks,
        "template_id": template_id,
        "article_id":  article_id,
    })
    return memory_vector.memory_store(
        tenant_id=tenant_id,
        text=caption,
        kind="social_post",
        source_ref=f"social_post:{post_id}",
        metadata=md,
    )


def publish_post_event(
    tenant_id: int,
    kind: str,
    *,
    post_id: int,
    networks: list[str],
    caption: Optional[str] = None,
    article_id: Optional[int] = None,
    template_id: Optional[str] = None,
) -> dict[str, Any]:
    """Publie un event social.post.* (created|approved|published|failed)."""
    if not kind.startswith("social.post."):
        kind = f"social.post.{kind}"
    return events.publish(
        tenant_id=tenant_id,
        kind=kind,
        payload={
            "post_id":     post_id,
            "networks":    networks,
            "caption":     caption,
            "article_id":  article_id,
            "template_id": template_id,
        },
        source_module="social",
        correlation_id=f"social_post:{post_id}",
    )
