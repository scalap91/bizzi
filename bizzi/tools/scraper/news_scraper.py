"""tools/scraper/news_scraper.py"""
import httpx, logging
from bs4 import BeautifulSoup

logger = logging.getLogger("bizzi.scraper")

SOURCES_BY_CAT = {
    "Politique": [
        {"name":"Vie Publique","url":"https://www.vie-publique.fr/rss.xml"},
        {"name":"Gouvernement","url":"https://www.gouvernement.fr/partage/feed"},
    ],
    "Economie": [
        {"name":"BFM Business","url":"https://www.bfmtv.com/rss/economie/"},
        {"name":"France Info Eco","url":"https://www.francetvinfo.fr/economie.rss"},
    ],
    "Monde": [
        {"name":"RFI","url":"https://www.rfi.fr/fr/rss"},
        {"name":"Le Figaro","url":"https://www.lefigaro.fr/rss/figaro_actualites.xml"},
    ],
    "Sport": [
        {"name":"RMC Sport","url":"https://rmcsport.bfmtv.com/rss/football/"},
    ],
    "Culture": [
        {"name":"Le Monde Culture","url":"https://www.lemonde.fr/culture/rss_full.xml"},
        {"name":"Ouest France","url":"https://www.ouest-france.fr/rss-en-continu.xml"},
    ],
    "Tech": [
        {"name":"01net","url":"https://www.01net.com/feed/"},
    ],
    "Sante": [
        {"name":"Le Monde Sante","url":"https://www.lemonde.fr/sante/rss_full.xml"},
    ],
    "Societe": [
        {"name":"Liberation","url":"https://www.liberation.fr/arc/outboundfeeds/rss/"},
    ],
    "Environnement": [
        {"name":"Reporterre","url":"https://reporterre.net/spip.php?page=backend"},
    ],
}

SOURCES_UNE = [
    {"name":"Le Figaro","url":"https://www.lefigaro.fr/rss/figaro_actualites.xml"},
    {"name":"France Info","url":"https://www.francetvinfo.fr/titres.rss"},
]

async def fetch_article_content(url, c):
    try:
        r = await c.get(url, headers={"User-Agent":"Mozilla/5.0"}, follow_redirects=True, timeout=10.0)
        if r.status_code != 200: return ""
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script","style","nav","header","footer","aside","form","iframe"]):
            tag.decompose()
        for selector in ["article",".article-content","[itemprop=articleBody]",".content","main"]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 200:
                    return text[:3000]
        return ""
    except:
        return ""

async def scrape_news(max_articles=6, category=None):
    articles = []
    sources = SOURCES_BY_CAT.get(category, []) if category else SOURCES_UNE
    if not sources:
        sources = SOURCES_UNE
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent":"Mozilla/5.0"}, follow_redirects=True) as c:
        for source in sources:
            try:
                r = await c.get(source["url"])
                if r.status_code != 200: continue
                soup = BeautifulSoup(r.text, "lxml-xml")
                items = soup.find_all("item")[:3]
                for item in items:
                    title = item.find("title")
                    link  = item.find("link")
                    desc  = item.find("description")
                    image = item.find("enclosure") or item.find("media:content")
                    url   = link.text.strip() if link else ""
                    full_content = await fetch_article_content(url, c) if url else ""
                    if not full_content and desc:
                        full_content = BeautifulSoup(desc.text,"html.parser").get_text()[:2000]
                    if not title or not title.text.strip(): continue
                    # Filtre 24h
                    pub = item.find("pubDate")
                    if pub and pub.text:
                        try:
                            pub_dt = parsedate_to_datetime(pub.text.strip()).replace(tzinfo=None)
                            if (datetime.utcnow() - pub_dt).total_seconds() > 86400:
                                continue
                        except Exception:
                            pass
                    articles.append({
                        "source": source["name"],
                        "title": title.text.strip(),
                        "desc": full_content[:500],
                        "full_content": full_content,
                        "link": url,
                        "image": image.get("url","") if image else "",
                        "category": category or "Une",
                    })
                    logger.info(f"[SCRAPER] {source['name']}: {title.text.strip()[:60]}")
                    if len(articles) >= max_articles: break
            except Exception as e:
                logger.warning(f"[SCRAPER] {source['name']} erreur: {e}")
    return articles

