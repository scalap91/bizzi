"""bizzi.org_hierarchy — Dimension hiérarchique organisationnelle GÉNÉRIQUE multi-tenant.

Module agnostique : fonctionne pour parti politique (section → fédération → national),
syndicat, franchise, banque, ONG, hôpitaux, église, etc. Le tenant définit SA
hiérarchie via la section `org_hierarchy:` de son YAML domains/<slug>.yaml.

Capacités exposées au moteur :
- CRUD org_units + geo_mapping (storage.py)
- Loader YAML idempotent (yaml_loader.populate_from_yaml)
- Helper de scoping JWT pour bizzi-audience (permissions.get_visible_units)
- Rollup org_aggregations cron-able (rollup.run_rollup)
- Broadcasts national → sections (broadcast.create_broadcast)
- Endpoints REST /api/org/* (routes.py) + /embed/org/* (embed.py)

Coordination :
- bizzi-audience : audience_reports.org_unit_id partagé, filtre via get_visible_units(scope)
- tools.regions.region_detector : fallback FR hardcoded dans resolve_city_with_fallback
- frontend (futur bizzi-frontend-cc) : iframe /embed/org/territories ou bizzi-loader.js
  data-bizzi-mount sur containers existants

Tables DB (migrations/001_org_hierarchy.sql) :
- org_units, geo_mapping, org_aggregations, org_broadcasts, org_audit_log

Phase 0 = scaffold + DDL + loader YAML + helper scope + endpoints lecture (livré).
Phase 1 = aggregations rollup, broadcasts, JWT enforcement strict embed,
          audit export+purge, mapping région canonique (livré).
Phase 1.5 = trend_pct (vs période précédente), embed dashboards section/fed/national,
            auto-program-generation triggers, retention cron périodique.
"""
from .permissions import (
    JWTScope,
    JWTError,
    verify_jwt,
    issue_jwt,
    get_visible_units,
    can_broadcast,
)
from .yaml_loader import populate_from_yaml, get_org_hierarchy_section
from . import storage

__all__ = [
    "JWTScope",
    "JWTError",
    "verify_jwt",
    "issue_jwt",
    "get_visible_units",
    "can_broadcast",
    "populate_from_yaml",
    "get_org_hierarchy_section",
    "storage",
]


def get_routers():
    """Helper de wiring : retourne les deux routers à monter dans api/main.py.

    Usage :
        from org_hierarchy import get_routers
        api_router, embed_router = get_routers()
        app.include_router(api_router,   prefix="/api/org",   tags=["OrgHierarchy"])
        app.include_router(embed_router, prefix="/embed/org", tags=["OrgEmbed"])
    """
    from .routes import router as api_router
    from .embed import embed_router

    return api_router, embed_router
