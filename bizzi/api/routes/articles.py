"""api/routes/articles.py
==========================
Chaîne éditoriale : produire des articles via les agents.

Routes :
    POST /api/articles/produce                       — un article unique
    POST /api/articles/produce-from-meeting/{id}     — un article par assignation
    GET  /api/articles/list                          — derniers articles
    GET  /api/articles/{id}                          — détail (avec auteur + éditeur)

Chaque production passe par : producer.produce → verifier.speak (fact-check)
→ validator.validate (score+decision) → INSERT productions.
"""

import hashlib
import logging
import os
import random
import re
import unicodedata
import urllib.parse
import yaml
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from moteur.team_loader import load_team
from openai import AsyncOpenAI

router = APIRouter()
logger = logging.getLogger("api.articles")

_db = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))

# Clés externes
PEXELS_KEY = "0XrLXNBD5eFgaLpXdL6IJpYvyJHQPw7WWUaEQBiyaHyiHB1zKpSoySF0"
try:
    OPENAI_KEY = open("/opt/bizzi/bizzi/.env").read().split("OPENAI_API_KEY=")[1].split("\n")[0].strip()
except Exception:
    OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
_openai = AsyncOpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# Mapping source → région (issu de pipeline.py existant)
SOURCES_TO_REGION = {
    "France Bleu Provence":   "PACA",
    "Var Matin":              "PACA",
    "France Bleu Bretagne":   "Bretagne",
    "France Bleu Armorique":  "Bretagne",
    "Midi Libre":             "Occitanie",
    "France Bleu Toulouse":   "Occitanie",
    "France Bleu Alsace":     "Grand-Est",
    "Sud Ouest":              "Nouvelle-Aquitaine",
    "France Bleu Gironde":    "Nouvelle-Aquitaine",
    "France Bleu Loire Ocean":"Pays-de-la-Loire",
    "France Bleu Nord":       "Hauts-de-France",
    "France Bleu Reunion":    "DOM-TOM",
    "Le Parisien":            "Ile-de-France",
    "20 Minutes Paris":       "Ile-de-France",
}

FALLBACK_BY_CATEGORY = {
    "Environnement": ["default-environnement.jpg", "default-monde.jpg"],
    "Sante":         ["default-sante.jpg"],
    "Sport":         ["default-sport.jpg"],
    "Culture":       ["default-culture.jpg"],
    "Tech":          ["default-tech.jpg"],
    "Economie":      ["default-economie.jpg"],
    "Monde":         ["default-monde.jpg", "default-politique.jpg"],
    "Politique":     ["default-politique.jpg", "default-monde.jpg"],
}


