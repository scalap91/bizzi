"""
Pipeline RSS → productions
- Slugify SEO propre (60 chars max, coupe sur mot complet)
- Insère dans table productions avec category_id + region_id (FK)
- Anti-doublon sur productions.slug et productions.title
"""

# ── Config régions ─────────────────────────────────────────────
SOURCES_TO_REGION = {
    "France Bleu Provence": "PACA",
    "Var Matin": "PACA",
    "France Bleu Bretagne": "Bretagne",
    "France Bleu Armorique": "Bretagne",
    "Midi Libre": "Occitanie",
    "France Bleu Toulouse": "Occitanie",
    "France Bleu Alsace": "Grand-Est",
    "Sud Ouest": "Nouvelle-Aquitaine",
    "France Bleu Gironde": "Nouvelle-Aquitaine",
    "France Bleu Loire Ocean": "Pays-de-la-Loire",
    "France Bleu Nord": "Hauts-de-France",
    "France Bleu Reunion": "DOM-TOM",
    "Le Parisien": "Ile-de-France",
    "20 Minutes Paris": "Ile-de-France",
}

def detect_region(source_name):
    if not source_name:
        return None
    for key, region in SOURCES_TO_REGION.items():
        if key.lower() in source_name.lower():
            return region
    return None


# ── Qualité contenu ───────────────────────────────────────────
def similarity_score(text1, text2):
    if not text1 or not text2:
        return 0
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    if not words1 or not words2:
        return 0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)

def is_too_similar(generated, source, threshold=0.5):
    return similarity_score(generated, source) > threshold

def is_too_short(content, min_words=150):
    return len(content.split()) < min_words


import os

# ── Slugify SEO (60 chars max, coupe propre) ──────────────────
import re as _re
import unicodedata as _ud

# Mots vides français à retirer si le slug dépasse la limite
STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "d",
    "et", "ou", "a", "au", "aux", "en", "dans", "sur", "sous",
    "par", "pour", "avec", "sans", "que", "qui", "quoi", "dont",
    "ce", "cet", "cette", "ces", "se", "sa", "son", "ses", "leur",
    "il", "elle", "ils", "elles", "on", "y", "est", "sont",
    "ne", "pas", "plus", "tres", "tout", "tous", "toute", "toutes",
}

def slugify_seo(s, max_len=60):
    """
    Slug SEO : minuscules, sans accents, mots séparés par '-'.
    Coupe propre sur mot complet, max 60 chars.
    Si dépasse, retire les stopwords du milieu jusqu'à passer.
    """
    if not s:
        return ""
    # Normalisation
    s = s.lower()
    s = _ud.normalize("NFD", s)
    s = _re.sub(r"[\u0300-\u036f]", "", s)
    s = _re.sub(r"[^a-z0-9\s-]", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()

    words = [w for w in s.split() if w and w != "-"]
    if not words:
        return ""

    # Cas 1 : on essaie d'inclure tous les mots
    candidate = "-".join(words)
    if len(candidate) <= max_len:
        return candidate

    # Cas 2 : trop long → on retire les stopwords (sauf le 1er mot)
    filtered = [words[0]] + [w for w in words[1:] if w not in STOPWORDS]
    candidate = "-".join(filtered)
    if len(candidate) <= max_len:
        return candidate

    # Cas 3 : encore trop long → on tronque sur mot complet
    result = []
    current_len = 0
    for w in filtered:
        # +1 pour le tiret
        added = len(w) + (1 if result else 0)
        if current_len + added > max_len:
            break
        result.append(w)
        current_len += added

    return "-".join(result) if result else filtered[0][:max_len]


# Alias pour rétro-compat
def slugify(s):
    return slugify_seo(s, max_len=60)


# ── Imports & config ──────────────────────────────────────────
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio, httpx, logging, os, uuid
from datetime import datetime
PEXELS_KEY = "0XrLXNBD5eFgaLpXdL6IJpYvyJHQPw7WWUaEQBiyaHyiHB1zKpSoySF0"
from sqlalchemy import create_engine, text
from openai import AsyncOpenAI
import sys
sys.path.insert(0, '/opt/bizzi/bizzi')
from tools.scraper.news_scraper import scrape_news, scrape_by_region, CATEGORIES, SOURCES_BY_REGION
from moteur.team_loader import load_team

router = APIRouter()
logger = logging.getLogger("api.pipeline")
RUNS = {}

engine = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))
openai_client = AsyncOpenAI(
    api_key=open("/opt/bizzi/bizzi/.env").read().split("OPENAI_API_KEY=")[1].split("\n")[0].strip()
)

