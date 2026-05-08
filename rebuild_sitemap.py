import os
import psycopg2
from datetime import datetime

conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))
cur = conn.cursor()
cur.execute("""
    SELECT slug, created_at FROM productions
    WHERE status='published' AND slug IS NOT NULL
    ORDER BY created_at DESC LIMIT 500
""")
rows = cur.fetchall()
conn.close()

lines = ['<?xml version="1.0" encoding="UTF-8"?>']
lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
lines.append(f"""  <url>
    <loc>https://onyx-infos.fr/</loc>
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
    <lastmod>{datetime.utcnow().strftime("%Y-%m-%d")}</lastmod>
  </url>""")
lines.append("""  <url>
    <loc>https://onyx-infos.fr/onyx-archives.html</loc>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

for slug, created_at in rows:
    lastmod = created_at.strftime("%Y-%m-%d") if created_at else "2026-04-25"
    lines.append(f"""  <url>
    <loc>https://onyx-infos.fr/article/{slug}</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
    <lastmod>{lastmod}</lastmod>
  </url>""")

lines.append('</urlset>')
with open("/opt/onyx-infos/public/sitemap.xml", "w") as f:
    f.write("\n".join(lines))
print(f"{datetime.utcnow()} - Sitemap: {len(rows)} articles")