def require_tenant(request: Request) -> tuple[int, str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token requis")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token vide")
    with _db.connect() as conn:
        row = conn.execute(
            text("SELECT id, slug FROM tenants WHERE token_hash = :t"),
            {"t": token},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Token invalide")
    return row[0], row[1]


def _slugify(t: str) -> str:
    s = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s[:200].strip("-")


def _agent_db_id(conn, tenant_id: int, slug: str) -> Optional[int]:
    row = conn.execute(
        text("SELECT id FROM agents WHERE tenant_id = :t AND slug = :s"),
        {"t": tenant_id, "s": slug},
    ).fetchone()
    return row[0] if row else None


_STOPWORDS = {"le","la","les","un","une","des","de","du","et","ou","a","au","aux","en","dans","sur","par","pour","avec","sans","ce","ces","est","sont","ne","pas","plus","tres","tout","tous","sa","son","ses","leur","il","elle","qui","que","quoi","dont"}

def _topic_overlap(a: str, b: str) -> int:
    """Compte les mots significatifs communs (>=4 chars, hors stopwords) entre 2 sujets."""
    def tokens(s):
        return {w for w in (s or "").lower().split() if len(w) >= 4 and w not in _STOPWORDS}
    return len(tokens(a) & tokens(b))


def _jaccard(a: str, b: str) -> float:
    def tokens(s):
        return {w for w in (s or "").lower().split() if len(w) >= 4 and w not in _STOPWORDS}
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _detect_region(source_name: str) -> Optional[str]:
    if not source_name:
        return None
    for key, region in SOURCES_TO_REGION.items():
        if key.lower() in source_name.lower():
            return region
    return None


_DOMAINS_DIR = "/opt/bizzi/bizzi/domains"


def _yaml_categories(tenant_slug: str) -> list[dict]:
    """Renvoie les categories définies dans domains/<tenant_slug>.yaml."""
    path = os.path.join(_DOMAINS_DIR, f"{tenant_slug}.yaml")
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("categories") or []
    except Exception as e:
        logger.warning(f"[CATEGORY] yaml load failed for {tenant_slug}: {e}")
        return []


def _infer_category_from_text(text: str, tenant_slug: str) -> Optional[str]:
    """Score les categories yaml par occurrence de keywords (case-insensitive).
    Retourne le label de la catégorie gagnante, ou None si aucun keyword ne matche.
    """
    cats = _yaml_categories(tenant_slug)
    if not cats:
        return None
    text_low = (text or "").lower()
    if not text_low:
        return None
    best_score = 0
    best_label = None
    for cat in cats:
        keywords = cat.get("keywords") or []
        score = sum(1 for kw in keywords if kw and kw.lower() in text_low)
        if score > best_score:
            best_score = score
            best_label = cat.get("label") or cat.get("id")
    return best_label if best_score > 0 else None


def _get_or_create_category_id(conn, name: str, tenant_id: int) -> int:
    name = name or "Une"
    row = conn.execute(
        text("SELECT id FROM categories WHERE tenant_id=:tid AND name=:n LIMIT 1"),
        {"tid": tenant_id, "n": name},
    ).fetchone()
    if row:
        return row[0]
    cat_slug = _slugify(name) or name.lower()[:80]
    new_id = conn.execute(
        text("INSERT INTO categories (tenant_id, name, slug, active, created_at) "
             "VALUES (:tid, :n, :s, true, now()) RETURNING id"),
        {"tid": tenant_id, "n": name, "s": cat_slug},
    ).fetchone()[0]
    return new_id


def _get_region_id(conn, name: Optional[str], tenant_id: int) -> Optional[int]:
    if not name:
        return None
    row = conn.execute(
        text("SELECT id FROM regions WHERE tenant_id=:tid AND name=:n LIMIT 1"),
        {"tid": tenant_id, "n": name},
    ).fetchone()
    return row[0] if row else None


async def _download_image(url: str, tenant_slug: str) -> str:
    """Télécharge une image dans /opt/<tenant>/public/img/ et retourne le chemin web /img/xxx.ext."""
    if not url:
        return ""
    try:
        save_dir = f"/opt/{tenant_slug}/public/img"
        os.makedirs(save_dir, exist_ok=True)
        ext = url.split("?")[0].split(".")[-1][:4] or "jpg"
        if not ext.isalnum():
            ext = "jpg"
        filename = f"{uuid.uuid4().hex[:8]}.{ext}"
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 1024:
                with open(f"{save_dir}/{filename}", "wb") as f:
                    f.write(r.content)
                return f"/img/{filename}"
    except Exception as e:
        logger.warning(f"[IMG] download {url[:60]}: {e}")
    return ""


async def _search_pexels(query: str, tenant_slug: str) -> str:
    """Cherche une photo Pexels à partir du titre français (GPT traduit en mots-clés EN)."""
    if not _openai:
        return ""
    try:
        kw_resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Give 3 precise english keywords for a news photo about: {query[:120]}. Return only keywords separated by spaces, no punctuation."}],
            max_tokens=15, temperature=0,
        )
        keywords = kw_resp.choices[0].message.content.strip()
        q = urllib.parse.quote(keywords)
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"https://api.pexels.com/v1/search?query={q}&per_page=10&orientation=landscape",
                headers={"Authorization": PEXELS_KEY},
            )
            if r.status_code == 200:
                photos = (r.json() or {}).get("photos") or []
                if photos:
                    photo = random.choice(photos[:5])
                    return await _download_image(photo["src"]["large"], tenant_slug)
    except Exception as e:
        logger.warning(f"[PEXELS] {e}")
    return ""


