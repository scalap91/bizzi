"""bizzi.social.templates — Templates par tenant.

Lit la section `social:` dans /opt/bizzi/bizzi/domains/<tenant>.yaml :

    social:
      networks: [tiktok, instagram]
      shadow_mode: true
      tiktok_token_ref: env:TIKTOK_TOKEN_LESDEMOCRATES
      templates:
        article_clip:
          base: lesdemocrates_article   # builtin
          overrides:
            duration_sec: 25
        custom_promo:
          background_image: /var/www/lesdemocrates/static/promo.jpg
          overlays: [...]              # full custom

Si aucune config tenant, les builtins (BUILTIN_TEMPLATES) restent accessibles
par leur id. Les overrides sont fusionnés à la volée par get_template().
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

import yaml

from .video_generator import BUILTIN_TEMPLATES

DOMAINS_DIR = Path("/opt/bizzi/bizzi/domains")


def _tenant_yaml_path(tenant_slug: str) -> Path:
    return DOMAINS_DIR / f"{tenant_slug}.yaml"


def load_tenant_social_config(tenant_slug: str) -> dict[str, Any]:
    """Charge la section `social:` du yaml tenant. {} si absent."""
    p = _tenant_yaml_path(tenant_slug)
    if not p.exists():
        return {}
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return data.get("social") or {}


def list_tenant_templates(tenant_slug: str) -> list[str]:
    """Retourne les ids de templates disponibles pour ce tenant (custom + builtins)."""
    cfg = load_tenant_social_config(tenant_slug)
    custom = list((cfg.get("templates") or {}).keys())
    return custom + list(BUILTIN_TEMPLATES.keys())


def get_template(tenant_slug: str, template_id: str) -> Optional[dict]:
    """Résout un template par id pour un tenant.

    Ordre de résolution :
      1. Template custom dans yaml tenant.
      2. Template custom avec champ `base: <builtin>` → fusion + overrides.
      3. Builtin direct (si template_id ∈ BUILTIN_TEMPLATES).
    """
    cfg = load_tenant_social_config(tenant_slug)
    custom = (cfg.get("templates") or {}).get(template_id)

    if custom:
        base_id = custom.get("base")
        if base_id and base_id in BUILTIN_TEMPLATES:
            merged = copy.deepcopy(BUILTIN_TEMPLATES[base_id])
            overrides = custom.get("overrides") or {}
            merged.update({k: v for k, v in overrides.items() if k != "overlays"})
            if "overlays" in overrides:
                merged["overlays"] = overrides["overlays"]
            return merged
        return copy.deepcopy(custom)

    if template_id in BUILTIN_TEMPLATES:
        return copy.deepcopy(BUILTIN_TEMPLATES[template_id])
    return None


def get_tenant_networks(tenant_slug: str) -> list[str]:
    cfg = load_tenant_social_config(tenant_slug)
    return list(cfg.get("networks") or [])


def is_shadow_mode(tenant_slug: str) -> bool:
    """Shadow mode = ON par défaut (Pascal valide avant publication)."""
    cfg = load_tenant_social_config(tenant_slug)
    return bool(cfg.get("shadow_mode", True))


def provider_credential_ref(tenant_slug: str, network: str) -> Optional[str]:
    """Retourne la référence de credential (ex: 'env:TIKTOK_TOKEN_X')."""
    cfg = load_tenant_social_config(tenant_slug)
    return cfg.get(f"{network}_token_ref")
