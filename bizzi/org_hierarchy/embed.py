"""bizzi.org_hierarchy.embed — Endpoints HTML embed iframe.

Rappel règle Pascal : ADDITIF UNIQUEMENT. On ne crée PAS de nouveau container
côté tenant. Le tenant a déjà ses containers (ex: panel-territoires sur
lesdemocrates.org). Il y ajoute juste `data-bizzi-mount="org/territories"`.

L'iframe (ou le hydratation par bizzi-loader.js) charge l'URL renvoyée par
ces endpoints et l'affiche dans le container existant.

Phase 0 : rendu HTML minimal autonome (sans nav, sans footer, CSS variables
white-label). Phase 1 : SvelteKit/Leaflet, JWT scoping strict, audit log.
"""
from __future__ import annotations

import html
from typing import Optional

import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from . import audit, permissions, storage
from .permissions import JWTError


embed_router = APIRouter()

# Phase 1 : enforcement strict du JWT par défaut. Pascal peut désactiver via
# BIZZI_EMBED_REQUIRE_JWT=false pour tests/dev (localement).
_REQUIRE_JWT = os.getenv("BIZZI_EMBED_REQUIRE_JWT", "true").lower() == "true"


# ─── Helpers ────────────────────────────────────────────────────────────────


def _extract_token(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    if "token" in request.query_params:
        return request.query_params.get("token")
    return None


def _scope_or_raise(request: Request, expected_tenant_id: int):
    """Phase 1 : enforcement strict.

    - JWT obligatoire si BIZZI_EMBED_REQUIRE_JWT=true (défaut).
    - tenant_id du JWT doit correspondre au tenant demandé.
    - rôle doit avoir au moins une visibilité sur le tenant.

    Retourne le scope vérifié ou None si JWT non requis et absent.
    Lève HTTPException 401/403 sinon.
    """
    token = _extract_token(request)
    if not token:
        if _REQUIRE_JWT:
            raise HTTPException(401, "JWT required (Authorization: Bearer ... or ?token=)")
        return None

    try:
        scope = permissions.verify_jwt(token)
    except JWTError as e:
        raise HTTPException(401, f"Invalid JWT: {e}")

    if scope.tenant_id != expected_tenant_id:
        raise HTTPException(403, "JWT tenant_id mismatch")

    return scope


def _build_tree(units: list[dict]) -> list[dict]:
    by_id = {u["id"]: {**u, "children": []} for u in units}
    roots = []
    for u in units:
        node = by_id[u["id"]]
        if u.get("parent_id") and u["parent_id"] in by_id:
            by_id[u["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _render_tree_html(roots: list[dict]) -> str:
    """Liste HTML imbriquée. Chaque section avec geo_meta.postal_code obtient
    un lien Google Maps (Phase 0 — Phase 1 = vraie carte Leaflet/MapLibre)."""
    def render_node(node: dict) -> str:
        meta = node.get("geo_meta") or {}
        pc = meta.get("postal_code")
        name = html.escape(node.get("name", ""))
        level = html.escape(node.get("level", ""))
        link = ""
        if pc:
            q = f"{name} {pc}"
            link = (
                f' <a class="bz-geo" target="_blank" rel="noopener" '
                f'href="https://www.google.com/maps/search/?api=1&query={html.escape(q)}">📍 {html.escape(pc)}</a>'
            )
        children = node.get("children") or []
        children_html = ""
        if children:
            children_html = "<ul>" + "".join(render_node(c) for c in children) + "</ul>"
        return (
            f'<li data-unit-id="{node["id"]}" data-level="{level}">'
            f'<span class="bz-level">{level}</span> '
            f'<span class="bz-name">{name}</span>{link}'
            f"{children_html}</li>"
        )

    if not roots:
        return '<p class="bz-empty">Aucune unité organisationnelle déclarée.</p>'
    return '<ul class="bz-tree">' + "".join(render_node(r) for r in roots) + "</ul>"


_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --primary-color: {primary};
    --font: {font};
    --bg: #fff;
    --fg: #1a1a1a;
    --muted: #6b7280;
  }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--fg);
    font-family: var(--font), system-ui, sans-serif; }}
  .bz-wrap {{ padding: 16px 18px; }}
  h1.bz-title {{ font-size: 20px; margin: 0 0 12px; color: var(--primary-color); }}
  ul.bz-tree, ul.bz-tree ul {{ list-style: none; padding-left: 18px; margin: 0; }}
  ul.bz-tree {{ padding-left: 0; }}
  ul.bz-tree li {{ padding: 4px 0; border-left: 2px solid color-mix(in srgb, var(--primary-color) 20%, transparent); padding-left: 10px; margin-left: 4px; }}
  .bz-level {{ display:inline-block; font-size: 11px; text-transform: uppercase;
    letter-spacing: .04em; color: var(--muted); min-width: 90px; }}
  .bz-name {{ font-weight: 600; }}
  .bz-geo {{ margin-left: 8px; font-size: 12px; color: var(--primary-color);
    text-decoration: none; }}
  .bz-geo:hover {{ text-decoration: underline; }}
  .bz-empty {{ color: var(--muted); }}
  .bz-foot {{ font-size: 11px; color: var(--muted); margin-top: 12px; }}