def _fallback_image(category: str, title: str) -> str:
    """Image fallback statique selon la catégorie, choisie par hash MD5 du titre (évite doublons consécutifs)."""
    pool = FALLBACK_BY_CATEGORY.get(category) or ["default-monde.jpg"]
    idx = int(hashlib.md5((title or "").encode("utf-8", "ignore")).hexdigest(), 16) % len(pool)
    return f"/img/{pool[idx]}"


async def _pick_image(news_image_url: str, title: str, category: str, tenant_slug: str, conn, tenant_id: int) -> str:
    """Cascade : RSS → Pexels → fallback. Anti-doublon DB sur 30 jours."""
    candidates = []

    # 1. Image RSS du flux
    if news_image_url:
        local = await _download_image(news_image_url, tenant_slug)
        if local:
            candidates.append(local)

    # 2. Pexels si rien
    if not candidates:
        pexels = await _search_pexels(title, tenant_slug)
        if pexels:
            candidates.append(pexels)

    # 3. Fallback statique en dernier recours (toujours dispo)
    candidates.append(_fallback_image(category, title))

    # Anti-doublon : check DB pour les 30 derniers jours
    for c in candidates:
        if not c:
            continue
        existing = conn.execute(
            text("SELECT 1 FROM productions WHERE tenant_id=:t AND image_url=:i AND created_at > now() - interval '30 days' LIMIT 1"),
            {"t": tenant_id, "i": c},
        ).fetchone()
        if not existing:
            return c

    # Si TOUTES les candidates sont déjà utilisées récemment, on prend la dernière (fallback) anyway
    return candidates[-1] if candidates else ""


class ProduceRequest(BaseModel):
    topic: str
    journalist_slug: str


