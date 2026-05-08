"""bizzi.social.routes — Endpoints FastAPI /api/social/*.

Phase 0 : SHADOW MODE uniquement. Aucune publication réelle.
Les endpoints enqueue dans social_posts (status='pending') puis Pascal valide.

Wiring dans api/main.py : pas encore — à ajouter avec validation Pascal.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import social_log, templates
from .video_generator import generate_video

router = APIRouter()


class PostBody(BaseModel):
    tenant_id: int
    networks: list[str] = Field(..., description="tiktok|instagram|x|linkedin")
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    template_id: Optional[str] = None
    template_context: dict = Field(default_factory=dict)
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    agent_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    tenant_slug: Optional[str] = None  # pour résoudre template via templates.py


class ApproveBody(BaseModel):
    approved_by: str


class MetricsBody(BaseModel):
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    extra: Optional[dict] = None


@router.post("/post")
def post_create(body: PostBody):
    """Enqueue un post. Si template_id + template_context fournis, génère la vidéo
    localement (ffmpeg) et stocke le chemin dans video_url."""
    video_url = body.video_url
    if body.template_id and body.tenant_slug and not video_url:
        tpl = templates.get_template(body.tenant_slug, body.template_id)
        if not tpl:
            raise HTTPException(404, f"Template inconnu : {body.template_id}")
        try:
            video_url = generate_video(tpl, body.template_context)
        except Exception as e:
            raise HTTPException(500, f"video_generator failed: {e}")

    shadow = templates.is_shadow_mode(body.tenant_slug) if body.tenant_slug else True
    post_id = social_log.enqueue_post(
        tenant_id=body.tenant_id,
        networks=body.networks,
        caption=body.caption,
        video_url=video_url,
        thumbnail_url=body.thumbnail_url,
        hashtags=body.hashtags,
        template_id=body.template_id,
        context=body.template_context,
        agent_id=body.agent_id,
        scheduled_at=body.scheduled_at,
        shadow=shadow,
    )
    return {"post_id": post_id, "status": "pending", "shadow": shadow, "video_url": video_url}


@router.get("/pending")
def pending(tenant_id: int, limit: int = 50):
    return {"items": social_log.get_pending(tenant_id, limit=limit)}


@router.get("/agent/{agent_id}/posts")
def agent_posts(agent_id: int, limit: int = 50):
    return {"items": social_log.get_agent_posts(agent_id, limit=limit)}


@router.get("/tenant/{tenant_id}/posts")
def tenant_posts(tenant_id: int, status: Optional[str] = None, limit: int = 100):
    return {"items": social_log.get_tenant_posts(tenant_id, status=status, limit=limit)}


@router.get("/calendar")
def calendar(tenant_id: int, from_dt: datetime, to_dt: datetime):
    return {"items": social_log.get_calendar(tenant_id, from_dt, to_dt)}


@router.post("/post/{post_id}/approve")
def approve(post_id: int, body: ApproveBody):
    if not social_log.get_post(post_id):
        raise HTTPException(404, "Post not found")
    social_log.update_status(post_id, "approved", approved_by=body.approved_by)
    return {"post_id": post_id, "status": "approved"}


@router.post("/post/{post_id}/reject")
def reject(post_id: int, body: ApproveBody):
    if not social_log.get_post(post_id):
        raise HTTPException(404, "Post not found")
    social_log.update_status(post_id, "rejected", approved_by=body.approved_by)
    return {"post_id": post_id, "status": "rejected"}


@router.post("/post/{post_id}/metrics")
def metrics(post_id: int, body: MetricsBody):
    social_log.update_metrics(
        post_id, views=body.views, likes=body.likes,
        comments=body.comments, shares=body.shares, extra=body.extra,
    )
    return {"post_id": post_id, "ok": True}


@router.get("/templates/{tenant_slug}")
def list_templates(tenant_slug: str):
    return {
        "tenant": tenant_slug,
        "shadow_mode": templates.is_shadow_mode(tenant_slug),
        "networks": templates.get_tenant_networks(tenant_slug),
        "templates": templates.list_tenant_templates(tenant_slug),
    }
