"""Résolution des templates calendar par tenant.

Format YAML attendu :

    comms:
      calendar:
        templates:
          rdv_consultation:
            title:       "Consultation — {{ patient_name }}"
            description: "Type : {{ type }}. Notes : {{ notes }}."
            location:    "Cabinet {{ cabinet }}"
            duration_minutes: 30
            reminders_minutes: [1440, 60]   # J-1 + H-1

Mini-renderer partagé (cf. comms._template). Strict : variable manquante → ValueError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import _template

YAML_DIR = _template.YAML_DIR


@dataclass
class RenderedEvent:
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    duration_minutes: int = 30
    reminders_minutes: Optional[list[int]] = None


def reload_tenant_yaml(tenant_slug: Optional[str] = None) -> None:
    _template.reload_tenant_yaml()


def get_calendar_config(tenant_slug: str) -> dict:
    cfg = _template.load_tenant_yaml(tenant_slug, yaml_dir=YAML_DIR) or {}
    return ((cfg.get("comms") or {}).get("calendar") or {})


def list_templates(tenant_slug: str) -> list[str]:
    return list((get_calendar_config(tenant_slug).get("templates") or {}).keys())


def render(tenant_slug: str, template_id: str, context: dict) -> RenderedEvent:
    """Renvoie title + description + location rendus + duration + reminders."""
    templates = get_calendar_config(tenant_slug).get("templates") or {}
    tpl = templates.get(template_id)
    if not tpl:
        raise KeyError(f"template calendar '{template_id}' inconnu pour tenant {tenant_slug}")
    title_tpl = tpl.get("title")
    if not title_tpl:
        raise ValueError(f"template calendar '{template_id}' n'a pas de champ 'title'")
    ctx = context or {}
    return RenderedEvent(
        title=_template.render_string(title_tpl, ctx),
        description=_template.render_string(tpl["description"], ctx) if tpl.get("description") else None,
        location=_template.render_string(tpl["location"], ctx) if tpl.get("location") else None,
        duration_minutes=int(tpl.get("duration_minutes", 30)),
        reminders_minutes=list(tpl.get("reminders_minutes") or []),
    )


def render_inline(
    *,
    title: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    duration_minutes: int = 30,
    reminders_minutes: Optional[list[int]] = None,
    context: dict,
) -> RenderedEvent:
    if not title:
        raise ValueError("title requis")
    ctx = context or {}
    return RenderedEvent(
        title=_template.render_string(title, ctx),
        description=_template.render_string(description, ctx) if description else None,
        location=_template.render_string(location, ctx) if location else None,
        duration_minutes=int(duration_minutes),
        reminders_minutes=list(reminders_minutes or []),
    )
