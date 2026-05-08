"""api/routes/admin.py — Endpoints admin Bizzi (vrais tenants).

Lecture des stats depuis le log JSON Lines /var/log/bizzi-chat.log.
Liste des tenants depuis tenant_db.list_tenants() (yaml dans /opt/bizzi/bizzi/tenants/).

Wiring (dans api/main.py) :
    from api.routes import admin as admin_routes
    app.include_router(admin_routes.router, prefix="/api/admin", tags=["Admin"])

Note : un module `admin_usage.py` séparé monte aussi sur /api/admin (routes
/usage_stats, /usage_recent). Aucun conflit de chemin avec celles définies ici.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml as _yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

try:
    from tenant_db import list_tenants, load_tenant
except Exception:  # pragma: no cover
    list_tenants = lambda: []
    load_tenant = lambda slug: None

router = APIRouter()
logger = logging.getLogger("api.routes.admin")

CHAT_LOG_PATH = Path("/var/log/bizzi-chat.log")
TENANTS_DIR = Path("/opt/bizzi/bizzi/tenants")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


# ── Helpers ──────────────────────────────────────────────────────────

def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # Supporte "...+00:00" et "...Z"
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _read_chat_log() -> list[dict[str, Any]]:
    """Parse le log JSON Lines. Retourne liste vide si absent/illisible."""
    if not CHAT_LOG_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with CHAT_LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"chat log unreadable: {e}")
        return []
    return rows


def _is_today(ts_str: str, now: datetime) -> bool:
    ts = _parse_ts(ts_str)
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts) <= timedelta(hours=24)


def _row_cost(row: dict[str, Any]) -> float:
    # accepte "cost" ou "cost_estimated"
    v = row.get("cost")
    if v is None:
        v = row.get("cost_estimated", 0.0)
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _row_tools_called(row: dict[str, Any]) -> int:
    tc = row.get("tools_called") or []
    if isinstance(tc, list):
        return len(tc)
    if isinstance(tc, int):
        return tc
    return 0


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# ── /stats/global ────────────────────────────────────────────────────

@router.get("/stats/global")
async def global_stats():
    rows = _read_chat_log()
    now = datetime.now(timezone.utc)
    tenants_all = list_tenants() or []

    messages_total = len(rows)
    messages_today = 0
    tokens_in_total = 0
    tokens_out_total = 0
    cost_total_usd = 0.0
    cost_today_usd = 0.0
    tools_called_total = 0
    duration_sum = 0
    duration_count = 0
    active_today_set: set[str] = set()

    for r in rows:
        tin = _safe_int(r.get("input_tokens"))
        tout = _safe_int(r.get("output_tokens"))
        tokens_in_total += tin
        tokens_out_total += tout
        c = _row_cost(r)
        cost_total_usd += c
        tools_called_total += _row_tools_called(r)
        d = _safe_int(r.get("duration_ms"))
        if d > 0:
            duration_sum += d
            duration_count += 1
        ts_str = r.get("ts") or r.get("timestamp") or ""
        if _is_today(ts_str, now):
            messages_today += 1
            cost_today_usd += c
            t = r.get("tenant")
            if t:
                active_today_set.add(t)

    avg_duration_ms = int(duration_sum / duration_count) if duration_count else 0

    return {
        "tenants_total":       len(tenants_all),
        "tenants_active_today": len(active_today_set),
        "messages_total":      messages_total,
        "messages_today":      messages_today,
        "tokens_in_total":     tokens_in_total,
        "tokens_out_total":    tokens_out_total,
        "cost_total_usd":      round(cost_total_usd, 6),
        "cost_today_usd":      round(cost_today_usd, 6),
        "tools_called_total":  tools_called_total,
        "avg_duration_ms":     avg_duration_ms,
    }


# ── /stats/tenants ───────────────────────────────────────────────────

@router.get("/stats/tenants")
async def all_tenants_stats():
    rows = _read_chat_log()
    now = datetime.now(timezone.utc)

    # Pré-calcule un index par tenant pour ne parcourir le log qu'une fois
    by_tenant: dict[str, dict[str, Any]] = {}
    for r in rows:
        slug = r.get("tenant") or "unknown"
        bucket = by_tenant.setdefault(slug, {
            "messages_total": 0,
            "messages_today": 0,
            "tokens_in_total": 0,
            "tokens_out_total": 0,
            "cost_total_usd": 0.0,
            "cost_today_usd": 0.0,
        })
        bucket["messages_total"] += 1
        bucket["tokens_in_total"] += _safe_int(r.get("input_tokens"))
        bucket["tokens_out_total"] += _safe_int(r.get("output_tokens"))
        c = _row_cost(r)
        bucket["cost_total_usd"] += c
        ts_str = r.get("ts") or r.get("timestamp") or ""
        if _is_today(ts_str, now):
            bucket["messages_today"] += 1
            bucket["cost_today_usd"] += c

    out: list[dict[str, Any]] = []
    for slug in (list_tenants() or []):
        # Charge le YAML/config tenant
        name = slug
        domain = ""
        model = ""
        queries_count = 0
        plan = "pro"
        try:
            prov = load_tenant(slug)
            cfg = getattr(prov, "config", None) or prov
            md = getattr(cfg, "metadata", {}) or {}
            name = md.get("name", slug)
            domain = md.get("domain", "")
            llm = getattr(cfg, "llm", None)
            model = getattr(llm, "model", "") if llm else (md.get("llm", {}) or {}).get("model", "")
            queries = getattr(cfg, "queries", {}) or {}
            queries_count = len(queries)
            plan = md.get("plan", "pro")
        except Exception as e:
            logger.warning(f"tenant load failed for {slug}: {e}")
            # Fallback : lit directement le YAML (utile pour tenants
            # nouvellement créés dont la DB password env n'est pas encore set).
            try:
                yaml_path = TENANTS_DIR / f"{slug}.yaml"
                if yaml_path.exists():
                    raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                    md = raw.get("metadata", {}) or {}
                    name = md.get("name", slug)
                    domain = md.get("domain", "")
                    model = (md.get("llm", {}) or {}).get("model", "")
                    queries_count = len(raw.get("queries", {}) or {})
                    plan = md.get("plan", "pro")
            except Exception as e2:
                logger.warning(f"tenant YAML fallback failed for {slug}: {e2}")

        b = by_tenant.get(slug, {})
        messages_total = int(b.get("messages_total", 0))
        messages_today = int(b.get("messages_today", 0))

        # Statut basique : actif si activité 24h, warning si jamais de message, sinon active
        if messages_today > 0:
            status = "active"
        elif messages_total > 0:
            status = "active"
        else:
            status = "warning"

        out.append({
            "slug":             slug,
            "name":             name,
            "domain":           domain,
            "status":           status,
            "plan":             plan,
            "messages_total":   messages_total,
            "messages_today":   messages_today,
            "tokens_in_total":  int(b.get("tokens_in_total", 0)),
            "tokens_out_total": int(b.get("tokens_out_total", 0)),
            "cost_total_usd":   round(float(b.get("cost_total_usd", 0.0)), 6),
            "cost_today_usd":   round(float(b.get("cost_today_usd", 0.0)), 6),
            "queries_count":    queries_count,
            "model":            model,
            "created_at":       None,
        })

    out.sort(key=lambda t: t["messages_total"], reverse=True)
    return {"tenants": out, "total": len(out)}


# ── /tenants/create ──────────────────────────────────────────────────
# Sprint 3 : auto-dépôt YAML scalable via configurator (pas de code spécifique tenant).

class TenantCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z][a-z0-9-]{1,30}$")
    name: str
    domain: str  # media|politics|diagnostic|travel|custom|...
    description: str = ""
    agent_persona: str
    system_prompt: str = ""  # peut être vide → backend met un défaut
    llm_model: str = "claude-haiku-4-5"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.7
    rate_limit_max_per_day: int = 100
    rate_limit_max_tokens_per_day: int = 200000
    # Connexion DB (facultative)
    db_type: str = ""  # "" si pas configuré, sinon "postgres"
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = ""
    db_user: str = ""
    db_password_env: str = ""  # nom de la variable env


@router.post("/tenants/create")
async def create_tenant(payload: TenantCreate):
    # 1. validation slug + non-collision
    if not SLUG_RE.match(payload.slug):
        raise HTTPException(400, f"slug invalide '{payload.slug}' (regex: ^[a-z][a-z0-9-]{{1,30}}$)")
    target = TENANTS_DIR / f"{payload.slug}.yaml"
    if target.exists():
        raise HTTPException(409, f"tenant '{payload.slug}' existe déjà")

    # 2. construction du YAML structuré
    default_prompt = (
        f"Tu es l'agent support de {payload.name}.\n"
        "{persona}\n"
        "Réponds en français, concis (3-5 phrases), professionnel."
    )
    metadata = {
        "name": payload.name,
        "domain": payload.domain,
        "description": payload.description or f"Tenant {payload.slug}",
        "agent_persona": payload.agent_persona,
        "system_prompt": payload.system_prompt or default_prompt,
        "llm": {
            "model": payload.llm_model,
            "max_tokens": payload.llm_max_tokens,
            "temperature": payload.llm_temperature,
        },
        "rate_limit": {
            "max_per_day": payload.rate_limit_max_per_day,
            "max_tokens_per_day": payload.rate_limit_max_tokens_per_day,
        },
    }

    yaml_data: dict[str, Any] = {
        "tenant": payload.slug,
        "metadata": metadata,
    }

    # 3. db facultatif
    if payload.db_type and payload.db_name:
        yaml_data["db"] = {
            "type": payload.db_type,
            "host": payload.db_host,
            "port": payload.db_port,
            "name": payload.db_name,
            "user": payload.db_user or f"bizzi_reader_{payload.slug}",
            "password_env": payload.db_password_env or f"BIZZI_{payload.slug.upper().replace('-', '_')}_DB_PASSWORD",
        }
    else:
        # Placeholder direct (pas password_env) pour que tenant_db.registry ne crash pas au boot.
        yaml_data["db"] = {
            "type": "postgres",
            "host": "127.0.0.1",
            "port": 5432,
            "name": f"TODO_{payload.slug}_db",
            "user": f"bizzi_reader_{payload.slug}",
            "password": "TODO_PLACEHOLDER_ROTATE_ME",
        }
    yaml_data["queries"] = {}

    # 4. écriture
    yaml_text = _yaml.dump(
        yaml_data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    header = (
        f"# Tenant {payload.slug} — créé via configurator le "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"# Format scalable consommé par tenant_db.registry\n\n"
    )
    try:
        TENANTS_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(header + yaml_text, encoding="utf-8")
    except Exception as e:
        logger.error(f"écriture tenant {payload.slug} échouée: {e}")
        raise HTTPException(500, f"impossible d'écrire le fichier tenant: {e}")

    # 5. invalidation cache tenant_db si déjà chargé
    try:
        from tenant_db.registry import _CACHE  # type: ignore
        _CACHE.pop(payload.slug, None)
    except Exception:
        pass

    return {
        "success": True,
        "slug": payload.slug,
        "path": str(target),
        "yaml_preview": yaml_text[:500],
        "next_step": (
            "Tenant visible immédiatement dans /api/admin/stats/tenants. "
            "Configurer queries/DB plus tard pour activer les tools."
        ),
    }
