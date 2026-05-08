"""scripts/lesdemocrates_articles_recent.py — Use case démo Phase 0.

Démontre l'usage de bizzi.data sur le tenant `lesdemocrates` :
  1. Lit le schéma sémantique tenant (data_sources + entities + views)
  2. Liste les vues disponibles
  3. Exécute la vue `articles_recent` (Postgres bizzi DB, table bizzi_articles)
  4. Affiche le résultat

Usage :
    /opt/bizzi/bizzi/venv/bin/python -m data.scripts.lesdemocrates_articles_recent

Note : le tenant `lesdemocrates` n'a pas (encore) d'articles dans la DB
production — le script tombera sur 0 row, c'est attendu. Le tenant
`onyx-infos` a 176 articles ; passer TENANT=onyx-infos pour tester.
"""
from __future__ import annotations

import json
import os
import sys

# Bootstrap PYTHONPATH (script lancé depuis /opt/bizzi)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

from data import semantic, views  # noqa: E402


def main() -> int:
    tenant = os.environ.get("TENANT", "lesdemocrates")
    print(f"== Tenant : {tenant} ==\n")

    schema = semantic.load_data_config(tenant)
    print(f"Sources déclarées : {list(schema.sources)}")
    print(f"Entités  : {list(schema.entities)}")
    print(f"Vues     : {list(schema.views)}\n")

    if "articles_recent" not in schema.views:
        print("✗ vue 'articles_recent' absente du YAML — skip exécution")
        return 1

    print("→ execute_view('articles_recent', tenant_slug=…) :\n")
    rows = views.execute_view(tenant, "articles_recent", {"tenant_slug": tenant, "limit": 5})
    print(json.dumps(rows, indent=2, default=str, ensure_ascii=False))
    print(f"\n{len(rows)} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
