"""Résolution des templates email par tenant.

Format YAML attendu :

    comms:
      mail:
        templates:
          welcome:
            subject: "Bienvenue {{ first_name }} !"
            html:    "<h1>Bienvenue {{ first_name }}</h1><p>Merci de rejoindre {{ org }}.</p>"
            text:    "Bienvenue {{ first_name }}, merci de rejoindre {{ org }}."

Mini-renderer partagé (cf. comms._template). Strict : variable manquante → ValueError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import _template

YAML_DIR = _template.YAML_DIR


@dataclass
class RenderedMail:
    subject: str
    html: Optional[str] = None
    text: Optional[str] = None


def reload_tenant_yaml(tenant_slug: Optional[str] = None) -> None:
    _template.reload_tenant_yaml()


def get_mail_config(tenant_slug: str) -> dict:
    cfg = _template.load_tenant_yaml(tenant_slug, yaml_dir=YAML_DIR) or {}
    return ((cfg.get("comms") or {}).get("mail") or {})


def list_templates(tenant_slug: str) -> list[str]:
    return list((get_mail_config(tenant_slug).get("templates") or {}).keys())


def render(tenant_slug: str, template_id: str, context: dict) -> RenderedMail:
    """Renvoie (subject, html, text) rendus. Lève KeyError si template inconnu."""
    templates = get_mail_config(tenant_slug).get("templates") or {}
    tpl = templates.get(template_id)
    if not tpl:
        raise KeyError(f"template mail '{template_id}' inconnu pour tenant {tenant_slug}")
    subject_tpl = tpl.get("subject")
    if not subject_tpl:
        raise ValueError(f"template mail '{template_id}' n'a pas de champ 'subject'")
    if not (tpl.get("html") or tpl.get("text")):
        raise ValueError(f"template mail '{template_id}' n'a ni 'html' ni 'text'")
    ctx = context or {}
    return RenderedMail(
        subject=_template.render_string(subject_tpl, ctx),
        html=_template.render_string(tpl["html"], ctx) if tpl.get("html") else None,
        text=_template.render_string(tpl["text"], ctx) if tpl.get("text") else None,
    )


def render_inline(
    *, subject: str, html: Optional[str] = None, text: Optional[str] = None, context: dict
) -> RenderedMail:
    """Rendu d'un template ad-hoc (non issu du yaml). Utile pour tests / appels API directs."""
    if not subject:
        raise ValueError("subject requis")
    if not (html or text):
        raise ValueError("au moins un de html/text requis")
    ctx = context or {}
    return RenderedMail(
        subject=_template.render_string(subject, ctx),
        html=_template.render_string(html, ctx) if html else None,
        text=_template.render_string(text, ctx) if text else None,
    )