# Tenant Onyx Infos (id=1) — à externaliser plus tard si multi-tenant
DEFAULT_TENANT_ID = 1

# Mapping tenant URL → slug DB (le cron envoie "onyx-infos", la DB a "onyx")
TENANT_SLUG_MAP = {"onyx-infos": "onyx", "onyx": "onyx", "lediagnostiqueur": "lediagnostiqueur"}


def _match_journalist(producers, news):
    """Choisit le journaliste dont la specialty matche le titre/category de la news."""
    if not producers:
        return None
    haystack = (news.get("title", "") + " " + (news.get("category") or "")).lower()
    for p in producers:
        spec_words = [w.lower() for w in (p.specialty or "").split() if len(w) >= 4]
        if any(w in haystack for w in spec_words):
            return p
    return producers[0]


def _agent_db_id(conn, tenant_id, slug):
    if not slug:
        return None
    row = conn.execute(
        text("SELECT id FROM agents WHERE tenant_id=:t AND slug=:s LIMIT 1"),
        {"t": tenant_id, "s": slug},
    ).fetchone()
    return row[0] if row else None


# ── Helpers FK ────────────────────────────────────────────────
def get_category_id(conn, name, tenant_id=DEFAULT_TENANT_ID):
    """Retourne l'id d'une catégorie par son nom. Crée si manquante."""
    if not name:
        name = "Une"
    row = conn.execute(
        text("SELECT id FROM categories WHERE tenant_id=:tid AND name=:n LIMIT 1"),
        {"tid": tenant_id, "n": name}
    ).fetchone()
    if row:
        return row[0]
    # Auto-création si la catégorie n'existe pas
    cat_slug = slugify_seo(name, max_len=80)
    new_id = conn.execute(
        text("INSERT INTO categories (tenant_id, name, slug, active, created_at) "
             "VALUES (:tid, :n, :s, true, now()) RETURNING id"),
        {"tid": tenant_id, "n": name, "s": cat_slug}
    ).fetchone()[0]
    logger.info(f"[CAT] Auto-créée: {name} (id={new_id})")
    return new_id

def get_region_id(conn, name, tenant_id=DEFAULT_TENANT_ID):
    """Retourne l'id d'une région par son nom. None si absente (pas d'auto-création)."""
    if not name:
        return None
    row = conn.execute(
        text("SELECT id FROM regions WHERE tenant_id=:tid AND name=:n LIMIT 1"),
        {"tid": tenant_id, "n": name}
    ).fetchone()
    return row[0] if row else None


# ── Sauvegarde DB (productions) ───────────────────────────────
def save_to_db(run_id, tenant, topic, cnt, image_url="", category="Une", source="",
               agent_db_id=None, editor_db_id=None, qa_score=None, qa_reason="",
               region_override=None):
    """Insère un article dans productions avec FK category_id + region_id (+ agent_id si fourni)."""
    try:
        with engine.connect() as conn:
            # Slug SEO unique
            base_slug = slugify_seo(topic, max_len=60)
            if not base_slug:
                logger.warning(f"[DB] Slug vide pour: {topic[:50]}")
                return False

            slug = base_slug
            n = 1
            while True:
                exists = conn.execute(
                    text("SELECT id FROM productions WHERE slug=:s LIMIT 1"),
                    {"s": slug}
                ).fetchone()
                if not exists:
                    break
                n += 1
                # On ajoute juste -N à la fin, en respectant max_len
                suffix = f"-{n}"
                slug = base_slug[:60 - len(suffix)] + suffix

            # FK
            cat_id = get_category_id(conn, category)
            # Région : (1) override fourni par le scrape (item['region'] depuis SOURCES_BY_REGION),
            #         (2) mapping source RSS (SOURCES_TO_REGION), (3) sinon NULL (national/international).
            region_name = region_override or detect_region(source)
            reg_id = get_region_id(conn, region_name)

            # Word count rapide
            wc = len(cnt.split())

            conn.execute(text("""
                INSERT INTO productions
                (tenant_id, agent_id, editor_agent_id,
                 title, slug, content_html, content_raw, image_url,
                 category_id, region_id, status, word_count,
                 quality_score, qa_score, qa_passed, qa_reason,
                 created_at, published_at)
                VALUES
                (:tid, :aid, :eid,
                 :title, :slug, :html, :raw, :img,
                 :cat_id, :reg_id, 'published'::productionstatus, :wc,
                 :qs, :qa, :qp, :qr,
                 :now, :now)
            """), {
                "tid": DEFAULT_TENANT_ID,
                "aid": agent_db_id,
                "eid": editor_db_id,
                "title": topic[:500],
                "slug": slug,
                "html": cnt,
                "raw": cnt,
                "img": image_url,
                "cat_id": cat_id,
                "reg_id": reg_id,
                "wc": wc,
                "qs": int(qa_score) if qa_score is not None else None,
                "qa": float(qa_score) if qa_score is not None else None,
                "qp": (qa_score is not None and qa_score >= 70),
                "qr": qa_reason or "",
                "now": datetime.utcnow(),
            })
            conn.commit()
            logger.info(f"[DB] OK · {category} · {slug}")
            return True
    except Exception as e:
        logger.error(f"[DB] Erreur insertion: {e}")
        return False


