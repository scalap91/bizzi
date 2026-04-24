"""
api/routes/content.py
======================
API contenu ONYX — lit depuis la table productions (tenant_id=1)
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import create_engine, text
import logging

router = APIRouter()
logger = logging.getLogger("api.content")

_db = create_engine("postgresql://bizzi_admin:Bizzi2026x@localhost/bizzi")

ONYX_TENANT_ID = 1
ONYX_BASE_URL  = "https://onyx-infos.fr"


def _build_og_html(title, content, image_url, slug):
    title = (title or "").replace('"', "'")
    desc  = content.replace("\n", " ").replace("#", "")[:160].replace('"', "'")
    img   = image_url or ""
    if img.startswith("/"):
        img = ONYX_BASE_URL + img
    url = f"{ONYX_BASE_URL}/article/{slug}"
    return f"""<html><head>
<meta charset="UTF-8">
<title>{title} — Onyx Infos</title>
<meta name="description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{img}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{img}">
<meta http-equiv="refresh" content="0;url={url}">
</head><body><a href="{url}">{title}</a></body></html>"""


@router.get("/list")
async def list_articles(limit: int = 20, category: str = None, region: str = None):
    try:
        with _db.connect() as conn:
            rows = conn.execute(text("""
                SELECT p.title, p.content_html, p.created_at, p.status,
                       p.image_url, p.slug, p.meta_description
                FROM productions p
                WHERE p.tenant_id = :tid
                  AND p.status = 'published'
                  AND p.slug IS NOT NULL
                ORDER BY p.created_at DESC
                LIMIT :lim
            """), {"tid": ONYX_TENANT_ID, "lim": limit}).fetchall()
            return [{
                "title":      r[0] or "",
                "content":    r[1] or "",
                "created_at": str(r[2]),
                "status":     r[3] or "",
                "image_url":  (r[4] or "").replace("/img/", f"{ONYX_BASE_URL}/img/"),
                "slug":       r[5] or "",
                "excerpt":    r[6] or "",
                "category":   "",
                "region":     "",
            } for r in rows]
    except Exception as e:
        logger.error(f"[LIST] {e}")
        raise HTTPException(500, str(e))


@router.get("/article-by-slug")
async def article_by_slug(slug: str = ""):
    try:
        with _db.connect() as conn:
            row = conn.execute(text("""
                SELECT title, content_html, image_url
                FROM productions
                WHERE tenant_id = :tid AND slug = :slug LIMIT 1
            """), {"tid": ONYX_TENANT_ID, "slug": slug}).fetchone()
            if not row:
                return RedirectResponse(ONYX_BASE_URL)
            return HTMLResponse(_build_og_html(row[0], row[1] or "", row[2] or "", slug))
    except Exception as e:
        return RedirectResponse(ONYX_BASE_URL)


@router.get("/article-meta")
async def article_meta(id: int = 0):
    try:
        with _db.connect() as conn:
            rows = conn.execute(text("""
                SELECT title, content_html, image_url, slug
                FROM productions
                WHERE tenant_id = :tid AND status = 'published'
                ORDER BY created_at DESC LIMIT 200
            """), {"tid": ONYX_TENANT_ID}).fetchall()
            if id >= len(rows):
                return RedirectResponse(ONYX_BASE_URL)
            r = rows[id]
            return HTMLResponse(_build_og_html(r[0], r[1] or "", r[2] or "", r[3] or ""))
    except Exception as e:
        return RedirectResponse(ONYX_BASE_URL)


@router.get("/og-home")
async def og_home():
    return HTMLResponse(f"""<html><head>
<meta charset="UTF-8">
<title>Onyx Infos — L'actualité en continu</title>
<meta property="og:type" content="website">
<meta property="og:title" content="Onyx Infos">
<meta property="og:description" content="Toute l'actualité française et internationale.">
<meta property="og:url" content="{ONYX_BASE_URL}">
<meta http-equiv="refresh" content="0;url={ONYX_BASE_URL}">
</head><body><a href="{ONYX_BASE_URL}">Onyx Infos</a></body></html>""")


@router.get("/sitemap.xml")
async def sitemap():
    try:
        with _db.connect() as conn:
            rows = conn.execute(text("""
                SELECT slug, created_at FROM productions
                WHERE tenant_id = :tid AND status = 'published'
                  AND slug IS NOT NULL AND slug != ''
                ORDER BY created_at DESC LIMIT 5000
            """), {"tid": ONYX_TENANT_ID}).fetchall()
        urls = [
            f"<url><loc>{ONYX_BASE_URL}/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>",
            f"<url><loc>{ONYX_BASE_URL}/onyx-archives.html</loc><changefreq>hourly</changefreq><priority>0.8</priority></url>",
            f"<url><loc>{ONYX_BASE_URL}/regions.html</loc><changefreq>weekly</changefreq><priority>0.6</priority></url>",
        ]
        for r in rows:
            urls.append(f"<url><loc>{ONYX_BASE_URL}/article/{r[0]}</loc><lastmod>{str(r[1])[:10]}</lastmod><changefreq>never</changefreq><priority>0.7</priority></url>")
        xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(urls) + "</urlset>"
        return Response(content=xml, media_type="application/xml")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/regions")
async def list_regions():
    return {"regions": [
        {"name": "Île-de-France", "slug": "ile-de-france"},
        {"name": "PACA", "slug": "paca"},
        {"name": "Bretagne", "slug": "bretagne"},
        {"name": "Occitanie", "slug": "occitanie"},
        {"name": "Grand-Est", "slug": "grand-est"},
        {"name": "Nouvelle-Aquitaine", "slug": "nouvelle-aquitaine"},
        {"name": "Pays-de-la-Loire", "slug": "pays-de-la-loire"},
        {"name": "Hauts-de-France", "slug": "hauts-de-france"},
        {"name": "DOM-TOM", "slug": "dom-tom"},
    ]}


@router.get("/region/{region_slug}")
async def get_region_articles(region_slug: str, limit: int = 20):
    try:
        with _db.connect() as conn:
            rows = conn.execute(text("""
                SELECT p.title, p.content_html, p.created_at, p.image_url, p.slug
                FROM productions p
                JOIN regions r ON r.id = p.region_id
                WHERE p.tenant_id = :tid AND p.status = 'published'
                  AND r.slug = :reg
                ORDER BY p.created_at DESC LIMIT :lim
            """), {"tid": ONYX_TENANT_ID, "reg": region_slug, "lim": limit}).fetchall()
        return [{"title": r[0] or "", "content": r[1] or "", "created_at": str(r[2]),
                 "image_url": (r[3] or "").replace("/img/", f"{ONYX_BASE_URL}/img/"),
                 "slug": r[4] or "", "region": region_slug} for r in rows]
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/stats")
async def stats():
    try:
        with _db.connect() as conn:
            r = conn.execute(text("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE status = 'published'),
                       COUNT(*) FILTER (WHERE status = 'draft'),
                       COUNT(*) FILTER (WHERE status = 'editor_review'),
                       COUNT(*) FILTER (WHERE status = 'rejected'),
                       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24h')
                FROM productions WHERE tenant_id = :tid
            """), {"tid": ONYX_TENANT_ID}).fetchone()
        return {"total": r[0], "published": r[1], "draft": r[2],
                "in_review": r[3], "rejected": r[4], "last_24h": r[5]}
    except Exception as e:
        raise HTTPException(500, str(e))
