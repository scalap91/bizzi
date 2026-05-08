"""tenant_db/registry.py — charge les YAML de /opt/bizzi/bizzi/tenants/ et expose load_tenant(slug).

YAML attendu :
    tenant: airbizness
    db:
      type: postgres
      host: 127.0.0.1
      port: 5432
      name: airbizness
      user: bizzi_reader
      password_env: BIZZI_AIRBIZNESS_DB_PASSWORD
    queries:
      get_booking_by_email:
        sql: "SELECT booking_ref, origin, destination, ... WHERE user_email = %(email)s"
        params: [email]
        description: "Retourne les bookings d'un email"
        returns: rows
"""
from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Optional
import yaml

from .base import TenantConfig, QueryDef, TenantDBProvider, LLMConfig, RateLimitConfig
from .postgres import PROVIDERS as _PG_PROVIDERS

logger = logging.getLogger("tenant_db.registry")

TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"
PROVIDERS = {**_PG_PROVIDERS}

_CACHE: dict[str, TenantDBProvider] = {}


class TenantNotFound(Exception):
    pass


def _build_dsn(db_cfg: dict) -> str:
    """Construit une DSN psycopg2 keyword/value depuis le YAML."""
    pwd_env = db_cfg.get("password_env")
    pwd = os.getenv(pwd_env) if pwd_env else db_cfg.get("password")
    if not pwd:
        raise RuntimeError(
            f"DB password not found: env var '{pwd_env}' is empty or 'password' missing in yaml"
        )
    parts = [
        f"host={db_cfg.get('host', '127.0.0.1')}",
        f"port={db_cfg.get('port', 5432)}",
        f"dbname={db_cfg['name']}",
        f"user={db_cfg['user']}",
        f"password={pwd}",
        "connect_timeout=5",
        "application_name=bizzi-tenant-db",
    ]
    return " ".join(parts)


def _parse_yaml(path: Path) -> TenantConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    slug = raw.get("tenant") or path.stem
    db_cfg = raw["db"]
    db_type = db_cfg.get("type", "postgres")
    if db_type == "postgres":
        dsn = _build_dsn(db_cfg)
    else:
        raise NotImplementedError(f"db type '{db_type}' not supported yet")

    queries: dict[str, QueryDef] = {}
    for name, q in (raw.get("queries") or {}).items():
        queries[name] = QueryDef(
            name=name,
            sql=q["sql"],
            params=q.get("params", []) or [],
            description=q.get("description", ""),
            returns=q.get("returns", "rows"),
            max_rows=int(q.get("max_rows", 50)),
        )

    md = raw.get("metadata", {}) or {}
    persona = md.get("agent_persona", "") or ""
    sys_prompt = md.get("system_prompt", "") or ""

    llm_raw = md.get("llm", {}) or {}
    llm_cfg = LLMConfig(
        model=str(llm_raw.get("model", "claude-haiku-4-5")),
        max_tokens=int(llm_raw.get("max_tokens", 1024)),
        temperature=float(llm_raw.get("temperature", 0.7)),
    )

    rl_raw = md.get("rate_limit", {}) or {}
    rl_cfg = RateLimitConfig(
        max_per_day=int(rl_raw.get("max_per_day", 100)),
        max_tokens_per_day=int(rl_raw.get("max_tokens_per_day", 200000)),
    )

    return TenantConfig(
        slug=slug,
        db_type=db_type,
        db_dsn=dsn,
        queries=queries,
        metadata=md,
        agent_persona=persona,
        system_prompt=sys_prompt,
        llm=llm_cfg,
        rate_limit=rl_cfg,
    )


def list_tenants() -> list[str]:
    if not TENANTS_DIR.exists():
        return []
    return sorted(p.stem for p in TENANTS_DIR.glob("*.yaml"))


def load_tenant(slug: str) -> TenantDBProvider:
    if slug in _CACHE:
        return _CACHE[slug]
    path = TENANTS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise TenantNotFound(f"tenant config not found: {path}")
    cfg = _parse_yaml(path)
    Provider = PROVIDERS.get(cfg.db_type)
    if not Provider:
        raise RuntimeError(f"no provider for db type '{cfg.db_type}'")
    provider = Provider(cfg)
    _CACHE[slug] = provider
    logger.info(f"tenant loaded: {slug} (db={cfg.db_type}, queries={len(cfg.queries)})")
    return provider


def reload_tenant(slug: str) -> TenantDBProvider:
    """Force reload (utile en dev quand on édite le yaml)."""
    if slug in _CACHE:
        try:
            _CACHE[slug].close()
        except Exception:
            pass
        del _CACHE[slug]
    return load_tenant(slug)