# ── Téléchargement image ──────────────────────────────────────
async def search_pexels_image(query, tenant):
    try:
        import urllib.parse, random
        # GPT génère 3 mots-clés anglais précis
        kw_resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":f"Give 3 precise english keywords for a news photo search about this french article title. Return only keywords separated by spaces, no punctuation, no explanation: {query[:100]}"}],
            max_tokens=15, temperature=0
        )
        keywords = kw_resp.choices[0].message.content.strip()
        logger.info(f"[PEXELS] keywords: {keywords}")
        q = urllib.parse.quote(keywords)
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"https://api.pexels.com/v1/search?query={q}&per_page=10&orientation=landscape",
                headers={"Authorization": PEXELS_KEY}
            )
            if r.status_code == 200:
                data = r.json()
                photos = data.get("photos", [])
                if photos:
                    photo = random.choice(photos[:5])
                    img_url = photo["src"]["large"]
                    return await download_image(img_url, tenant)
    except Exception as e:
        logger.warning(f"[PEXELS] {e}")
    return ""

async def download_image(url, tenant):
    if not url:
        return ""
    try:
        save_dir = f"/opt/{tenant}/public/img"
        os.makedirs(save_dir, exist_ok=True)
        ext = url.split(".")[-1].split("?")[0][:4] or "jpg"
        filename = f"{uuid.uuid4().hex[:8]}.{ext}"
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                with open(f"{save_dir}/{filename}", "wb") as f:
                    f.write(r.content)
                return f"/img/{filename}"
    except Exception as e:
        logger.warning(f"[IMG] {e}")
    return ""


# ── API ───────────────────────────────────────────────────────
class PipelineRequest(BaseModel):
    tenant: str
    domain: str = "media"
    topics: Optional[list] = None
    category: Optional[str] = None


@router.post("/run")
async def run_pipeline(data: PipelineRequest):
    run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    RUNS[run_id] = {"status": "started", "tenant": data.tenant, "content": [], "rejected": []}
    asyncio.create_task(execute_pipeline(run_id, data))
    return {
        "status": "started",
        "run_id": run_id,
        "domain": data.domain,
        "check": f"/api/pipeline/status/{run_id}"
    }


