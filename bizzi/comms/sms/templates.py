"""Résolution des templates SMS par tenant.

Utilise le mini-renderer partagé `comms._template`.

Format YAML :

    comms:
      sms:
        templates:
          rdv_reminder:
            body: "Bonjour {{ first_name }}, RDV demain {{ time }} chez {{ org }}."
"""
from __future__ import annotations

from typing import Optional

from .. import _template

# Compat tests Phase 1 — certains montent monkeypatch sur t.YAML_DIR.
YAML_DIR = _template.YAML_DIR


def reload_tenant_yaml(tenant_slug: Optional[str] = None) -> None:
    _template.reload_tenant_yaml()


def get_sms_config(tenant_slug: str) -> dict:
    cfg = _template.load_tenant_yaml(tenant_slug, yaml_dir=YAML_DIR) or {}
    return ((cfg.get("comms") or {}).get("sms") or {})


def list_templates(tenant_slug: str) -> list[str]:
    return list((get_sms_config(tenant_slug).get("templates") or {}).keys())


def render(tenant_slug: str, template_id: str, context: dict) -> str:
    templates = get_sms_config(tenant_slug).get("templates") or {}
    tpl = templates.get(template_id)
    if not tpl:
        raise KeyError(f"template SMS '{template_id}' inconnu pour tenant {tenant_slug}")
    body_tpl = tpl.get("body")
    if not body_tpl:
        raise ValueError(f"template SMS '{template_id}' n'a pas de champ 'body'")
    return _template.render_string(body_tpl, context or {})


def render_inline(body_template: str, context: dict) -> str:
    return _template.render_string(body_template, context or {})
