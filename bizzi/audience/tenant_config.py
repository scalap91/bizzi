"""bizzi.audience.tenant_config — Lecture de la section `audience:` du YAML.

Engine universel : aucune valeur sectorielle codée en dur. Tout vient
du fichier domains/<slug>.yaml. Cache en mémoire (TTL léger) pour éviter
de relire le YAML à chaque requête.

Forme attendue du YAML (cf. brief universel) :

    audience:
      enabled: true
      monthly_budget_eur: 30
      sources:
        chatbot:        { enabled: true }
        facebook:       { enabled: false, page_id: "..." }
        forms:          { enabled: true }
        webhook_zendesk:{ enabled: false }
      categories:
        # forme libre : string OU dict {id,label,icon,color}
        - logement
        - { id: securite, label: "Sécurité", icon: "🛡️" }
      priority_keywords_boost:
        +5: ["agression", "rats"]
        +3: ["loyer"]
      alerts:
        threshold_explosion_pct: 30
        notify: pascal@example.fr
      content_generation:
        enabled: true
        auto_propose:
          reply_text: true
          facebook_post: false
          ticket_zendesk: false
          faq_entry: false
          bug_issue: false
          improvement_idea: true
          synthesis_report: true
        require_validation: true
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import yaml

from ._db import get_conn

# Le repo monte les YAML tenant ici. Un override via env reste possible
# pour les tests ; pas de fallback codé en dur sur d'autres chemins.
DOMAINS_DIR = os.environ.get(
    "BIZZI_DOMAINS_DIR",
    "/opt/bizzi/bizzi/domains",
)

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_SEC = 60.0


def resolve_tenant_slug(tenant_id: int) -> Optional[str]:
    """Résout tenant.id → tenant.slug via la table tenants."""
    with get_conn(dict_rows=True) as c, c.cursor() as cur:
        cur.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row["slug"] if row else None


def resolve_tenant_id(tenant_slug: str) -> Optional[int]:
    with get_conn(dict_rows=True) as c, c.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (tenant_slug,))
        row = cur.fetchone()
        return row["id"] if row else None


def load_tenant_yaml(tenant_slug: str) -> dict[str, Any]:
    """Charge le YAML complet du tenant. Cache 60s."""
    now = time.time()
    cached = _CACHE.get(tenant_slug)
    if cached and (now - cached[0]) < _TTL_SEC:
        return cached[1]
    path = os.path.join(DOMAINS_DIR, f"{tenant_slug}.yaml")
    if not os.path.exists(path):
        _CACHE[tenant_slug] = (now, {})
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _CACHE[tenant_slug] = (now, data)
    return data


def normalize_category(item: Any) -> dict[str, Any]:
    """Accepte une catégorie sous forme de string ou dict.

    Retourne {id, label, icon?, color?}.
    """
    if isinstance(item, str):
        return {"id": item, "label": item}
    if isinstance(item, dict):
        cid = item.get("id") or item.get("label")
        if not cid:
            raise ValueError(f"category sans id/label : {item!r}")
        out = {"id": str(cid), "label": item.get("label", str(cid))}
        if "icon" in item:
            out["icon"] = item["icon"]
        if "color" in item:
            out["color"] = item["color"]
        return out
    raise ValueError(f"category non reconnue : {item!r}")


def get_audience_config(tenant_slug: str) -> dict[str, Any]:
    """Retourne la section `audience:` normalisée pour ce tenant.

    Si la section est absente, retourne un dict avec `enabled: False` et
    une liste vide de catégories — l'engine reste opérant mais en mode
    classification "autres" uniquement.
    """
    yml = load_tenant_yaml(tenant_slug)
    audience = (yml or {}).get("audience", {}) or {}

    raw_cats = audience.get("categories", []) or []
    categories = [normalize_category(c) for c in raw_cats]

    # priority_keywords_boost : YAML peut avoir des clés int OU strings "+5"
    raw_boost = audience.get("priority_keywords_boost", {}) or {}
    boost: dict[int, list[str]] = {}
    for k, v in raw_boost.items():
        try:
            key_int = int(str(k).replace("+", "").strip())
        except ValueError:
            continue
        boost[key_int] = list(v) if isinstance(v, (list, tuple)) else []

    sources = audience.get("sources", {}) or {}
    alerts_cfg = audience.get("alerts", {}) or {}
    cg = audience.get("content_generation", {}) or {}

    return {
        "enabled": bool(audience.get("enabled", False)),
        "monthly_budget_eur": audience.get("monthly_budget_eur"),
        "sources": sources,
        "categories": categories,
        "category_ids": [c["id"] for c in categories],
        "priority_keywords_boost": boost,
        "alerts": {
            "threshold_explosion_pct": float(alerts_cfg.get("threshold_explosion_pct", 30)),
            "notify": alerts_cfg.get("notify"),
            "notify_channel": alerts_cfg.get("notify_channel"),
        },
        "content_generation": {
            "enabled": bool(cg.get("enabled", False)),
            "auto_propose": cg.get("auto_propose", {}) or {},
            "require_validation": bool(cg.get("require_validation", True)),
        },
        "tenant_name": (yml.get("identity") or {}).get("name") or tenant_slug,
    }


def invalidate_cache(tenant_slug: Optional[str] = None) -> None:
    if tenant_slug is None:
        _CACHE.clear()
    else:
        _CACHE.pop(tenant_slug, None)