async def _process_one_article(item, run_id, data, producers, validator, verifier, label="NAT"):
    """Pipeline complet pour 1 article : produce → fact_check → validate → image → save_to_db.
    Retourne True si publié, False sinon. Étanche : la région vient de item['region'] (ou None pour national).
    """
    cat = item.get("category", "Une")
    source = item.get("source", "")
    title = item["title"]

    # 1. Choix journaliste par specialty
    journalist = _match_journalist(producers, item)
    if not journalist:
        return False

    # 2. Production
    try:
        rss_context = item.get("full_content") or item.get("desc") or ""
        ctx = f"SOURCE : {source}\nTITRE : {title}\nCONTENU :\n{rss_context[:2000]}"
        produce_result = await journalist.produce(topic=title, context=ctx)
        cnt = (produce_result or {}).get("content", "")
    except Exception as e:
        logger.error(f"[{label}] {journalist.slug} produce error: {e}")
        RUNS[run_id]["rejected"].append({"title": title, "journalist": journalist.slug, "stage": "produce", "reason": str(e)})
        return False

    if not cnt:
        RUNS[run_id]["rejected"].append({"title": title, "journalist": journalist.slug, "stage": "produce", "reason": "contenu vide"})
        return False

    # 3. Fact-check (log only)
    fact_check_msg = ""
    if verifier:
        try:
            fact_check_msg = await verifier.speak(
                f"Tu fact-checkes cet article. Liste 3 affirmations à vérifier en priorité (puces courtes).\n\nArticle :\n{cnt[:2000]}"
            )
        except Exception as e:
            logger.warning(f"[FACTCHECK] {verifier.slug}: {e}")

    # 4. Validation Victor (si reject → SKIP)
    qa_score = None
    qa_reason = ""
    if validator:
        try:
            validation = await validator.validate(cnt)
            qa_score = validation.get("score", 0)
            decision = validation.get("decision", "reject")
            qa_reason = validation.get("feedback", "")
        except Exception as e:
            logger.error(f"[VALIDATE] {validator.slug}: {e}")
            decision = "reject"
        if decision != "approve":
            logger.info(f"[QA-REJECT][{label}] {journalist.slug} → '{title[:60]}' score={qa_score}")
            RUNS[run_id]["rejected"].append({"title": title, "journalist": journalist.slug, "stage": "victor", "score": qa_score, "reason": qa_reason})
            return False

    # 5. QA bas niveau
    if is_too_short(cnt, min_words=150):
        RUNS[run_id]["rejected"].append({"title": title, "journalist": journalist.slug, "stage": "qa-short", "reason": f"{len(cnt.split())} mots"})
        return False
    if is_too_similar(cnt, item.get("description", "") + " " + title, threshold=0.6):
        RUNS[run_id]["rejected"].append({"title": title, "journalist": journalist.slug, "stage": "qa-similar", "reason": "trop proche RSS"})
        return False

    # 6. Image cascade
    local_img = await download_image(item.get("image", ""), data.tenant)
    if not local_img:
        local_img = await search_pexels_image(title, data.tenant)
    if not local_img:
        import hashlib
        fallback_pools = {
            "Environnement": ["default-environnement.jpg", "default-monde.jpg"],
            "Sante": ["default-sante.jpg"], "Sport": ["default-sport.jpg"],
            "Culture": ["default-culture.jpg"], "Tech": ["default-tech.jpg"],
            "Economie": ["default-economie.jpg"],
            "Monde": ["default-monde.jpg", "default-politique.jpg"],
            "Politique": ["default-politique.jpg", "default-monde.jpg"],
        }
        pool = fallback_pools.get(cat, ["default-monde.jpg"])
        idx = int(hashlib.md5(title.encode()).hexdigest(), 16) % len(pool)
        local_img = "/img/" + pool[idx]

    # 7. FK agents
    with engine.connect() as _conn:
        aid = _agent_db_id(_conn, DEFAULT_TENANT_ID, journalist.slug)
        eid = _agent_db_id(_conn, DEFAULT_TENANT_ID, validator.slug) if validator else None

    # 8. INSERT (region_override = item['region'] si fourni par scrape régional, sinon None)
    ok = save_to_db(
        run_id, data.tenant, title, cnt, local_img, cat, source,
        agent_db_id=aid, editor_db_id=eid, qa_score=qa_score, qa_reason=qa_reason,
        region_override=item.get("region"),
    )
    if ok:
        RUNS[run_id]["content"].append({
            "topic": title, "content": cnt, "image": local_img,
            "category": cat, "source": source, "region": item.get("region", ""),
            "journalist": journalist.slug, "qa_score": qa_score,
            "fact_check": fact_check_msg, "created_at": datetime.utcnow().isoformat(),
        })
        logger.info(f"[OK][{label}] {journalist.slug} → '{title[:50]}' region={item.get('region','-')} score={qa_score}")
    return ok


