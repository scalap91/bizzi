"""scripts/migrate_knowledge_to_pgvector.py — Migration FS knowledge → memory_vector.

Contexte : `bizzi.tools.knowledge.knowledge_engine.KnowledgeEngine` stocke
les memories des anciens agents en JSON sur disque sous
    bureau/competences/<agent_slug>/{memory.json,urls.json,index.json}

Le module bizzi.data.memory_vector (Phase 0) propose une mémoire pgvector
moderne, par tenant (table memory_<tenant_id>). Cette migration COPIE
(jamais ne déplace ni supprime) les entrées FS vers pgvector pour que les
NOUVEAUX agents puissent les retrouver via memory_search.

Garanties :
  - **Aucun fichier FS n'est modifié ou supprimé** (lecture seule).
  - **Idempotent** : chaque entrée FS reçoit un fs_hash déterministe ;
    si déjà présent dans memory_<tenant_id>, on skip.
  - Best-effort : un agent absent de la DB est loggé puis skipé, pas
    d'échec global.

Usage CLI :
    # Dry-run (par défaut) — affiche ce qui SERAIT migré
    /opt/bizzi/bizzi/venv/bin/python -m data.scripts.migrate_knowledge_to_pgvector

    # Apply
    /opt/bizzi/bizzi/venv/bin/python -m data.scripts.migrate_knowledge_to_pgvector --apply

    # Un seul agent
    /opt/bizzi/bizzi/venv/bin/python -m data.scripts.migrate_knowledge_to_pgvector --apply --agent alice-roy

    # Inclure URLs et documents (en plus des memory entries)
    /opt/bizzi/bizzi/venv/bin/python -m data.scripts.migrate_knowledge_to_pgvector --apply --include-urls --include-docs

    # Override de la racine FS
    KNOWLEDGE_ROOT=/path/to/competences /opt/bizzi/bizzi/venv/bin/python -m data.scripts.migrate_knowledge_to_pgvector --apply

Wiring optionnel au boot uvicorn (à valider Pascal) :
    from data.scripts.migrate_knowledge_to_pgvector import migrate_all
    migrate_all(apply=True)   # idempotent — safe à lancer à chaque boot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Bootstrap PYTHONPATH si lancé hors module.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

import psycopg2
from psycopg2.extras import RealDictCursor

from data import memory_vector  # noqa: E402
from data._db import DB_CONFIG  # noqa: E402


logger = logging.getLogger("bizzi.data.migrate_knowledge")


# ── Defaults ─────────────────────────────────────────────────────
# knowledge_engine.py: KNOWLEDGE_ROOT = Path(os.getenv("KNOWLEDGE_ROOT", "bureau/competences"))
# C'est un chemin RELATIF au CWD. On le résout en absolu.
def _default_root() -> Path:
    env = os.environ.get("KNOWLEDGE_ROOT")
    if env:
        return Path(env).resolve()
    # Fallback : /opt/bizzi/bizzi/bureau/competences
    fallback = Path("/opt/bizzi/bizzi/bureau/competences")
    if fallback.exists():
        return fallback
    # Sinon CWD/bureau/competences
    return (Path.cwd() / "bureau" / "competences").resolve()


# ── Agent slug → (tenant_id, agent_id) ───────────────────────────
def _resolve_agents(agent_slugs: list[str]) -> dict[str, tuple[int, int]]:
    """Mappe chaque slug FS vers (tenant_id, agent_id) DB.

    Retourne {slug: (tenant_id, agent_id)} pour les slugs trouvés en DB.
    Les slugs non trouvés sont absents du dict (à logger comme skip).
    """
    if not agent_slugs:
        return {}
    with psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG) as c, c.cursor() as cur:
        cur.execute(
            "SELECT slug, tenant_id, id FROM agents WHERE slug = ANY(%s)",
            (list(agent_slugs),),
        )
        return {r["slug"]: (r["tenant_id"], r["id"]) for r in cur.fetchall()}


# ── FS readers ───────────────────────────────────────────────────
def _safe_load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[fs] %s — JSON invalide (%s)", path, e)
        return {}


def _list_agent_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


# ── Idempotence ──────────────────────────────────────────────────
def _entry_hash(payload: dict[str, Any]) -> str:
    """Hash déterministe sur le contenu et le path FS source.

    Inclut : (agent_slug, kind, fs_path, content_or_url) — sha256 court.
    """
    canonical = json.dumps(
        {k: payload.get(k) for k in ("agent_slug", "kind", "fs_path",
                                     "content_or_url", "topic", "added_at")},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _already_migrated(tenant_id: int, fs_hash: str) -> bool:
    """Cherche si une row memory existe avec metadata.fs_hash = fs_hash.

    On ne se base PAS sur la table directement avec un index dédié — on fait
    un scan ciblé. Le volume migré est petit (<1k entries) donc OK Phase 1.
    Si ça grossit, on ajoutera CREATE INDEX ON memory_<tid> ((metadata->>'fs_hash')).
    """
    table = memory_vector._table_name(tenant_id)
    try:
        with psycopg2.connect(**DB_CONFIG) as c, c.cursor() as cur:
            cur.execute(
                # %s = fs_hash, on cast metadata->>'fs_hash' en text pour égalité.
                f"SELECT 1 FROM {table} WHERE metadata->>'fs_hash' = %s LIMIT 1",
                (fs_hash,),
            )
            return cur.fetchone() is not None
    except psycopg2.errors.UndefinedTable:
        # La table memory_<tenant> n'existe pas encore — sera créée au premier store.
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("idempotence check failed for tenant=%s: %s", tenant_id, e)
        return False


# ── Migration unitaire ───────────────────────────────────────────
def _migrate_memory_entry(
    *,
    tenant_id: int,
    agent_id: int,
    agent_slug: str,
    fs_path: Path,
    entry_index: int,
    entry: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    topic   = (entry.get("topic")   or "")[:500]
    content = (entry.get("content") or "")
    src     = entry.get("source") or "fs_migration"
    added   = entry.get("added_at") or ""

    if not content.strip():
        return {"action": "skip_empty", "fs_path": str(fs_path), "entry_index": entry_index}

    text = (f"[{topic}]\n\n{content}" if topic else content)[:8000]

    fs_hash = _entry_hash({
        "agent_slug":      agent_slug,
        "kind":            "knowledge_memory",
        "fs_path":         str(fs_path),
        "content_or_url":  content[:200],
        "topic":           topic,
        "added_at":        added,
    })

    if _already_migrated(tenant_id, fs_hash):
        return {"action": "skip_duplicate", "fs_hash": fs_hash}

    if not apply:
        return {"action": "would_insert", "fs_hash": fs_hash,
                "topic": topic, "tenant_id": tenant_id, "agent_id": agent_id,
                "fs_path": str(fs_path)}

    mid = memory_vector.memory_store(
        tenant_id=tenant_id,
        text=text,
        agent_id=agent_id,
        kind="knowledge_memory",
        source_ref=f"fs_knowledge:{agent_slug}:memory.json#{entry_index}",
        metadata={
            "fs_hash":       fs_hash,
            "fs_source":     "knowledge_engine",
            "fs_path":       str(fs_path),
            "agent_slug":    agent_slug,
            "topic":         topic,
            "fs_source_field": src,
            "added_at":      added,
            "migrated_from": "tools.knowledge.knowledge_engine",
        },
    )
    return {"action": "inserted", "memory_id": mid, "fs_hash": fs_hash}


def _migrate_url(
    *,
    tenant_id: int,
    agent_id: int,
    agent_slug: str,
    fs_path: Path,
    url_entry: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    url     = (url_entry.get("url") or "").strip()
    label   = (url_entry.get("label") or url)[:500]
    cat     = url_entry.get("category") or "general"
    preview = url_entry.get("preview") or ""
    if not url:
        return {"action": "skip_empty"}

    text = f"[URL référence] {label}\nCategory: {cat}\n{url}\n\n{preview}"[:8000]
    fs_hash = _entry_hash({
        "agent_slug":     agent_slug,
        "kind":           "knowledge_url",
        "fs_path":        str(fs_path),
        "content_or_url": url,
        "topic":          label,
    })
    if _already_migrated(tenant_id, fs_hash):
        return {"action": "skip_duplicate", "fs_hash": fs_hash}

    if not apply:
        return {"action": "would_insert_url", "fs_hash": fs_hash,
                "url": url, "tenant_id": tenant_id}

    mid = memory_vector.memory_store(
        tenant_id=tenant_id,
        text=text,
        agent_id=agent_id,
        kind="knowledge_url",
        source_ref=f"fs_knowledge:{agent_slug}:urls.json:{url}",
        metadata={
            "fs_hash":     fs_hash,
            "fs_source":   "knowledge_engine",
            "fs_path":     str(fs_path),
            "agent_slug":  agent_slug,
            "url":         url,
            "label":       label,
            "category":    cat,
            "migrated_from": "tools.knowledge.knowledge_engine",
        },
    )
    return {"action": "inserted_url", "memory_id": mid, "fs_hash": fs_hash}


def _migrate_doc(
    *,
    tenant_id: int,
    agent_id: int,
    agent_slug: str,
    fs_path: Path,
    doc_entry: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    filename = doc_entry.get("filename") or ""
    preview  = doc_entry.get("text_preview") or ""
    char_cnt = doc_entry.get("char_count", 0)
    dtype    = doc_entry.get("type", "")
    if not filename or not preview:
        return {"action": "skip_no_text"}

    text = f"[Document {dtype}] {filename}\nTaille texte: {char_cnt} chars\n\n{preview}"[:8000]
    fs_hash = _entry_hash({
        "agent_slug":     agent_slug,
        "kind":           "knowledge_doc",
        "fs_path":        str(fs_path),
        "content_or_url": filename,
        "topic":          filename,
    })
    if _already_migrated(tenant_id, fs_hash):
        return {"action": "skip_duplicate", "fs_hash": fs_hash}

    if not apply:
        return {"action": "would_insert_doc", "fs_hash": fs_hash,
                "filename": filename, "tenant_id": tenant_id}

    mid = memory_vector.memory_store(
        tenant_id=tenant_id,
        text=text,
        agent_id=agent_id,
        kind="knowledge_doc",
        source_ref=f"fs_knowledge:{agent_slug}:index.json:{filename}",
        metadata={
            "fs_hash":      fs_hash,
            "fs_source":    "knowledge_engine",
            "fs_path":      str(fs_path),
            "agent_slug":   agent_slug,
            "filename":     filename,
            "doc_type":     dtype,
            "char_count":   char_cnt,
            "migrated_from": "tools.knowledge.knowledge_engine",
        },
    )
    return {"action": "inserted_doc", "memory_id": mid, "fs_hash": fs_hash}


# ── Migration globale ────────────────────────────────────────────
def migrate_all(
    *,
    apply: bool = False,
    only_agent: Optional[str] = None,
    include_urls: bool = False,
    include_docs: bool = False,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Point d'entrée programmatique.

    Idempotent : safe à lancer à chaque démarrage. Retourne un dict de stats
    (per-agent + total) pour logging applicatif.
    """
    root = root or _default_root()
    if not root.exists():
        return {"ok": False, "error": f"KNOWLEDGE_ROOT introuvable : {root}",
                "agents": {}, "totals": {}}

    agent_dirs = _list_agent_dirs(root)
    if only_agent:
        agent_dirs = [d for d in agent_dirs if d.name == only_agent]
        if not agent_dirs:
            return {"ok": False, "error": f"agent {only_agent!r} introuvable sous {root}",
                    "agents": {}, "totals": {}}

    slugs = [d.name for d in agent_dirs]
    db_map = _resolve_agents(slugs)

    per_agent: dict[str, Any] = {}
    totals = {
        "memory_inserted":  0, "memory_skipped":  0, "memory_would":   0,
        "url_inserted":     0, "url_skipped":     0, "url_would":      0,
        "doc_inserted":     0, "doc_skipped":     0, "doc_would":      0,
        "agents_processed": 0, "agents_skipped":  0,
    }

    for agent_dir in agent_dirs:
        slug = agent_dir.name
        if slug not in db_map:
            per_agent[slug] = {"skipped_reason": "agent_slug not in DB"}
            totals["agents_skipped"] += 1
            continue

        tenant_id, agent_id = db_map[slug]
        ag_stats: dict[str, Any] = {
            "tenant_id": tenant_id, "agent_id": agent_id,
            "memories": [], "urls": [], "docs": [],
        }

        # 1. memory.json
        mem_path = agent_dir / "memory.json"
        if mem_path.exists():
            mem_data = _safe_load_json(mem_path)
            for i, entry in enumerate(mem_data.get("entries") or []):
                res = _migrate_memory_entry(
                    tenant_id=tenant_id, agent_id=agent_id, agent_slug=slug,
                    fs_path=mem_path, entry_index=i, entry=entry, apply=apply,
                )
                ag_stats["memories"].append(res)
                act = res.get("action", "")
                if act == "inserted":      totals["memory_inserted"] += 1
                elif act == "would_insert": totals["memory_would"]   += 1
                else:                       totals["memory_skipped"] += 1

        # 2. urls.json
        if include_urls:
            url_path = agent_dir / "urls.json"
            if url_path.exists():
                url_data = _safe_load_json(url_path)
                for url_entry in url_data.get("urls") or []:
                    res = _migrate_url(
                        tenant_id=tenant_id, agent_id=agent_id, agent_slug=slug,
                        fs_path=url_path, url_entry=url_entry, apply=apply,
                    )
                    ag_stats["urls"].append(res)
                    act = res.get("action", "")
                    if act == "inserted_url":      totals["url_inserted"] += 1
                    elif act == "would_insert_url": totals["url_would"]   += 1
                    else:                           totals["url_skipped"] += 1

        # 3. index.json (documents)
        if include_docs:
            idx_path = agent_dir / "index.json"
            if idx_path.exists():
                idx_data = _safe_load_json(idx_path)
                for doc_entry in idx_data.get("documents") or []:
                    res = _migrate_doc(
                        tenant_id=tenant_id, agent_id=agent_id, agent_slug=slug,
                        fs_path=idx_path, doc_entry=doc_entry, apply=apply,
                    )
                    ag_stats["docs"].append(res)
                    act = res.get("action", "")
                    if act == "inserted_doc":      totals["doc_inserted"] += 1
                    elif act == "would_insert_doc": totals["doc_would"]   += 1
                    else:                           totals["doc_skipped"] += 1

        per_agent[slug] = ag_stats
        totals["agents_processed"] += 1

    return {
        "ok": True,
        "apply": apply,
        "root": str(root),
        "include_urls": include_urls,
        "include_docs": include_docs,
        "totals": totals,
        "agents": per_agent,
    }


