
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
    "Le Monde": "Ile-de-France",
}

def detect_region(source_name):
    for key, region in SOURCES_TO_REGION.items():
        if key.lower() in source_name.lower():
            return region
    return None


def similarity_score(text1, text2):
    """Retourne un score de similarite entre 0 et 1"""
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
    """Retourne True si le contenu est trop similaire a la source"""
    return similarity_score(generated, source) > threshold

def is_too_short(content, min_words=150):
    """Retourne True si l article est trop court"""
    return len(content.split()) < min_words


import re as _re
import unicodedata as _ud

def slugify(s):
    s = s.lower()
    s = _ud.normalize("NFD", s)
    s = _re.sub(r"[\u0300-\u036f]", "", s)
    s = _re.sub(r"[^a-z0-9\s-]", "", s)
    s = _re.sub(r"\s+", "-", s.strip())
    return s[:80].strip("-")

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio, httpx, logging, os, uuid
from datetime import datetime
from sqlalchemy import create_engine, text
from openai import AsyncOpenAI
import sys
sys.path.insert(0, '/opt/bizzi/bizzi')
from tools.scraper.news_scraper import scrape_news, CATEGORIES

router = APIRouter()
logger = logging.getLogger("api.pipeline")
RUNS = {}
engine = create_engine("postgresql://bizzi_admin:Bizzi2026x@localhost/bizzi")
openai_client = AsyncOpenAI(api_key=open("/opt/bizzi/bizzi/.env").read().split("OPENAI_API_KEY=")[1].split("\n")[0].strip())

def save_to_db(run_id, tenant, topic, cnt, image_url="", category="Une"):
    try:
        with engine.connect() as conn:
            base_slug = slugify(topic)
            slug = base_slug
            n = 1
            while True:
                exists = conn.execute(text("SELECT id FROM bizzi_articles WHERE slug=:s"), {"s":slug}).fetchone()
                if not exists:
                    break
                slug = base_slug + "-" + str(n)
                n += 1
            conn.execute(text("INSERT INTO bizzi_articles (run_id,tenant,topic,content,created_at,status,image_url,category,slug,region) VALUES (:run_id,:tenant,:topic,:content,:now,'published',:img,:cat,:slug,:region)"),
                {"run_id":run_id,"tenant":tenant,"topic":topic,"content":cnt,"now":datetime.utcnow(),"img":image_url,"cat":category,"slug":slug,"region":detect_region(category)})
            conn.commit()
    except Exception as e:
        logger.error(f"DB: {e}")

async def download_image(url, tenant):
    if not url: return ""
    try:
        save_dir = f"/opt/{tenant}/public/img"
        os.makedirs(save_dir, exist_ok=True)
        ext = url.split(".")[-1].split("?")[0][:4] or "jpg"
        filename = f"{uuid.uuid4().hex[:8]}.{ext}"
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                with open(f"{save_dir}/{filename}", "wb") as f:
                    f.write(r.content)
                return f"/img/{filename}"
    except: pass
    return ""

class PipelineRequest(BaseModel):
    tenant: str
    domain: str = "media"
    topics: Optional[list] = None
    category: Optional[str] = None

@router.post("/run")
async def run_pipeline(data: PipelineRequest):
    run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    RUNS[run_id] = {"status":"started","tenant":data.tenant,"content":[]}
    asyncio.create_task(execute_pipeline(run_id, data))
    return {"status":"started","run_id":run_id,"domain":data.domain,"check":f"/api/pipeline/status/{run_id}"}

async def execute_pipeline(run_id, data):
    try:
        RUNS[run_id]["status"] = "running"
        # Recuperer articles deja en base
        with engine.connect() as conn:
            existing = [r[0] for r in conn.execute(text("SELECT topic FROM bizzi_articles ORDER BY created_at DESC LIMIT 100")).fetchall()]
        # Scraper toutes les categories + une
        all_news = []
        cats = [None] + CATEGORIES  # None = A la une
        for cat in cats:
            news = await scrape_news(max_articles=3, category=cat)
            all_news.extend(news)
        # Filtrer les doublons
        news_new = [n for n in all_news if n["title"] not in existing]
        if not news_new:
            logger.info("Tous les articles sont deja en base")
            RUNS[run_id]["status"] = "completed"
            return
        # Generer max 6 articles par run
        for item in news_new[:6]:
            cat = item.get("category","Une")
            prompt = f"""Tu es journaliste pour {data.tenant}, rubrique {cat}.
Redige un article complet en francais base sur :

SOURCE: {item["source"]}
TITRE: {item["title"]}
CONTENU: {item.get("full_content", item.get("desc",""))[:2000]}

Format:
# [TITRE]

[CHAPEAU 2 phrases]

## Introduction
[contexte]

## Analyse
[3 paragraphes]

## Chiffres cles
- [donnee 1]
- [donnee 2]

## Conclusion
[synthese]"""
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":prompt}],
                max_tokens=1000,
                temperature=0.7
            )
            cnt = response.choices[0].message.content.strip()
            local_img = await download_image(item.get("image",""), data.tenant)
            RUNS[run_id]["content"].append({"topic":item["title"],"content":cnt,"image":local_img,"category":cat,"source":item["source"],"created_at":datetime.utcnow().isoformat()})
            # Controle qualite
            if is_too_short(cnt, min_words=150):
                logger.warning(f"Article trop court, ignore: {item['title'][:50]}")
                continue
            if is_too_similar(cnt, item.get("description","") + " " + item.get("title",""), threshold=0.6):
                logger.warning(f"Article trop similaire a la source, ignore: {item['title'][:50]}")
                continue
            save_to_db(run_id, data.tenant, item["title"], cnt, local_img, item.get("category", cat))
            logger.info(f"[{cat}] Article: {item['title'][:50]}")
        RUNS[run_id]["status"] = "completed"
        logger.info(f"Pipeline {run_id} termine — {len(RUNS[run_id]['content'])} articles")
    except Exception as e:
        RUNS[run_id]["status"] = "error"
        RUNS[run_id]["error"] = str(e)
        logger.error(f"Error: {e}")

@router.get("/status/{run_id}")
async def pipeline_status(run_id: str):
    if run_id not in RUNS: raise HTTPException(404,"Run introuvable")
    run = RUNS[run_id]
    return {"run_id":run_id,"status":run["status"],"articles":len(run.get("content",[])),"content":run.get("content",[])}
