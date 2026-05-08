"""bizzi.data — Applique le patch YAML data sections à lesdemocrates.yaml.

Idempotent : si data_sources / semantic_schema / semantic_views existent déjà
dans le yaml, le script ne touche à rien et exit 0.

Usage (à valider par Pascal — modifie un fichier root) :
    sudo /opt/bizzi/bizzi/venv/bin/python \
        /opt/bizzi/bizzi/data/migrations/apply_lesdemocrates_data.py
"""
from __future__ import annotations

import datetime
import shutil
import sys
from pathlib import Path

import yaml


YAML_PATH  = Path("/opt/bizzi/bizzi/domains/lesdemocrates.yaml")
PATCH_PATH = Path(__file__).resolve().parent / "001_lesdemocrates_data_sections.yaml.patch"


def main() -> int:
    if not YAML_PATH.exists():
        print(f"✗ {YAML_PATH} introuvable", file=sys.stderr)
        return 1
    if not PATCH_PATH.exists():
        print(f"✗ patch {PATCH_PATH} introuvable", file=sys.stderr)
        return 1

    with open(YAML_PATH, encoding="utf-8") as f:
        existing = yaml.safe_load(f) or {}

    already = {k: (k in existing)
               for k in ("data_sources", "semantic_schema", "semantic_views")}
    if any(already.values()):
        print(f"⚠ sections déjà présentes : "
              f"{[k for k,v in already.items() if v]}")
        print("→ patch non appliqué (idempotence)")
        return 0

    with open(PATCH_PATH, encoding="utf-8") as f:
        patch_text = f.read()
    patch = yaml.safe_load(patch_text) or {}

    # Backup
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = YAML_PATH.with_suffix(f".yaml.bak-pre-data-{ts}")
    shutil.copy2(YAML_PATH, backup)
    print(f"→ backup : {backup}")

    # Append en YAML formaté (préserve les commentaires existants)
    append_block = "\n\n# ══════════════════════════════════════════════════════════════\n"
    append_block += "# DATA — bizzi.data sections (data_sources, semantic_schema, semantic_views)\n"
    append_block += "# ══════════════════════════════════════════════════════════════\n\n"
    for key in ("data_sources", "semantic_schema", "semantic_views"):
        if key in patch:
            append_block += yaml.safe_dump(
                {key: patch[key]}, allow_unicode=True, sort_keys=False, width=120,
            )
            append_block += "\n"

    with open(YAML_PATH, "a", encoding="utf-8") as f:
        f.write(append_block)

    # Validation : recharger pour vérifier que le YAML reste valide
    with open(YAML_PATH, encoding="utf-8") as f:
        yaml.safe_load(f)

    print(f"✓ sections appliquées : {list(patch.keys())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
