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

SOURCES_BY_REGION = {
    "Ile-de-France": [
        {"name":"Le Monde","url":"https://www.lemonde.fr/rss/une.xml"},
    ],
    "PACA": [
        {"name":"France Bleu Provence","url":"https://www.francebleu.fr/rss/provence/"},
        {"name":"Var Matin","url":"https://www.varmatin.com/rss.xml"},
    ],
    "Bretagne": [
        {"name":"France Bleu Bretagne","url":"https://www.francebleu.fr/rss/breizh-izel/"},
        {"name":"France Bleu Armorique","url":"https://www.francebleu.fr/rss/armorique/"},
    ],
    "Occitanie": [
        {"name":"Midi Libre","url":"https://www.midilibre.fr/rss.xml"},
        {"name":"France Bleu Toulouse","url":"https://www.francebleu.fr/rss/toulouse/"},
    ],
    "Grand-Est": [
        {"name":"France Bleu Alsace","url":"https://www.francebleu.fr/rss/alsace/"},
    ],
    "Nouvelle-Aquitaine": [
        {"name":"Sud Ouest","url":"https://www.sudouest.fr/rss.xml"},
        {"name":"France Bleu Gironde","url":"https://www.francebleu.fr/rss/gironde/"},
    ],
    "Pays-de-la-Loire": [
        {"name":"France Bleu Loire Ocean","url":"https://www.francebleu.fr/rss/loire-ocean/"},
    ],
    "Hauts-de-France": [
        {"name":"France Bleu Nord","url":"https://www.francebleu.fr/rss/nord/"},
    ],
    "DOM-TOM": [
        {"name":"France Bleu Reunion","url":"https://www.francebleu.fr/rss/pays-de-savoie/"},
    ],
}
