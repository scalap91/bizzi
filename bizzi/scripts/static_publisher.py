#!/usr/bin/env python3
"""
static_publisher.py — Génère un fallback HTML statique JAMstack pour un tenant
éditorial Bizzi (Onyx, Lesdemocrates, Lediagnostiqueur, ...).

But : si bizzi-api / Postgres tombe, nginx peut servir ces fichiers via
error_page 5xx → /articles/<slug>.html, donc le site reste lisible.

Pattern multi-tenant générique : paramétrable via --tenant <slug>.

Usage :
    python static_publisher.py --tenant onyx --output /opt/onyx-infos/public --once
    python static_publisher.py --tenant onyx --output /opt/onyx-infos/public --loop --interval 300

Idempotent : ne réécrit un fichier que si son hash a changé.
Atomique  : écrit dans .tmp puis renomme.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text


# ── Templates ─────────────────────────────────────────────────────────────

ARTICLE_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc} — {site_name}</title>
<meta name="description" content="{meta_description_esc}">
<meta name="robots" content="index, follow">
<meta property="og:type" content="article">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{meta_description_esc}">
<meta property="og:image" content="{image_url_esc}">
<meta property="og:url" content="{canonical}">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="{canonical}">
<script type="application/ld+json">{jsonld}</script>
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  body{{margin:0;font-family:Georgia,'Times New Roman',serif;color:#1a1a1a;background:#fafafa;line-height:1.65}}
  header.site{{background:#111;color:#fff;padding:14px 24px}}
  header.site a{{color:#fff;text-decoration:none;font-weight:700;font-size:1.05em}}
  .banner{{background:#fff3cd;color:#664d03;text-align:center;padding:8px 16px;font-size:0.9em;border-bottom:1px solid #ffe69c}}
  main{{max-width:760px;margin:0 auto;padding:32px 20px;background:#fff}}
  h1{{font-size:2em;margin:0 0 12px;line-height:1.2}}
  .meta{{color:#666;font-size:0.92em;margin-bottom:24px}}
  .hero{{width:100%;height:auto;border-radius:6px;margin-bottom:24px}}
  .content p{{margin:0 0 16px}}
  .content h2{{margin-top:32px;font-size:1.4em}}
  footer.site{{text-align:center;padding:32px 16px;color:#888;font-size:0.85em;border-top:1px solid #eee;margin-top:48px}}
  footer.site a{{color:#555}}
</style>
</head>
<body>
<header class="site"><a href="/">← {site_name}</a></header>
<div class="banner">Mode lecture allégée — version statique pré-générée</div>
<main>
<article>
  <h1>{title_esc}</h1>
  <div class="meta">{published_human}</div>
  {hero_block}
  <div class="content">{content_html}</div>
</article>
</main>
<footer class="site">
  <p>{site_name} — <a href="/">Retour à l'accueil</a></p>
  <p style="font-size:0.78em;color:#aaa">Page statique générée le {generated_at} UTC</p>
</footer>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{site_name} — Derniers articles</title>
<meta name="description" content="Les derniers articles publiés sur {site_name}.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{site_url}/">
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1a1a;background:#f4f4f4}}
  header.site{{background:#111;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}}
  header.site h1{{font-size:1.2em;margin:0}}
  header.site a{{color:#fff;text-decoration:none}}
  .banner{{background:#fff3cd;color:#664d03;text-align:center;padding:8px 16px;font-size:0.9em;border-bottom:1px solid #ffe69c}}
  main{{max-width:1180px;margin:0 auto;padding:28px 20px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}}
  .card{{background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);transition:transform .15s,box-shadow .15s}}
  .card:hover{{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.12)}}
  .card a{{color:inherit;text-decoration:none;display:block;height:100%}}
  .card img{{width:100%;height:180px;object-fit:cover;display:block;background:#eee}}
  .card .body{{padding:14px 16px}}
  .card h2{{font-size:1.05em;margin:0 0 8px;line-height:1.3}}
  .card p{{font-size:0.9em;color:#555;margin:0;line-height:1.45}}
  footer.site{{text-align:center;padding:32px 16px;color:#888;font-size:0.85em;margin-top:32px}}
</style>
</head>
<body>
<header class="site">
  <h1><a href="/">{site_name}</a></h1>
  <div style="font-size:0.85em">Derniers articles</div>
</header>
<div class="banner">Mode lecture allégée — version statique pré-générée</div>
<main>
  <div class="grid">
    {cards}
  </div>
</main>
<footer class="site">
  <p>{site_name} — version statique</p>
  <p style="font-size:0.78em;color:#aaa">Page générée le {generated_at} UTC</p>
</footer>
</body>
</html>
"""