async def _produce_one(topic, journalist, validator, verifier, tenant_id, conn,
                        context: str = "", news_meta: Optional[dict] = None,
                        tenant_slug: Optional[str] = None, publish_on_approve: bool = False):
    """Pipeline complet : check_memory → produce(topic, context) → fact_check → validate → INSERT (status=published si approve+publish_on_approve) → save_memory."""

    from tools.knowledge.knowledge_engine import KnowledgeEngine
    engine = KnowledgeEngine(agent_slug=journalist.slug)
    news_meta = news_meta or {}

    # H.2 — Anti-doublon sujet
    similar = engine.get_memory(topic, limit=3)
    if similar and any(s.get("score_match", 1) >= 2 or _topic_overlap(topic, s.get("topic", "")) >= 2 for s in similar):
        return {
            "status":     "skipped_duplicate",
            "topic":      topic,
            "journalist": {"slug": journalist.slug, "name": journalist.name},
            "reason":     f"{journalist.name} a déjà couvert ce sujet récemment",
            "previous":   [{"topic": s.get("topic"), "added_at": s.get("added_at")} for s in similar[:2]],
        }

    result = await journalist.produce(topic, context=context)
    content = (result or {}).get("content", "")
    if not content:
        return {"status": "error", "topic": topic, "reason": "Production vide (OpenAI a renvoyé du vide)"}

    fact_check_msg = ""
    if verifier:
        fact_check_msg = await verifier.speak(
            f"Tu fact-checkes cet article sur '{topic}'. "
            f"Liste 3 affirmations à vérifier en priorité (puces courtes).\n\n"
            f"Article :\n{content[:2000]}"
        )

    score = 0
    decision = "reject"
    feedback = ""
    if validator:
        validation = await validator.validate(content)
        score    = validation.get("score", 0)
        decision = validation.get("decision", "reject")
        feedback = validation.get("feedback", "")

    journalist_db_id = _agent_db_id(conn, tenant_id, journalist.slug)
    validator_db_id  = _agent_db_id(conn, tenant_id, validator.slug) if validator else None

    first_line = content.split("\n", 1)[0].strip().lstrip("# ").strip()
    title = (first_line[:200] or topic)[:500]
    base_slug = _slugify(title) or _slugify(topic) or f"article-{int(datetime.utcnow().timestamp())}"
    slug = base_slug
    n = 1
    while conn.execute(text("SELECT 1 FROM productions WHERE slug = :s LIMIT 1"), {"s": slug}).fetchone():
        n += 1
        suffix = f"-{n}"
        slug = base_slug[:200 - len(suffix)] + suffix
        if n > 50:
            slug = f"{base_slug[:180]}-{int(datetime.utcnow().timestamp())}"
            break

    # Status final : si approve et publication demandée → published. Sinon rejected/approved_by_editor.
    if decision == "approve":
        db_status = "published" if publish_on_approve else "approved_by_editor"
    else:
        db_status = "rejected"

    # Image (si publication) + catégorie + région
    image_url = ""
    cat_id = None
    reg_id = None
    if publish_on_approve and decision == "approve" and tenant_slug:
        cat_name = news_meta.get("category")
        if not cat_name:
            cat_name = _infer_category_from_text(f"{title}\n\n{content}", tenant_slug) or "Une"
        region_name = news_meta.get("region") or _detect_region(news_meta.get("source", ""))
        cat_id = _get_or_create_category_id(conn, cat_name, tenant_id)
        reg_id = _get_region_id(conn, region_name, tenant_id)
        image_url = await _pick_image(
            news_image_url=news_meta.get("image", ""),
            title=title,
            category=cat_name,
            tenant_slug=tenant_slug,
            conn=conn,
            tenant_id=tenant_id,
        )

    publish_ts = datetime.utcnow() if db_status == "published" else None

    insert = conn.execute(text("""
        INSERT INTO productions (
            tenant_id, agent_id, editor_agent_id,
            content_type, title, slug, content_html, content_raw,
            word_count, quality_score, qa_score, qa_passed, qa_reason,
            fact_check_status,
            category_id, region_id, image_url,
            status,
            created_at, updated_at, published_at
        ) VALUES (
            :tid, :aid, :eid,
            'article', :title, :slug, :html, :raw,
            :wc, :qs, :qa, :qp, :qr,
            :fcs,
            :cat, :reg, :img,
            CAST(:st AS productionstatus),
            now(), now(), :pub
        ) RETURNING id
    """), {
        "tid": tenant_id, "aid": journalist_db_id, "eid": validator_db_id,
        "title": title, "slug": slug,
        "html": content, "raw": content,
        "wc": len(content.split()),
        "qs": int(score), "qa": float(score), "qp": (decision == "approve"),
        "qr": feedback,
        "fcs": "checked" if fact_check_msg else "skipped",
        "cat": cat_id, "reg": reg_id, "img": image_url or None,
        "st": db_status,
        "pub": publish_ts,
    }).fetchone()

    # H.1 — Mémoire auto : enregistre dans competences/<slug>/memory.json
    try:
        engine.add_memory(
            content=f"[{title}] score={int(score)}/100 decision={decision} — {(content or '')[:300]}",
            topic=topic,
            source=f"production_{insert[0]}",
        )
    except Exception as e:
        logger.warning(f"[MEMORY] add_memory échoué pour {journalist.slug}: {e}")

    # H.3 — Trigger social : si article publié, enfile un post réseau social
    # en shadow mode (Pascal valide via /api/social/post/{id}/approve).
    if db_status == "published" and tenant_slug:
        try:
            logger.info(f"[SOCIAL] firing trigger article_published for production_{insert[0]} tenant={tenant_slug}")
            from social.triggers import fire_article_published
            cat_slug = None
            reg_slug = None
            reg_label = None
            if cat_id:
                row = conn.execute(
                    text("SELECT slug, name FROM categories WHERE id=:i"),
                    {"i": cat_id},
                ).fetchone()
                if row:
                    cat_slug = row[0]
            if reg_id:
                row = conn.execute(
                    text("SELECT slug, name FROM regions WHERE id=:i"),
                    {"i": reg_id},
                ).fetchone()
                if row:
                    reg_slug, reg_label = row[0], row[1]
            excerpt = "\n".join(content.splitlines()[1:]).strip().split("\n\n", 1)[0][:200]
            fire_article_published(
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                article={
                    "id":           insert[0],
                    "agent_id":     journalist_db_id,
                    "title":        title,
                    "excerpt":      excerpt,
                    "category":     cat_slug,
                    "region":       reg_slug,
                    "region_label": reg_label,
                    "image_url":    image_url,
                    "slug":         slug,
                },
            )
        except Exception as e:
            logger.warning(f"[SOCIAL] trigger article_published échoué : {e}")

    return {
        "production_id": insert[0],
        "status":        db_status,
        "title":         title,
        "slug":          slug,
        "topic":         topic,
        "journalist":    {"slug": journalist.slug, "name": journalist.name, "specialty": journalist.specialty},
        "validator":     {"slug": validator.slug, "name": validator.name} if validator else None,
        "verifier":      {"slug": verifier.slug, "name": verifier.name} if verifier else None,
        "word_count":    len(content.split()),
        "score":         int(score),
        "decision":      decision,
        "feedback":      feedback,
        "fact_check":    fact_check_msg,
        "content":       content,
    }


