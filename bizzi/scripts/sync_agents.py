"""
scripts/sync_agents.py
=======================
Synchronise les agents (personnes) d'un tenant entre son YAML et la DB.

Structure YAML attendue (v2.0) :
  metiers:    liste des types de postes dans ce domaine
  personnes:  liste des agents reels (illimite)

Chaque personne reference un metier. Le metier definit le prompt_base
et le role fonctionnel universel (direction, production, validation...).

Usage :
    python3 -m scripts.sync_agents --tenant onyx
    python3 -m scripts.sync_agents --tenant onyx --dry-run
"""

import argparse
import os
import sys
import yaml
import psycopg2
from unicodedata import normalize


# ── DB connection ─────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host="localhost",
        database="bizzi",
        user="bizzi_admin",
        password=os.environ.get("DB_PASSWORD", ""),
    )


# ── Helpers ───────────────────────────────────────────────────

def slugify(name: str) -> str:
    """'Claire BERNARD' -> 'claire-bernard'"""
    s = normalize("NFD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = "".join(c if c.isalnum() or c == " " else "" for c in s)
    return "-".join(s.split())


def build_prompt(template: str, personality: str = "", **vars) -> str:
    """Remplace {key} par la valeur dans un template, et injecte la personnalité."""
    out = template
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v))
    out = out.strip()
    if personality:
        lines = out.split("\n")
        perso_line = f"Ton style : {personality}."
        if lines and lines[0].strip().startswith("Tu es"):
            lines.insert(1, perso_line)
        else:
            lines.insert(0, perso_line)
        out = "\n".join(lines)
    return out


# ── Extraction des agents depuis YAML v2 ──────────────────────

def extract_agents_from_yaml(yaml_path: str) -> list[dict]:
    """
    Retourne la liste plate des agents a creer/sync.
    Structure v2 : metiers + personnes.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if "metiers" not in data or "personnes" not in data:
        raise ValueError(
            "YAML v2 requis : doit contenir 'metiers' et 'personnes'. "
            "Ancienne structure (agents:instances) non supportee."
        )

    org_name = data["identity"]["name"]
    word_min = data["output"]["word_count_min"]
    word_max = data["output"]["word_count_max"]
    score_min = data["output"]["validation_score_min"]

    # Indexer les metiers par id
    metiers = {m["id"]: m for m in data["metiers"]}

    agents = []
    for p in data["personnes"]:
        metier_id = p["metier"]
        if metier_id not in metiers:
            raise ValueError(f"Personne '{p['name']}' reference un metier inconnu : '{metier_id}'")

        metier = metiers[metier_id]
        name = p["name"]
        specialty = p.get("specialty", "")

        prompt = build_prompt(
            metier["prompt_base"],
            personality=p.get("personality", ""),
            agent_name=name,
            specialty=specialty,
            org_name=org_name,
            word_count_min=word_min,
            word_count_max=word_max,
            validation_score_min=score_min,
        )

        agents.append({
            "slug": slugify(name),
            "name": name,
            "role": metier["role"],
            "agent_id": metier_id,
            "specialty": specialty,
            "personality": p.get("personality", ""),
            "system_prompt": prompt,
            "color": p.get("color", "#374151"),
        })

    return agents


# ── Synchronisation DB ────────────────────────────────────────

def sync_tenant_agents(tenant_slug: str, dry_run: bool = False):
    yaml_path = f"/opt/bizzi/bizzi/domains/{tenant_slug}.yaml"
    if not os.path.exists(yaml_path):
        print(f"[ERREUR] YAML introuvable : {yaml_path}")
        sys.exit(1)

    print(f"\n=== Sync agents pour tenant '{tenant_slug}' ===")
    print(f"YAML : {yaml_path}")
    print("MODE : DRY-RUN (aucune modification en DB)\n" if dry_run else "")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM tenants WHERE slug = %s", (tenant_slug,))
    row = cur.fetchone()
    if not row:
        print(f"[ERREUR] Tenant '{tenant_slug}' introuvable en DB")
        sys.exit(1)
    tenant_id = row[0]
    print(f"Tenant DB : id={tenant_id}\n")

    # Extraire
    try:
        desired_agents = extract_agents_from_yaml(yaml_path)
    except ValueError as e:
        print(f"[ERREUR YAML] {e}")
        sys.exit(1)

    desired_slugs = {a["slug"] for a in desired_agents}
    print(f"Agents definis dans YAML : {len(desired_agents)}")
    for a in desired_agents:
        spec = f" [{a['specialty']}]" if a["specialty"] else ""
        print(f"  - {a['slug']} ({a['role']}/{a['agent_id']}) : {a['name']}{spec}")
    print()

    # Agents existants
    cur.execute("SELECT slug, status FROM agents WHERE tenant_id = %s", (tenant_id,))
    existing = {r[0]: r[1] for r in cur.fetchall()}
    print(f"Agents en DB : {len(existing)}\n")

    inserted = updated = paused = 0

    for a in desired_agents:
        if a["slug"] in existing:
            if not dry_run:
                cur.execute("""
                    UPDATE agents SET
                        name = %s, role = %s, agent_id = %s,
                        specialty = %s, personality = %s,
                        system_prompt = %s, color = %s,
                        status = 'active', updated_at = now()
                    WHERE tenant_id = %s AND slug = %s
                """, (
                    a["name"], a["role"], a["agent_id"], a["specialty"],
                    a["personality"], a["system_prompt"], a["color"],
                    tenant_id, a["slug"],
                ))
            print(f"  [UPDATE] {a['slug']}")
            updated += 1
        else:
            if not dry_run:
                cur.execute("""
                    INSERT INTO agents (
                        tenant_id, slug, name, role, agent_id,
                        specialty, personality, system_prompt,
                        color, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', now(), now())
                """, (
                    tenant_id, a["slug"], a["name"], a["role"], a["agent_id"],
                    a["specialty"], a["personality"], a["system_prompt"], a["color"],
                ))
            print(f"  [INSERT] {a['slug']}")
            inserted += 1

    # Pauser les agents qui ne sont plus dans le YAML
    for slug, status in existing.items():
        if slug not in desired_slugs and status != "paused":
            if not dry_run:
                cur.execute("""
                    UPDATE agents SET status = 'paused', updated_at = now()
                    WHERE tenant_id = %s AND slug = %s
                """, (tenant_id, slug))
            print(f"  [PAUSE] {slug} (plus dans le YAML)")
            paused += 1

    if not dry_run:
        conn.commit()
    cur.close()
    conn.close()

    print(f"\n=== Resume ===")
    print(f"Inseres : {inserted}")
    print(f"Mis a jour : {updated}")
    print(f"Mis en pause : {paused}")
    if dry_run:
        print("\n(DRY-RUN : aucune modification appliquee)")


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synchronise les agents d'un tenant depuis son YAML v2")
    parser.add_argument("--tenant", required=True, help="Slug du tenant (ex: onyx)")
    parser.add_argument("--dry-run", action="store_true", help="Simule sans modifier la DB")
    args = parser.parse_args()

    sync_tenant_agents(args.tenant, dry_run=args.dry_run)