async def execute_pipeline(run_id, data):
    try:
        RUNS[run_id]["status"] = "running"

        # ── Charger l'équipe Bizzi du tenant (agents avec leur prompt perso) ──
        tenant_db_slug = TENANT_SLUG_MAP.get(data.tenant, "onyx")
        try:
            _config, team = load_team(tenant_db_slug)
        except Exception as e:
            logger.error(f"[PIPELINE] load_team({tenant_db_slug}) a échoué: {e}")
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["error"] = f"load_team failed: {e}"
            return

        producers = [a for a in team if a.role == "production" and a.status == "active"]
        validator = next((a for a in team if a.role == "validation" and a.status == "active"), None)
        verifier  = next((a for a in team if a.role == "verification" and a.status == "active"), None)

        if not producers:
            logger.error(f"[PIPELINE] Aucun producteur actif pour {tenant_db_slug}")
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["error"] = "no_producers"
            return

        logger.info(f"[PIPELINE] {tenant_db_slug} · {len(producers)} producteurs · validator={validator.name if validator else 'aucun'} · verifier={verifier.name if verifier else 'aucun'}")

        # ── Anti-doublon : récupérer titres déjà publiés ──────
        with engine.connect() as conn:
            existing = [
                r[0] for r in conn.execute(text(
                    "SELECT title FROM productions "
                    "WHERE tenant_id=:tid AND status='published'::productionstatus "
                    "ORDER BY created_at DESC LIMIT 200"
                ), {"tid": DEFAULT_TENANT_ID}).fetchall()
            ]
        seen_titles = set(existing)

        approved_nat = 0
        approved_reg = 0

        # ════════════════════════════════════════════════════════════
        # PHASE 1 : ÉDITION NATIONALE — max 4 articles (region=NULL)
        # ════════════════════════════════════════════════════════════
        national_news = []
        for cat in [None] + CATEGORIES:
            try:
                items = await scrape_news(max_articles=3, category=cat)
                for n in items:
                    if n["title"] not in seen_titles:
                        seen_titles.add(n["title"])
                        national_news.append(n)
            except Exception as e:
                logger.warning(f"[SCRAPE NAT] {cat}: {e}")

        logger.info(f"[PIPELINE] Phase NATIONALE : {len(national_news)} candidats")
        for item in national_news[:4]:
            try:
                if await _process_one_article(item, run_id, data, producers, validator, verifier, label="NAT"):
                    approved_nat += 1
            except Exception as e:
                logger.error(f"[NAT] erreur sur '{item.get('title','')[:50]}': {e}")

        # ════════════════════════════════════════════════════════════
        # PHASE 2 : ÉDITIONS RÉGIONALES — 1 article par région (avec region_id)
        # ════════════════════════════════════════════════════════════
        for region_name in SOURCES_BY_REGION.keys():
            try:
                items = await scrape_by_region(region_name, max_articles=3)
                fresh = [n for n in items if n["title"] not in seen_titles]
                if not fresh:
                    logger.info(f"[REG][{region_name}] aucun article frais")
                    continue
                target = fresh[0]
                # Force le tag région (au cas où le scraper ne l'a pas mis)
                target["region"] = region_name
                seen_titles.add(target["title"])
                if await _process_one_article(target, run_id, data, producers, validator, verifier, label=f"REG[{region_name}]"):
                    approved_reg += 1
            except Exception as e:
                logger.warning(f"[REG][{region_name}] erreur : {e}")

        RUNS[run_id]["status"] = "completed"
        RUNS[run_id]["approved_national"] = approved_nat
        RUNS[run_id]["approved_regional"] = approved_reg
        logger.info(
            f"Pipeline {run_id} terminé — édition nationale : {approved_nat} articles, "
            f"éditions régionales : {approved_reg} articles"
        )

    except Exception as e:
        RUNS[run_id]["status"] = "error"
        RUNS[run_id]["error"] = str(e)
        logger.error(f"[PIPELINE] {e}")


@router.get("/status/{run_id}")
async def pipeline_status(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(404, "Run introuvable")
    run = RUNS[run_id]
    return {
        "run_id": run_id,
        "status": run["status"],
        "articles": len(run.get("content", [])),
        "rejected_count": len(run.get("rejected", [])),
        "rejected": run.get("rejected", []),
        "content": run.get("content", [])
    }