@router.post("/produce")
async def produce_article(data: ProduceRequest, request: Request):
    """Produit UN article via un journaliste donné."""
    tenant_id, tenant_slug = require_tenant(request)
    config, team = load_team(tenant_slug)

    journalist = next((a for a in team if a.slug == data.journalist_slug), None)
    if not journalist:
        raise HTTPException(status_code=404, detail=f"Agent '{data.journalist_slug}' introuvable")
    if journalist.role != "production":
        raise HTTPException(status_code=400, detail=f"{journalist.name} n'est pas un producteur (role={journalist.role})")

    validator = next((a for a in team if a.role == "validation"   and a.status == "active"), None)
    verifier  = next((a for a in team if a.role == "verification" and a.status == "active"), None)

    started = datetime.utcnow()
    with _db.begin() as conn:
        result = await _produce_one(data.topic, journalist, validator, verifier, tenant_id, conn)
    finished = datetime.utcnow()
    result["duration_seconds"] = int((finished - started).total_seconds())
    logger.info(
        f"[ARTICLE] tenant={tenant_slug} {journalist.slug} → '{data.topic[:40]}' "
        f"score={result.get('score')} decision={result.get('decision')} {result['duration_seconds']}s"
    )
    return result


@router.post("/produce-from-meeting/{meeting_id}")
async def produce_from_meeting(meeting_id: int, request: Request):
    """Produit un article par assignation de la réunion donnée (chaîne complète)."""
    tenant_id, tenant_slug = require_tenant(request)

    with _db.connect() as conn:
        row = conn.execute(text("""
            SELECT assignments FROM meetings
            WHERE tenant_id = :t AND id = :m
        """), {"t": tenant_id, "m": meeting_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Réunion {meeting_id} introuvable")
    assignments = row[0] or {}
    if not assignments:
        raise HTTPException(status_code=400, detail="Cette réunion n'a aucune assignation.")

    config, team = load_team(tenant_slug)
    validator = next((a for a in team if a.role == "validation"   and a.status == "active"), None)
    verifier  = next((a for a in team if a.role == "verification" and a.status == "active"), None)

    started = datetime.utcnow()
    results = []
    with _db.begin() as conn:
        for slug, topic in assignments.items():
            journalist = next((a for a in team if a.slug == slug), None)
            if not journalist:
                results.append({"slug": slug, "topic": topic, "status": "error", "reason": "agent introuvable"})
                continue
            try:
                results.append(await _produce_one(topic, journalist, validator, verifier, tenant_id, conn))
            except Exception as e:
                logger.exception(f"[ARTICLE] erreur sur {slug} → '{topic}'")
                results.append({"slug": slug, "topic": topic, "status": "error", "reason": str(e)})
    finished = datetime.utcnow()

    approved = sum(1 for r in results if r.get("decision") == "approve")
    skipped  = sum(1 for r in results if r.get("status") == "skipped_duplicate")
    return {
        "meeting_id":       meeting_id,
        "duration_seconds": int((finished - started).total_seconds()),
        "articles_count":   sum(1 for r in results if "production_id" in r),
        "approved_count":   approved,
        "skipped_count":    skipped,
        "articles":         results,
    }


@router.get("/list")
async def list_articles(request: Request, limit: int = 20):
    tenant_id, tenant_slug = require_tenant(request)
    with _db.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.title, p.slug, p.word_count, p.quality_score, p.qa_passed,
                   p.fact_check_status, p.status::text, p.created_at,
                   a.slug, a.name
            FROM productions p
            LEFT JOIN agents a ON a.id = p.agent_id
            WHERE p.tenant_id = :tid
            ORDER BY p.created_at DESC
            LIMIT :lim
        """), {"tid": tenant_id, "lim": min(limit, 100)}).fetchall()
    return {
        "tenant":   tenant_slug,
        "count":    len(rows),
        "articles": [{
            "id":                r[0],
            "title":             r[1],
            "slug":              r[2],
            "word_count":        r[3],
            "quality_score":     r[4],
            "qa_passed":         r[5],
            "fact_check_status": r[6],
            "status":            r[7],
            "created_at":        r[8].isoformat() if r[8] else None,
            "journalist_slug":   r[9],
            "journalist_name":   r[10],
        } for r in rows],
    }


@router.get("/{article_id}")
async def get_article(article_id: int, request: Request):
    tenant_id, _ = require_tenant(request)
    with _db.connect() as conn:
        row = conn.execute(text("""
            SELECT p.id, p.title, p.slug, p.content_html, p.word_count,
                   p.quality_score, p.qa_passed, p.qa_reason, p.fact_check_status,
                   p.status::text, p.created_at,
                   a.slug, a.name, e.slug, e.name
            FROM productions p
            LEFT JOIN agents a ON a.id = p.agent_id
            LEFT JOIN agents e ON e.id = p.editor_agent_id
            WHERE p.tenant_id = :tid AND p.id = :aid
        """), {"tid": tenant_id, "aid": article_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Article introuvable")
    return {
        "id":                row[0],
        "title":             row[1],
        "slug":              row[2],
        "content":           row[3],
        "word_count":        row[4],
        "quality_score":     row[5],
        "qa_passed":         row[6],
        "qa_reason":         row[7],
        "fact_check_status": row[8],
        "status":            row[9],
        "created_at":        row[10].isoformat() if row[10] else None,
        "journalist_slug":   row[11],
        "journalist_name":   row[12],
        "editor_slug":       row[13],
        "editor_name":       row[14],
    }


# ════════════════════════════════════════════════════════════════════
# AUTO-PIPELINE : scrape → match → produce → publish
# ════════════════════════════════════════════════════════════════════

class AutoPipelineRequest(BaseModel):
    category: Optional[str] = None
    region:   Optional[str] = None
    max_articles: int = 5


def _match_journalist(producers: list, news: dict, taken: set):
    """Choisit le journaliste dont la specialty matche le mieux le titre/category de la news."""
    free = [p for p in producers if p.slug not in taken]
    if not free:
        return None
    title_low = (news.get("title", "") + " " + news.get("category", "")).lower()
    for p in free:
        spec_words = [w.lower() for w in (p.specialty or "").split() if len(w) >= 4]
        if any(w in title_low for w in spec_words):
            return p
    return free[0]


@router.post("/auto-pipeline")
async def auto_pipeline(data: AutoPipelineRequest, request: Request):
    """Chaîne complète : scrape → matching → meeting léger → produce(context=RSS) → publish.
    L'article publié atterrit en status='published' donc visible direct sur le site du tenant."""
    tenant_id, tenant_slug = require_tenant(request)
    config, team = load_team(tenant_slug)

    validator = next((a for a in team if a.role == "validation"   and a.status == "active"), None)
    verifier  = next((a for a in team if a.role == "verification" and a.status == "active"), None)
    producers = [a for a in team if a.role == "production" and a.status == "active"]
    if not producers:
        raise HTTPException(status_code=400, detail=f"Aucun producteur actif pour {tenant_slug}")

    # 1. Scrape
    from tools.scraper.news_scraper import scrape_news, scrape_by_region
    if data.region:
        all_news = await scrape_by_region(data.region, max_articles=8)
    else:
        all_news = await scrape_news(max_articles=12, category=data.category)

    if not all_news:
        return {"tenant": tenant_slug, "scraped": 0, "articles": [], "reason": "Aucune news scrapée"}

    # 2. Anti-doublon : 200 derniers titres en DB
    with _db.connect() as conn:
        existing_titles = [r[0] for r in conn.execute(text("""
            SELECT title FROM productions
            WHERE tenant_id = :t AND created_at > now() - interval '30 days'
            ORDER BY created_at DESC LIMIT 200
        """), {"t": tenant_id}).fetchall()]

    fresh_news = []
    for n in all_news:
        title = n.get("title", "")
        if not title:
            continue
        # Match exact ou Jaccard > 0.5
        if any(_jaccard(title, t) > 0.5 for t in existing_titles):
            continue
        # Pas 2 fois la même news en 1 lot
        if any(_jaccard(title, x.get("title", "")) > 0.6 for x in fresh_news):
            continue
        fresh_news.append(n)
        if len(fresh_news) >= data.max_articles:
            break

    if not fresh_news:
        return {"tenant": tenant_slug, "scraped": len(all_news), "articles": [],
                "reason": "Toutes les news scrapées sont déjà couvertes"}

    # 3. Matching journalistes
    taken = set()
    pairs = []
    for n in fresh_news:
        j = _match_journalist(producers, n, taken)
        if j:
            pairs.append((j, n))
            taken.add(j.slug)

    # 4. Production en chaîne
    started = datetime.utcnow()
    results = []
    with _db.begin() as conn:
        for journalist, news in pairs:
            try:
                r = await _produce_one(
                    topic       = news["title"],
                    journalist  = journalist,
                    validator   = validator,
                    verifier    = verifier,
                    tenant_id   = tenant_id,
                    conn        = conn,
                    context     = news.get("full_content", "") or news.get("desc", ""),
                    news_meta   = news,
                    tenant_slug = tenant_slug,
                    publish_on_approve = True,
                )
                r["news_source"] = news.get("source", "")
                r["news_url"]    = news.get("link", "")
                results.append(r)
            except Exception as e:
                logger.exception(f"[AUTO] erreur produce {journalist.slug} → {news['title'][:40]}")
                results.append({
                    "status": "error", "topic": news["title"],
                    "journalist": journalist.slug, "reason": str(e),
                })
    finished = datetime.utcnow()

    published = sum(1 for r in results if r.get("status") == "published")
    rejected  = sum(1 for r in results if r.get("status") == "rejected")
    skipped   = sum(1 for r in results if r.get("status") == "skipped_duplicate")

    return {
        "tenant":           tenant_slug,
        "duration_seconds": int((finished - started).total_seconds()),
        "scraped":          len(all_news),
        "fresh":            len(fresh_news),
        "matched":          len(pairs),
        "published_count":  published,
        "rejected_count":   rejected,
        "skipped_count":    skipped,
        "articles":         results,
    }
