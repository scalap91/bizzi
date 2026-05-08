"""bizzi.social.triggers — Mappe les évènements moteur Bizzi vers enqueue_post.

Lit la section `social.triggers` du yaml tenant et, sur évènement matché,
génère la vidéo + enfile un post (status='pending', shadow=true).

Exemple yaml :

    social:
      triggers:
        - event: article_published
          categories: [national, social]
          template: article_clip
          networks: [tiktok, instagram]
          caption: "{title} — {subtitle} #LesDemocrates"
          hashtags: [LesDemocrates]

Évènements supportés Phase 1 :
    article_published   — déclenché par api/routes/articles.py après INSERT
                          status='published'.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional

import httpx

from . import templates as _tpl
from .social_log import enqueue_post
from .video_generator import generate_video

logger = logging.getLogger("bizzi.social.triggers")

BG_CACHE_DIR = "/tmp"


# ── matching ──────────────────────────────────────────────────────────
def match_triggers(tenant_slug: str, event: str, ctx: dict) -> list[dict]:
    """Renvoie la liste des règles trigger qui matchent (event + filtres).

    Filtres supportés :
      - categories: list[str] — match si ctx['category'] ∈ list
      - regions:    list[str] — match si ctx['region']   ∈ list
      - min_score:  int       — match si ctx['score']    ≥ min_score
    """
    cfg = _tpl.load_tenant_social_config(tenant_slug)
    out = []
    for t in (cfg.get("triggers") or []):
        if t.get("event") != event:
            continue
        cats = t.get("categories")
        if cats and ctx.get("category") not in cats:
            continue
        regs = t.get("regions")
        if regs and ctx.get("region") not in regs:
            continue
        min_score = t.get("min_score")
        if min_score is not None and (ctx.get("score") or 0) < int(min_score):
            continue
        out.append(t)
    return out


# ── background image resolver ────────────────────────────────────────
async def _resolve_background(image_url: Optional[str]) -> Optional[str]:
    """Renvoie un chemin local utilisable par ffmpeg.

    - http(s) URL → download dans BG_CACHE_DIR (cache par hash url)
    - chemin local existant → renvoyé tel quel
    - sinon → None
    """
    if not image_url:
        return None
    if image_url.startswith(("http://", "https://")):
        h = hashlib.sha1(image_url.encode()).hexdigest()[:12]
        ext = Path(image_url.split("?")[0]).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            ext = ".jpg"
        local = Path(BG_CACHE_DIR) / f"social_bg_{h}{ext}"
        if local.exists() and local.stat().st_size > 0:
            return str(local)
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(image_url)
                r.raise_for_status()
                local.write_bytes(r.content)
                return str(local)
        except Exception as e:
            logger.warning(f"download {image_url} failed: {e}")
            return None
    p = Path(image_url)
    return str(p) if p.exists() else None


def _generate_fallback_bg(out_path: str, color: str = "#0a0a23") -> Optional[str]:
    """Génère un fond uni 1080x1920 si aucune image n'est disponible."""
    p = Path(out_path)
    if p.exists() and p.stat().st_size > 0:
        return str(p)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920",
             "-frames:v", "1", str(p)],
            capture_output=True,
        )
        if r.returncode != 0:
            logger.error(f"fallback bg ffmpeg failed: {r.stderr.decode()[-200:]}")
            return None
        return str(p)
    except FileNotFoundError:
        logger.error("ffmpeg not found — cannot generate fallback background")
        return None


def _safe_format(template: str, ctx: dict) -> str:
    try:
        return template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return template


# ── public API ────────────────────────────────────────────────────────
async def on_article_published(
    *,
    tenant_id: int,
    tenant_slug: str,
    article: dict,
) -> list[int]:
    """Hook après publication d'un article. Renvoie les post_ids enqueueés.

    article attendu : id, title, excerpt, category (slug), category_label,
                      region (slug), region_label, image_url, agent_id, slug.
    Le hook est best-effort : toute exception est loggée et avalée.
    """
    try:
        triggers = match_triggers(tenant_slug, "article_published", article)
        if not triggers:
            return []

        bg_path = await _resolve_background(article.get("image_url"))
        if not bg_path:
            bg_path = _generate_fallback_bg(f"{BG_CACHE_DIR}/social_bg_fallback_{tenant_slug}.jpg")
        if not bg_path:
            logger.error(f"no background image available for tenant={tenant_slug} article={article.get('id')}")
            return []

        post_ids: list[int] = []
        for trig in triggers:
            template_id = trig.get("template")
            if not template_id:
                continue
            tpl = _tpl.get_template(tenant_slug, template_id)
            if not tpl:
                logger.warning(f"tenant={tenant_slug} template={template_id!r} introuvable")
                continue

            ctx = {
                "title":          (article.get("title") or "")[:120],
                "subtitle":       (article.get("excerpt") or "")[:160],
                "category":       article.get("category") or "",
                "category_label": article.get("category_label") or "",
                "region":         article.get("region") or "",
                "region_label":   article.get("region_label") or "",
                "background_image": bg_path,
                "slug":           article.get("slug") or f"art{article.get('id')}",
            }

            try:
                video_path = generate_video(tpl, ctx)
            except Exception as e:
                logger.error(f"video_generator failed for article {article.get('id')} / "
                             f"template={template_id}: {e}")
                continue

            caption = _safe_format(trig.get("caption") or "{title}", ctx)
            hashtags = list(trig.get("hashtags") or [])
            networks = list(trig.get("networks") or [])

            post_id = enqueue_post(
                tenant_id=tenant_id,
                networks=networks,
                caption=caption,
                video_url=video_path,
                hashtags=hashtags,
                template_id=template_id,
                context={**ctx, "article_id": article.get("id"),
                         "trigger_event": "article_published"},
                agent_id=article.get("agent_id"),
                shadow=True,
                created_by="trigger:article_published",
            )
            post_ids.append(post_id)
            logger.info(
                f"tenant={tenant_slug} article={article.get('id')} → "
                f"post_id={post_id} template={template_id} networks={networks}"
            )
        return post_ids
    except Exception as e:
        logger.exception(f"on_article_published failed: {e}")
        return []


def fire_article_published(*, tenant_id: int, tenant_slug: str, article: dict) -> None:
    """Wrapper fire-and-forget : utilisable depuis du code sync ou async.

    - Si un event loop tourne déjà (cas FastAPI handler async) → schedule la coroutine.
    - Sinon → exécution synchrone (pour scripts CLI ou tests).

    Le hook lui-même est best-effort, donc cette fonction ne lève jamais.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(on_article_published(
            tenant_id=tenant_id, tenant_slug=tenant_slug, article=article))
    except RuntimeError:
        try:
            asyncio.run(on_article_published(
                tenant_id=tenant_id, tenant_slug=tenant_slug, article=article))
        except Exception as e:
            logger.exception(f"fire_article_published sync failed: {e}")
