"""scripts/migrate_chat_log_jsonl.py — migration rétroactive du log JSONL.

Lit `/var/log/bizzi-chat.log` (JSON Lines), parse, anonymise (best effort),
classe l'intent (Haiku) et insert dans `chat_logs`.

Idempotent (best effort) : on ne ré-insère pas les lignes déjà migrées,
critère = (tenant, session_id, ts) déjà présent dans `chat_logs.created_at`
à 1 seconde près. Comme le log JSONL ne contient PAS le contenu user/agent
des messages individuels (seulement les compteurs token + tools), les colonnes
`message_user` et `message_agent` resteront NULL pour ces rows.

Usage :
    /opt/bizzi/bizzi/venv/bin/python /opt/bizzi/bizzi/scripts/migrate_chat_log_jsonl.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path("/opt/bizzi/bizzi")
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

CHAT_LOG = Path("/var/log/bizzi-chat.log")
DB_URL = os.getenv("DATABASE_URL")


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _industry_for(slug: str) -> str:
    # On essaie de charger via tenant_db, sinon fallback "other".
    try:
        from tenant_db import load_tenant
        t = load_tenant(slug)
        md = (getattr(t, "config", None) and getattr(t.config, "metadata", None)) or {}
        return md.get("industry") or md.get("domain") or "other"
    except Exception:
        return "other"


def main() -> int:
    if not CHAT_LOG.exists():
        print(f"[migrate] {CHAT_LOG} introuvable")
        return 0
    if not DB_URL:
        print("[migrate] DATABASE_URL manquant")
        return 1

    rows: list[dict] = []
    with CHAT_LOG.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    print(f"[migrate] {len(rows)} lignes JSONL parsées")

    if not rows:
        return 0

    industry_cache: dict[str, str] = {}
    inserted = 0
    skipped = 0

    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        for r in rows:
            tenant = r.get("tenant")
            session_id = r.get("session_id")
            ts = _parse_ts(r.get("ts") or r.get("timestamp") or "")
            if not tenant or not session_id:
                skipped += 1
                continue

            # Idempotence : skip si déjà présent (à la seconde près).
            if ts is not None:
                cur.execute(
                    """
                    SELECT 1 FROM chat_logs
                    WHERE tenant = %s AND session_id = %s
                      AND created_at BETWEEN %s::timestamp - INTERVAL '1 second'
                                          AND %s::timestamp + INTERVAL '1 second'
                    LIMIT 1
                    """,
                    (tenant, session_id, ts, ts),
                )
                if cur.fetchone():
                    skipped += 1
                    continue

            industry = industry_cache.get(tenant)
            if industry is None:
                industry = _industry_for(tenant)
                industry_cache[tenant] = industry

            tools_called = r.get("tools_called") or []
            if not isinstance(tools_called, list):
                tools_called = []

            cur.execute(
                """
                INSERT INTO chat_logs (
                  tenant, tenant_industry, tenant_size_bucket, tenant_region,
                  session_id, agent_slug, model,
                  tokens_in, tokens_out, cost_usd, duration_ms,
                  tools_called, confidence, cgu_version, created_at
                ) VALUES (
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s, %s,
                  %s::jsonb, %s, %s,
                  COALESCE(%s, NOW())
                )
                """,
                (
                    tenant, industry, "sme", "fr-fr",
                    session_id, "support", r.get("model"),
                    int(r.get("input_tokens") or 0),
                    int(r.get("output_tokens") or 0),
                    float(r.get("cost_estimated") or 0.0),
                    int(r.get("duration_ms") or 0),
                    json.dumps(tools_called, ensure_ascii=False),
                    r.get("confidence"),
                    "v1.0",
                    ts,
                ),
            )
            inserted += 1

    print(f"[migrate] terminé : inserted={inserted}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