# ── CLI ──────────────────────────────────────────────────────────
def _cli() -> int:
    p = argparse.ArgumentParser(description="Migration FS knowledge_engine → memory_vector")
    p.add_argument("--apply", action="store_true",
                   help="Effectue réellement les insertions (sinon dry-run)")
    p.add_argument("--agent", type=str, default=None,
                   help="Migrer uniquement cet agent_slug")
    p.add_argument("--include-urls", action="store_true",
                   help="Migrer aussi urls.json")
    p.add_argument("--include-docs", action="store_true",
                   help="Migrer aussi index.json (preview docs)")
    p.add_argument("--root", type=str, default=None,
                   help="Chemin racine FS (default $KNOWLEDGE_ROOT ou /opt/bizzi/bizzi/bureau/competences)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    print(f"== Migration FS knowledge → pgvector "
          f"({'APPLY' if args.apply else 'DRY-RUN'}) ==")
    print(f"   include-urls={args.include_urls}  include-docs={args.include_docs}")
    print(f"   only-agent={args.agent or '(tous)'}")

    res = migrate_all(
        apply=args.apply,
        only_agent=args.agent,
        include_urls=args.include_urls,
        include_docs=args.include_docs,
        root=Path(args.root).resolve() if args.root else None,
    )

    if not res["ok"]:
        print(f"\n✗ {res.get('error')}")
        return 1

    print(f"\nroot: {res['root']}")
    print(f"agents traités : {res['totals']['agents_processed']} "
          f"(skipped: {res['totals']['agents_skipped']})\n")

    # Per-agent summary (concise)
    for slug, st in res["agents"].items():
        if "skipped_reason" in st:
            print(f"  [skip] {slug:25s} → {st['skipped_reason']}")
            continue
        m = st["memories"]
        u = st.get("urls", [])
        d = st.get("docs", [])
        ins = sum(1 for r in m + u + d if r.get("action", "").startswith("inserted"))
        wld = sum(1 for r in m + u + d if r.get("action", "").startswith("would"))
        skp = sum(1 for r in m + u + d if r.get("action", "").startswith("skip"))
        if (ins + wld + skp) > 0:
            print(f"  {slug:25s} tenant={st['tenant_id']:2d} agent={st['agent_id']:3d}  "
                  f"insert={ins} would={wld} skip={skp}  "
                  f"(mem={len(m)} url={len(u)} doc={len(d)})")

    print("\n=== TOTALS ===")
    for k, v in res["totals"].items():
        print(f"  {k:22s} = {v}")

    if not args.apply:
        print("\nℹ  Dry-run (rien écrit). Relancer avec --apply pour effectuer la migration.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
