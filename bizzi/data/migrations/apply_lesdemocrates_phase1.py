"""bizzi.data — Applique le patch YAML Phase 1 à lesdemocrates.yaml.

Idempotent : ne ré-écrit pas les sections déjà patchées.

Usage :
    sudo /opt/bizzi/bizzi/venv/bin/python \
        /opt/bizzi/bizzi/data/migrations/apply_lesdemocrates_phase1.py
"""
from __future__ import annotations

import datetime
import shutil
import sys
from pathlib import Path

import yaml


YAML_PATH  = Path("/opt/bizzi/bizzi/domains/lesdemocrates.yaml")
PATCH_PATH = Path(__file__).resolve().parent / "003_lesdemocrates_phase1.yaml.patch"


def main() -> int:
    if not YAML_PATH.exists() or not PATCH_PATH.exists():
        print("✗ fichiers introuvables", file=sys.stderr)
        return 1

    with open(YAML_PATH, encoding="utf-8") as f:
        existing = yaml.safe_load(f) or {}

    # Vérifier que Phase 0 a été appliqué d'abord
    if "data_sources" not in existing or "semantic_schema" not in existing:
        print("✗ Phase 0 (data_sources/semantic_schema) pas appliqué — "
              "exécuter d'abord apply_lesdemocrates_data.py", file=sys.stderr)
        return 1

    # Idempotence : on cherche les NOUVELLES entités/views — on n'écrit que
    # celles qui ne sont pas déjà présentes.
    with open(PATCH_PATH, encoding="utf-8") as f:
        patch = yaml.safe_load(f) or {}

    new_entities = patch.get("semantic_schema_additions") or {}
    new_views    = patch.get("semantic_views_additions") or {}
    new_routes   = patch.get("events_routes") or []

    existing_entities = existing.get("semantic_schema") or {}
    existing_views    = existing.get("semantic_views")  or {}

    entities_to_add = {k: v for k, v in new_entities.items() if k not in existing_entities}
    views_to_add    = {k: v for k, v in new_views.items()    if k not in existing_views}
    routes_to_add   = new_routes if "events_routes" not in existing else []

    if not entities_to_add and not views_to_add and not routes_to_add:
        print("⚠ rien à ajouter (toutes les sections déjà présentes)")
        return 0

    # Backup
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = YAML_PATH.with_suffix(f".yaml.bak-pre-phase1-{ts}")
    shutil.copy2(YAML_PATH, backup)
    print(f"→ backup : {backup}")

    # Append en YAML formaté.
    blocks: list[str] = ["\n\n# ══════════════════════════════════════════════════════════════"]
    blocks.append("# DATA — Phase 1 (audience/phone/social entities + views + events_routes)")
    blocks.append("# ══════════════════════════════════════════════════════════════\n")

    if entities_to_add:
        blocks.append("# Append à semantic_schema (existant) :")
        blocks.append("semantic_schema:")
        sub = yaml.safe_dump(entities_to_add, allow_unicode=True, sort_keys=False, width=120)
        blocks.append("\n".join("  " + line for line in sub.rstrip().splitlines()))
        blocks.append("")

    if views_to_add:
        blocks.append("# Append à semantic_views (existant) :")
        blocks.append("semantic_views:")
        sub = yaml.safe_dump(views_to_add, allow_unicode=True, sort_keys=False, width=120)
        blocks.append("\n".join("  " + line for line in sub.rstrip().splitlines()))
        blocks.append("")

    if routes_to_add:
        blocks.append(yaml.safe_dump({"events_routes": routes_to_add},
                                     allow_unicode=True, sort_keys=False, width=120))
        blocks.append("")

    append_block = "\n".join(blocks) + "\n"
    with open(YAML_PATH, "a", encoding="utf-8") as f:
        f.write(append_block)

    # Validation
    with open(YAML_PATH, encoding="utf-8") as f:
        yaml.safe_load(f)
    print(f"✓ Phase 1 appliqué : "
          f"{len(entities_to_add)} entités, {len(views_to_add)} vues, "
          f"{len(routes_to_add)} routes events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