CARD_TEMPLATE = """<article class="card"><a href="/article/{slug}">
  <img src="{image_url}" alt="{image_alt}" loading="lazy" onerror="this.style.background='#ddd';this.removeAttribute('src')">
  <div class="body">
    <h2>{title}</h2>
    <p>{excerpt}</p>
  </div>
</a></article>"""


# ── Helpers ───────────────────────────────────────────────────────────────

def esc(value: str | None) -> str:
    """HTML-escape une valeur potentiellement None."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def safe_excerpt(value: str | None, n: int = 160) -> str:
    if not value:
        return ""
    txt = " ".join(str(value).split())
    if len(txt) > n:
        txt = txt[: n - 1].rstrip() + "…"
    return txt


def build_jsonld(article: dict, site_name: str, site_url: str) -> str:
    canonical = f"{site_url}/article/{article['slug']}"
    pub = article.get("published_at")
    pub_iso = pub.isoformat() if pub else None
    payload = {
        "@context": "https://schema.org",
        "@type": article.get("schema_type") or "NewsArticle",
        "headline": article.get("title") or "",
        "description": article.get("meta_description") or article.get("excerpt") or "",
        "image": article.get("image_url") or "",
        "datePublished": pub_iso,
        "dateModified": pub_iso,
        "url": canonical,
        "mainEntityOfPage": canonical,
        "publisher": {
            "@type": "Organization",
            "name": site_name,
            "url": site_url,
        },
    }
    # JSON-encoded + on escape les </ pour éviter le break du <script>
    return json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")


def render_article(article: dict, *, site_name: str, site_url: str) -> str:
    title = article.get("title") or "(sans titre)"
    pub = article.get("published_at")
    published_human = pub.strftime("%d %B %Y") if pub else ""
    image_url = article.get("image_url") or ""
    image_alt = article.get("image_alt") or title
    hero_block = (
        f'<img class="hero" src="{esc(image_url)}" alt="{esc(image_alt)}">'
        if image_url
        else ""
    )
    # content_html : déjà du HTML stocké en DB → on garde tel quel, sinon fallback
    content_html = article.get("content_html") or (
        "<p>"
        + esc(article.get("content_raw") or "Contenu indisponible.").replace(
            "\n\n", "</p><p>"
        )
        + "</p>"
    )
    canonical = f"{site_url}/article/{article['slug']}"
    # generated_at = updated_at de l'article (stable -> idempotent)
    upd = article.get("updated_at") or pub
    generated_at = upd.strftime("%Y-%m-%d %H:%M") if upd else ""
    return ARTICLE_TEMPLATE.format(
        title_esc=esc(title),
        site_name=esc(site_name),
        meta_description_esc=esc(
            article.get("meta_description") or article.get("excerpt") or title
        ),
        image_url_esc=esc(image_url),
        canonical=esc(canonical),
        jsonld=build_jsonld(article, site_name, site_url),
        published_human=esc(published_human),
        hero_block=hero_block,
        content_html=content_html,
        generated_at=esc(generated_at),
    )


def render_index(articles: list[dict], *, site_name: str, site_url: str) -> str:
    cards = "\n    ".join(
        CARD_TEMPLATE.format(
            slug=esc(a["slug"]),
            image_url=esc(a.get("image_url") or "/onyx-logo.svg"),
            image_alt=esc(a.get("image_alt") or a.get("title") or ""),
            title=esc(a.get("title") or "(sans titre)"),
            excerpt=esc(safe_excerpt(a.get("excerpt") or a.get("meta_description"))),
        )
        for a in articles
        if a.get("slug")
    )
    # generated_at = updated_at du plus recent (stable -> idempotent)
    most_recent = None
    for a in articles:
        u = a.get("updated_at") or a.get("published_at")
        if u and (most_recent is None or u > most_recent):
            most_recent = u
    generated_at = most_recent.strftime("%Y-%m-%d %H:%M") if most_recent else ""
    return INDEX_TEMPLATE.format(
        site_name=esc(site_name),
        site_url=esc(site_url),
        cards=cards,
        generated_at=esc(generated_at),
    )


def write_atomic(path: Path, content: str) -> bool:
    """
    Écrit content dans path de façon atomique. Retourne True si le fichier a
    changé (write effectif), False si idempotent skip.
    """
    new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
            if hashlib.sha256(existing.encode("utf-8")).hexdigest() == new_hash:
                return False
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


# ── DB ────────────────────────────────────────────────────────────────────

TENANT_PROFILES = {
    "onyx": {"site_name": "Onyx Infos", "site_url": "https://onyx-infos.fr"},
    "lesdemocrates": {
        "site_name": "Les Démocrates",
        "site_url": "https://lesdemocrates.fr",
    },
    "lediagnostiqueur": {
        "site_name": "Le Diagnostiqueur",
        "site_url": "https://lediagnostiqueur.fr",
    },
}


def fetch_tenant(engine, tenant_slug: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, slug, name, COALESCE(site_url,'') AS site_url "
                "FROM tenants WHERE slug = :s LIMIT 1"
            ),
            {"s": tenant_slug},
        ).mappings().first()
    if not row:
        raise SystemExit(f"Tenant introuvable: {tenant_slug!r}")
    profile = TENANT_PROFILES.get(tenant_slug, {})
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": profile.get("site_name") or row["name"],
        "site_url": profile.get("site_url") or row["site_url"] or f"https://{tenant_slug}.fr",
    }


def fetch_articles(engine, tenant_id: int, n: int) -> list[dict]:
    sql = text(
        """
        SELECT id, slug, title, excerpt, content_html, content_raw,
               image_url, image_alt, meta_title, meta_description,
               canonical_url, schema_type, published_at, updated_at
        FROM productions
        WHERE tenant_id = :tid
          AND status = 'published'
          AND slug IS NOT NULL AND slug <> ''
        ORDER BY COALESCE(published_at, updated_at, created_at) DESC NULLS LAST
        LIMIT :n
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"tid": tenant_id, "n": n}).mappings().all()
    return [dict(r) for r in rows]


