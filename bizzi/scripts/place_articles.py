import os
"""
Le Placier (Nathan LEROY) relit chaque article Onyx publié et décide de sa place
(région française ou null). Met à jour region_id en DB.

Usage :
    python3 place_articles.py            # tous
    python3 place_articles.py --limit 50
    python3 place_articles.py --dry-run
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, '/opt/bizzi/bizzi')

from sqlalchemy import create_engine, text
from moteur.team_loader import load_team

DB = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))


async def main(limit, dry_run):
    config, team = load_team("onyx")
    placier = next((a for a in team if a.role == "placement" and a.status == "active"), None)
    if not placier:
        print("ERREUR : aucun placier (role=placement) actif dans l'équipe")
        return

    with DB.connect() as conn:
        regions = [r[1] for r in conn.execute(
            text("SELECT id, name FROM regions WHERE tenant_id=1 ORDER BY name")
        ).fetchall()]
        region_id_by_name = {r[1]: r[0] for r in conn.execute(
            text("SELECT id, name FROM regions WHERE tenant_id=1")
        ).fetchall()}

    print(f"=== Placier : {placier.name} ===")
    print(f"Régions valides ({len(regions)}) : {', '.join(regions)}")
    print(f"Mode : {'DRY-RUN' if dry_run else 'WRITE'}\n")

    with DB.connect() as conn:
        sql = """
            SELECT p.id, p.title, p.content_raw, c.name AS category
            FROM productions p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.tenant_id = 1 AND p.status='published' AND p.region_id IS NULL
              AND p.content_raw IS NOT NULL
            ORDER BY p.created_at DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(text(sql)).fetchall()

    print(f"{len(rows)} articles à placer\n")

    started = time.time()
    by_region = {}
    none_count = 0
    err_count = 0
    for i, row in enumerate(rows, 1):
        aid, title, content, category = row
        try:
            result = await placier.place(title or "", content or "", category or "Une", regions)
        except Exception as e:
            err_count += 1
            print(f"  ✗ [{i:4d}/{len(rows)}] #{aid} EXCEPTION : {e}")
            continue
        region = result.get("region")
        conf = result.get("confidence", 0)
        if region:
            by_region[region] = by_region.get(region, 0) + 1
            if not dry_run:
                with DB.connect() as conn:
                    conn.execute(
                        text("UPDATE productions SET region_id = :rid, updated_at = now() "
                             "WHERE id = :aid AND tenant_id = 1 AND region_id IS NULL"),
                        {"rid": region_id_by_name[region], "aid": aid}
                    )
                    conn.commit()
            mark = "→"
        else:
            none_count += 1
            mark = "·"
        if i % 25 == 0 or i == len(rows):
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(rows) - i) / rate if rate > 0 else 0
            print(f"  [{i:4d}/{len(rows)}] {mark} #{aid} {(title or '')[:50]:55s} → {region or 'null':22s} ({conf}%)  · {elapsed:.0f}s · ETA {eta:.0f}s")

    elapsed = time.time() - started
    print(f"\n=== Bilan : {elapsed:.0f}s ({elapsed/max(len(rows),1):.1f}s/article) ===")
    print(f"  Placés         : {sum(by_region.values())}")
    print(f"  Sans région    : {none_count}")
    print(f"  Erreurs        : {err_count}")
    print(f"\n=== Distribution ===")
    for r, n in sorted(by_region.items(), key=lambda x: -x[1]):
        print(f"  {r:25s} {n}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="0 = tous")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.dry_run))
