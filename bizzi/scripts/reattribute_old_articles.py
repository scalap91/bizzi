import os
"""
Réattribue les anciens articles Onyx (sans agent_id) à un journaliste,
et leur fait remanier l'article dans son style perso.

Garde IDENTIQUES : id, slug, title, image_url, category_id, region_id, status,
                   created_at, published_at.
Met à jour : agent_id, content_html, content_raw, word_count, updated_at.

Usage :
    python3 reattribute_old_articles.py --limit 10
    python3 reattribute_old_articles.py --limit 10 --dry-run
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, '/opt/bizzi/bizzi')

from sqlalchemy import create_engine, text
from moteur.team_loader import load_team

DB = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))

# Mapping catégorie → slug journaliste (priorité 1, sinon fallback specialty matching)
CATEGORY_MAP = {
    "Sport": "sophie-durand", "Sante": "sophie-durand", "Santé": "sophie-durand",
    "Economie": "julie-moreau", "Économie": "julie-moreau", "Finance": "julie-moreau",
    "Politique": "claire-bernard", "Societe": "claire-bernard", "Société": "claire-bernard",
    "Une": "claire-bernard", "Monde": "claire-bernard", "International": "claire-bernard",
    "Culture": "emma-rousseau", "Arts": "emma-rousseau",
    "Tech": "thomas-levy", "Numerique": "thomas-levy", "Numérique": "thomas-levy",
    "Buzz": "thomas-levy", "Viral": "thomas-levy",
    "Environnement": "marc-fontaine", "Faits divers": "marc-fontaine",
    "Investigation": "marc-fontaine", "Reportage": "marc-fontaine",
}


def _match_journalist(producers, title, category):
    """Match : (1) mapping catégorie, (2) specialty matching, (3) fallback premier producer."""
    if category in CATEGORY_MAP:
        slug = CATEGORY_MAP[category]
        j = next((p for p in producers if p.slug == slug), None)
        if j:
            return j
    haystack = (title + " " + (category or "")).lower()
    for p in producers:
        spec_words = [w.lower() for w in (p.specialty or "").split() if len(w) >= 4]
        if any(w in haystack for w in spec_words):
            return p
    return producers[0] if producers else None


async def reattribute_one(article, journalist, dry_run=False):
    aid, title, category, content_raw, _slug = article
    if dry_run:
        return {"id": aid, "journalist": journalist.slug, "status": "dry-run"}

    ctx = (
        "REMANIE cet article publié précédemment, avec TON style perso et ta personnalité. "
        "Garde STRICTEMENT : les faits, l'angle, les sources nommées, les dates et chiffres exacts. "
        "Change : le ton, les formulations, la structure des phrases, l'enchaînement. "
        "Ne signe pas l'article (pas de '*Article signé par...*').\n\n"
        f"TITRE : {title}\n"
        f"CATÉGORIE : {category or 'Une'}\n"
        f"ARTICLE ORIGINAL :\n{(content_raw or '')[:3000]}"
    )
    result = await journalist.produce(topic=title, context=ctx)
    new_content = (result or {}).get("content", "")
    if not new_content:
        return {"id": aid, "journalist": journalist.slug, "status": "error", "reason": "produce vide"}

    with DB.connect() as conn:
        ag = conn.execute(
            text("SELECT id FROM agents WHERE tenant_id=1 AND slug=:s LIMIT 1"),
            {"s": journalist.slug}
        ).fetchone()
        if not ag:
            return {"id": aid, "journalist": journalist.slug, "status": "error", "reason": "agent introuvable"}

        conn.execute(
            text("""
                UPDATE productions
                SET agent_id    = :aid,
                    content_html= :html,
                    content_raw = :raw,
                    word_count  = :wc,
                    updated_at  = now()
                WHERE id = :pid AND tenant_id = 1 AND agent_id IS NULL
            """),
            {"aid": ag[0], "html": new_content, "raw": new_content,
             "wc": len(new_content.split()), "pid": aid}
        )
        conn.commit()
    return {"id": aid, "journalist": journalist.slug, "status": "ok",
            "wc": len(new_content.split())}


async def main(limit, dry_run):
    config, team = load_team("onyx")
    producers = [a for a in team if a.role == "production" and a.status == "active"]
    print(f"=== Réattribution articles Onyx ===")
    print(f"Équipe : {len(producers)} producteurs ({', '.join(p.slug for p in producers)})")
    print(f"Mode   : {'DRY-RUN' if dry_run else 'WRITE'} · limit={limit}\n")

    with DB.connect() as conn:
        total_remaining = conn.execute(text("""
            SELECT COUNT(*) FROM productions
            WHERE tenant_id = 1 AND agent_id IS NULL
              AND status = 'published' AND content_raw IS NOT NULL
        """)).scalar()
        rows = conn.execute(text("""
            SELECT p.id, p.title, c.name AS category, p.content_raw, p.slug
            FROM productions p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.tenant_id = 1 AND p.agent_id IS NULL
              AND p.status = 'published' AND p.content_raw IS NOT NULL
            ORDER BY p.created_at DESC
            LIMIT :n
        """), {"n": limit}).fetchall()
    print(f"Articles restants à attribuer : {total_remaining}")
    print(f"Ce run : {len(rows)} articles\n")

    started = time.time()
    ok = err = 0
    for i, art in enumerate(rows, 1):
        aid, title, category, _, _ = art
        journalist = _match_journalist(producers, title or "", category or "Une")
        try:
            r = await reattribute_one(art, journalist, dry_run=dry_run)
            status_icon = "✓" if r.get("status") in ("ok", "dry-run") else "✗"
            print(f"  {status_icon} [{i:3d}/{len(rows)}] #{aid} cat={(category or 'Une'):14s} → {journalist.slug:18s} {r.get('status','?'):8s} wc={r.get('wc','-')}")
            if r.get("status") == "ok":
                ok += 1
            elif r.get("status") == "dry-run":
                ok += 1
            else:
                err += 1
                print(f"           reason: {r.get('reason','')}")
        except Exception as e:
            err += 1
            print(f"  ✗ [{i:3d}/{len(rows)}] #{aid} EXCEPTION: {e}")

    elapsed = time.time() - started
    print(f"\n=== Bilan : {ok} ok · {err} erreurs · {elapsed:.0f}s ({elapsed/max(len(rows),1):.1f}s/article) ===")
    if total_remaining > len(rows):
        print(f"Encore {total_remaining - ok} articles à traiter pour finir.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.dry_run))