# ── Pipeline ──────────────────────────────────────────────────────────────

def publish_static(
    *,
    engine,
    tenant_slug: str,
    output_dir: Path,
    n_recent: int,
    log: logging.Logger,
) -> dict:
    t0 = time.time()
    tenant = fetch_tenant(engine, tenant_slug)
    articles = fetch_articles(engine, tenant["id"], n_recent)

    articles_dir = output_dir / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    errors = 0
    for a in articles:
        try:
            html_out = render_article(
                a, site_name=tenant["name"], site_url=tenant["site_url"]
            )
            target = articles_dir / f"{a['slug']}.html"
            if write_atomic(target, html_out):
                written += 1
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            log.warning("article %s rendu KO: %s", a.get("slug"), e)

    # Index
    index_html = render_index(
        articles, site_name=tenant["name"], site_url=tenant["site_url"]
    )
    index_changed = write_atomic(output_dir / "index-static.html", index_html)

    elapsed = time.time() - t0
    stats = {
        "tenant": tenant_slug,
        "tenant_id": tenant["id"],
        "articles_total": len(articles),
        "articles_written": written,
        "articles_skipped": skipped,
        "articles_errors": errors,
        "index_changed": index_changed,
        "elapsed_s": round(elapsed, 2),
    }
    log.info("publish_static OK %s", stats)
    return stats


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Générateur de fallback HTML statique multi-tenant")
    p.add_argument("--tenant", required=True, help="slug du tenant (onyx, lesdemocrates, ...)")
    p.add_argument("--output", required=True, help="répertoire web racine (ex: /opt/onyx-infos/public)")
    p.add_argument("--n", type=int, default=50, help="nombre d'articles récents (défaut 50)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="exécution unique")
    g.add_argument("--loop", action="store_true", help="boucle infinie (interval)")
    p.add_argument("--interval", type=int, default=300, help="secondes entre runs en mode --loop")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("static_publisher")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL absent de l'env")
        return 2

    engine = create_engine(db_url, pool_pre_ping=True)
    output_dir = Path(args.output)

    def run_once() -> int:
        try:
            publish_static(
                engine=engine,
                tenant_slug=args.tenant,
                output_dir=output_dir,
                n_recent=args.n,
                log=log,
            )
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("publish_static FAILED: %s", e)
            return 1

    if args.once:
        return run_once()

    # loop
    log.info("loop mode: interval=%ds tenant=%s output=%s", args.interval, args.tenant, output_dir)
    while True:
        run_once()
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            log.info("interrompu — exit propre")
            return 0


if __name__ == "__main__":
    sys.exit(main())