CATEGORIES = list(SOURCES_BY_CAT.keys())

# 14 régions françaises. Mix PQR (Var Matin, Midi Libre, Sud Ouest) quand elle est
# accessible (les flux France Bleu sont morts depuis le rebrand "Ici" 2025), et
# Google News RSS par région en complément/fallback.
_GN = "https://news.google.com/rss/search"
SOURCES_BY_REGION = {
    "Ile-de-France": [
        {"name": "Google News Ile-de-France", "url": f"{_GN}?q=ile-de-france+paris+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "PACA": [
        {"name": "Var Matin",        "url": "https://www.varmatin.com/rss.xml"},
        {"name": "Google News PACA", "url": f"{_GN}?q=marseille+nice+toulon+provence+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Bretagne": [
        {"name": "Google News Bretagne", "url": f"{_GN}?q=bretagne+rennes+brest+quimper+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Occitanie": [
        {"name": "Midi Libre",            "url": "https://www.midilibre.fr/rss.xml"},
        {"name": "Google News Occitanie", "url": f"{_GN}?q=occitanie+toulouse+montpellier+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Grand-Est": [
        {"name": "Google News Grand-Est", "url": f"{_GN}?q=grand+est+strasbourg+nancy+metz+reims+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Nouvelle-Aquitaine": [
        {"name": "Sud Ouest",                     "url": "https://www.sudouest.fr/rss.xml"},
        {"name": "Google News Nouvelle-Aquitaine","url": f"{_GN}?q=nouvelle+aquitaine+bordeaux+pau+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Pays-de-la-Loire": [
        {"name": "Google News Pays-de-la-Loire", "url": f"{_GN}?q=nantes+angers+le+mans+pays+de+la+loire+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Hauts-de-France": [
        {"name": "Google News Hauts-de-France", "url": f"{_GN}?q=lille+amiens+hauts+de+france+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Auvergne-Rhône-Alpes": [
        {"name": "Google News Auvergne-Rhône-Alpes", "url": f"{_GN}?q=lyon+grenoble+saint-etienne+auvergne+rhone+alpes+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Bourgogne-Franche-Comté": [
        {"name": "Google News Bourgogne-Franche-Comté", "url": f"{_GN}?q=dijon+besancon+bourgogne+franche+comte+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Centre-Val de Loire": [
        {"name": "Google News Centre-Val de Loire", "url": f"{_GN}?q=orleans+tours+bourges+centre+val+de+loire+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Normandie": [
        {"name": "Google News Normandie", "url": f"{_GN}?q=rouen+caen+le+havre+normandie+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "Corse": [
        {"name": "Google News Corse", "url": f"{_GN}?q=ajaccio+bastia+corse+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
    "DOM-TOM": [
        {"name": "Google News DOM-TOM", "url": f"{_GN}?q=outre+mer+reunion+martinique+guadeloupe+mayotte+actualite&hl=fr&gl=FR&ceid=FR:fr"},
    ],
}


async def scrape_by_region(region_name, max_articles=3):
    sources = SOURCES_BY_REGION.get(region_name, [])
    articles = []
    async with httpx.AsyncClient(timeout=15) as c:
        for source in sources:
            try:
                r = await c.get(source["url"], headers={"User-Agent": "Mozilla/5.0"})
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:max_articles]:
                    title = item.find("title")
                    desc = item.find("description")
                    image = item.find("enclosure") or item.find("media:content")
                    pub = item.find("pubDate")
                    if pub and pub.text:
                        try:
                            from email.utils import parsedate_to_datetime
                            pub_dt = parsedate_to_datetime(pub.text.strip()).replace(tzinfo=None)
                            if (datetime.utcnow() - pub_dt).total_seconds() > 86400:
                                continue
                        except Exception:
                            pass
                    if not title or not title.text.strip():
                        continue
                    full_content = BeautifulSoup(desc.text, "html.parser").get_text()[:2000] if desc else ""
                    articles.append({
                        "source": source["name"],
                        "title": title.text.strip(),
                        "desc": full_content[:500],
                        "full_content": full_content,
                        "image": image.get("url", "") if image else "",
                        "category": "Une",
                        "region": region_name,
                    })
            except Exception as e:
                logger.warning(f"[SCRAPER REGION] {source['name']}: {e}")
    return articles
