import os
import psycopg2
import asyncio
from openai import AsyncOpenAI

key = open("/opt/bizzi/bizzi/.env").read().split("OPENAI_API_KEY=")[1].split("\n")[0].strip()
client = AsyncOpenAI(api_key=key)
conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))

async def rewrite(id, title, content):
    prompt = f"""Tu es journaliste de presse française. Réécris cet article dans un style humain, direct, jamais scolaire.

TITRE : {title}

CONTENU ORIGINAL :
{content[:3000]}

REGLES STRICTES :
- 450 à 600 mots
- Mélange phrases courtes (max 10 mots) et longues (20-25 mots)
- JAMAIS : "crucial", "majeur", "vertigineux", "à l'heure où", "en outre", "par ailleurs", "en parallèle", "force est de constater", "les prochaines semaines seront"
- Max 3 intertitres ## par article
- Une seule citation entre guillemets maximum
- Dernier paragraphe : fait concret ou date précise, pas une question rhétorique
- INTERDIT de mentionner une date antérieure à 2026 sauf si clairement historique
- Retourne UNIQUEMENT le texte de l'article, sans introduction ni commentaire"""

    try:
        r = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.8
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"Erreur article {id}: {e}")
        return None

async def main():
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, content_raw FROM productions
        WHERE status='published' AND slug IS NOT NULL
        AND content_raw IS NOT NULL AND content_raw != ''
        ORDER BY id DESC
    """)
    articles = cur.fetchall()
    print(f"📝 {len(articles)} articles à réécrire...")
    for i, (id, title, content) in enumerate(articles):
        new_content = await rewrite(id, title, content)
        if new_content:
            cur.execute("UPDATE productions SET content_raw=%s, updated_at=NOW() WHERE id=%s", (new_content, id))
            conn.commit()
            print(f"✅ [{i+1}/{len(articles)}] {title[:50]}")
        await asyncio.sleep(0.5)
    conn.close()
    print("🎉 Réécriture terminée")

asyncio.run(main())
