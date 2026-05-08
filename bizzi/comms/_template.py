"""Mini-renderer interne partagé par comms.sms et comms.mail.

Syntaxe `{{ key }}` ou `{{ key.subkey }}` (dot-path, dict).
- Strict : variable manquante → ValueError. Variable None → ValueError.
- Aucune exécution de code (regex pure, pas de jinja2 / pas de format()).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Optional

import yaml

YAML_DIR = "/opt/bizzi/bizzi/domains"

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def _resolve(path: str, ctx: dict) -> Any:
    cur: Any = ctx
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            raise ValueError(f"variable inconnue: {path}")
    return cur


def render_string(template: str, ctx: Optional[dict]) -> str:
    if not template:
        return ""
    ctx = ctx or {}

    def _sub(m: re.Match) -> str:
        val = _resolve(m.group(1), ctx)
        if val is None:
            raise ValueError(f"variable {m.group(1)} = None")
        return str(val)

    return _VAR_RE.sub(_sub, template)


@lru_cache(maxsize=64)
def _load_tenant_yaml_cached(yaml_dir: str, tenant_slug: str) -> dict:
    path = os.path.join(yaml_dir, f"{tenant_slug}.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_tenant_yaml(tenant_slug: str, yaml_dir: Optional[str] = None) -> dict:
    return _load_tenant_yaml_cached(yaml_dir or YAML_DIR, tenant_slug)


def reload_tenant_yaml() -> None:
    """Vide le cache yaml. À appeler dans tests ou après hot-reload."""
    _load_tenant_yaml_cached.cache_clear()