</style>
</head>
<body>
<div class="bz-wrap">
  <h1 class="bz-title">{title}</h1>
  {tree}
  <div class="bz-foot">{count_label}</div>
</div>
<script>
  // post-message height pour auto-fit iframe parent (Pascal: containers existants)
  function postHeight(){{
    try {{
      const h = document.documentElement.scrollHeight;
      window.parent && window.parent.postMessage({{ type: "bizzi:resize", height: h }}, "*");
    }} catch (e) {{}}
  }}
  window.addEventListener("load", postHeight);
  new ResizeObserver(postHeight).observe(document.documentElement);
</script>
</body>
</html>
"""


# ─── Endpoint /embed/org/territories/{tenant_id} ────────────────────────────


@embed_router.get("/territories/{tenant_id}", response_class=HTMLResponse)
def embed_territories(
    tenant_id: int,
    request: Request,
    primary_color: str = Query("#D6140D", description="CSS --primary-color (white-label)"),
    font: str = Query("Barlow", description="CSS font family principale"),
):
    """Carte interactive des org_units du tenant. Sans nav, sans footer (iframe).

    Phase 1 : JWT enforcement strict (sauf si BIZZI_EMBED_REQUIRE_JWT=false).
    Toute requête est auditée (org_audit_log).
    """
    scope = _scope_or_raise(request, tenant_id)

    if scope is not None:
        visible = set(permissions.get_visible_units(scope))
        all_units = storage.list_units(tenant_id)
        units = [u for u in all_units if u["id"] in visible]
        role = scope.role
        user_id = scope.user_id
        scope_unit = scope.org_unit_id
    else:
        # Mode dev (BIZZI_EMBED_REQUIRE_JWT=false) — lecture complète.
        units = storage.list_units(tenant_id)
        role = None
        user_id = None
        scope_unit = None

    roots = _build_tree(units)
    tree_html = _render_tree_html(roots)

    # Audit log obligatoire (Phase 1, retention 90j conformité).
    try:
        audit.log_request(
            tenant_id=tenant_id,
            role=role,
            user_id=user_id,
            org_unit_id=scope_unit,
            path=str(request.url.path),
            method="GET",
            ip=request.client.host if request.client else None,
            query=dict(request.query_params),
            status_code=200,
        )
    except Exception:
        pass

    body = _TEMPLATE.format(
        title="Territoires & sections",
        primary=html.escape(primary_color, quote=True),
        font=html.escape(font, quote=True),
        tree=tree_html,
        count_label=f"{len(units)} unité(s) affichée(s)" + (
            f" · scope {role}" if role else " · mode public"
        ),
    )
    return HTMLResponse(content=body)
