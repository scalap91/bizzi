import os
import urllib.request, json, psycopg2, re
from datetime import datetime
import openai

key = open("/opt/bizzi/bizzi/.env").read()
api_key = key.split("OPENAI_API_KEY=")[1].split("\n")[0].strip()
client = openai.OpenAI(api_key=api_key)
DB = os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi")
TENANT_ID = 2
UNSPLASH_KEY = "neho7JbRM-dR6_ZnJNukjZiIu_0fcaK-CdH7QOFNo2U"
CATS = {"dpe":14,"amiante":15,"plomb":16,"certifications":17,"jurisprudence":18,"conseils":19,"grand-public":20,"reglementation":13}

def slugify(t):
    t = t.lower()
    for a,b in [("e\u0301","e"),("\xe9","e"),("\xe8","e"),("\xea","e"),("\xe0","a"),("\xf9","u"),("\xf4","o"),("\xee","i"),("\xe7","c"),("\u2019",""),("\u2018",""),("'"," ")]:
        t = t.replace(a,b)
    t = re.sub(r"[^a-z0-9\s-]","",t)
    t = re.sub(r"\s+","-",t.strip())
    t = re.sub(r"-+","-",t)
    return t[:80].strip("-")

def get_image(keywords):
    try:
        q = urllib.parse.quote(keywords)
        url = f"https://api.unsplash.com/search/photos?query={q}&per_page=1&client_id={UNSPLASH_KEY}"
        r = urllib.request.urlopen(url, timeout=10)
        data = json.loads(r.read())
        if data.get("results"):
            return data["results"][0]["urls"]["regular"]
    except: pass
    return ""

def fetch_sources():
    sources = []
    for tag in ["energie","logement","batiment","environnement"]:
        try:
            r = urllib.request.urlopen(f"https://www.data.gouv.fr/api/1/datasets/?tag={tag}&page_size=6&sort=-created", timeout=10)
            for x in json.loads(r.read())["data"]:
                sources.append(x["title"])
        except: pass
    return sources[:15]

def generer(sources):
    ctx = "\n".join(f"- {s}" for s in sources)
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":f"""Tu es journaliste SEO expert en diagnostic immobilier pour Le Diagnostiqueur.
Voici des actualites recentes du secteur immobilier et energetique :
{ctx}

Genere UN article SEO-optimise de 600 mots pour diagnostiqueurs immobiliers certifies.
Date : {datetime.now().strftime("%d %B %Y")}

Regles SEO :
- Titre H1 accrocheur avec mot-cle principal (DPE, amiante, plomb, certification...)
- Introduction avec le mot-cle en premier paragraphe
- Sous-titres H2 avec mots-cles secondaires
- Conclusion avec appel a l action
- Vocabulaire technique exact : DPE, CREP, RAAT, COFRAC, norme NF
- Longueur : exactement 600 mots

Reponds UNIQUEMENT en JSON valide sans markdown :
{{
  "titre": "Titre SEO optimise avec mot-cle principal",
  "meta_title": "Titre meta 60 chars max",
  "meta_description": "Description meta 155 chars max avec mot-cle et appel action",
  "slug": "url-slug-seo-optimise",
  "categorie": "dpe|amiante|plomb|certifications|jurisprudence|conseils|reglementation",
  "image_keywords": "3 mots cles anglais pour photo Pexels",
  "contenu": "Article HTML complet avec balises h2 et p"
}}"""}],
        max_tokens=1500
    )
    text = r.choices[0].message.content.strip()
    text = re.sub(r"^```json","",text).strip()
    text = re.sub(r"```$","",text).strip()
    return json.loads(text)

import urllib.parse

def sauvegarder(data, image_url):
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    slug = data.get("slug") or slugify(data["titre"])
    slug = slug + "-" + datetime.now().strftime("%Y%m%d")
    cat_id = CATS.get(data.get("categorie","reglementation"), 13)
    
    cur.execute("""
        INSERT INTO productions (
            tenant_id, title, content_html, slug, status,
            category_id, image_url, created_at,
            meta_title, meta_description
        ) VALUES (%s,%s,%s,%s,'published',%s,%s,NOW(),%s,%s)
        ON CONFLICT (slug) DO NOTHING RETURNING id
    """, (
        TENANT_ID,
        data["titre"],
        data["contenu"],
        slug,
        cat_id,
        image_url,
        data.get("meta_title", data["titre"][:60]),
        data.get("meta_description", "")
    ))
    result = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return result, slug

print("Recuperation sources data.gouv.fr...")
sources = fetch_sources()
print(f"{len(sources)} sources")
print("Generation GPT-4o-mini...")
data = generer(sources)
print(f"Titre    : {data['titre']}")
print(f"Slug     : {data.get('slug')}")
print(f"Meta     : {data.get('meta_description','')[:80]}...")
print("Recherche image Pexels...")
image_url = get_image(data.get("image_keywords","building inspection france"))
print(f"Image    : {image_url[:60] if image_url else 'aucune'}...")
result, slug = sauvegarder(data, image_url)
print(f"OK - ID {result[0]} - slug: {slug}" if result else "Deja existant")
