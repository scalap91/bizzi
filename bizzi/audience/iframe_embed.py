"""bizzi.audience.embed — Endpoints iframe white-label tenant.

Pattern Pascal : ZÉRO nouveau container HTML côté tenant. Bizzi se branche
via `bizzi-loader.js` qui lit `data-bizzi-mount="audience/<niveau>"` sur
des containers EXISTANTS et y injecte une iframe pointant vers les
endpoints ci-dessous.

Endpoints :
  GET  /embed/audience/section/{id}      HTML scopé section (1 unité)
  GET  /embed/audience/federation/{id}   HTML scopé fédération
  GET  /embed/audience/national/{id}     HTML scopé national (= tenant)
  WS   /embed/audience/stream            Live feed scopé via JWT
  GET  /embed/audience/loader.js         Script bizzi-loader distribuable

Sécurité :
- JWT signé HS256 avec clé partagée tenant ↔ Bizzi (env BIZZI_AUDIENCE_JWT_SECRET)
- Vérif tenant_id présent en DB
- Filtrage queries via `visible_units(scope)` (orghierarchy_client)
- Audit log toutes requêtes (audience_embed_audit, rétention 90j)

Auto-fit : la page embed envoie `postMessage({type:'bizzi-resize', height:H})`
au parent à chaque changement de taille — `bizzi-loader.js` ajuste l'iframe.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from . import event_bus, storage
from .auth import JWTClaims, JWTError, decode_jwt
from .orghierarchy_client import get_visible_units
from .tenant_config import resolve_tenant_slug, get_audience_config

logger = logging.getLogger(__name__)
router = APIRouter()

_LOADER_JS_PATH = os.path.join(os.path.dirname(__file__), "static", "bizzi-loader.js")


# ── Auth helper ──────────────────────────────────────────────────
def _verify_jwt_or_401(token: Optional[str]) -> JWTClaims:
    if not token:
        raise HTTPException(401, "missing token")
    try:
        claims = decode_jwt(token.strip())
    except JWTError as e:
        raise HTTPException(401, f"invalid token: {e}") from None
    return claims


def _check_scope_consistency(claims: JWTClaims, scope_kind: str, scope_id: int) -> None:
    """Refuse si le JWT ne couvre pas le niveau demandé.

    section : claims.org_unit_id == scope_id (sauf admin)
    federation : claims.role admin OU 'federation' avec org_unit_id == scope_id
    national : claims.role doit être admin/national/tenant_admin
    """
    if claims.role in {"admin", "tenant_admin", "owner"}:
        return  # admin tenant : tout autorisé sur SON tenant
    if scope_kind == "national":
        if claims.role not in {"national"}:
            raise HTTPException(403, "scope national requires admin/national role")
        return
    if scope_kind == "federation":
        if claims.role not in {"federation", "national"}:
            raise HTTPException(403, "scope federation requires federation+ role")
        if claims.org_unit_id is not None and int(claims.org_unit_id) != int(scope_id):
            raise HTTPException(403, "scope federation id mismatch")
        return
    if scope_kind == "section":
        if claims.org_unit_id is None:
            raise HTTPException(403, "no org_unit_id in token")
        if int(claims.org_unit_id) != int(scope_id):
            raise HTTPException(403, "scope section id mismatch")
        return
    raise HTTPException(400, f"unknown scope kind: {scope_kind}")


def _audit(request: Request, claims: JWTClaims, endpoint: str,
           visible_units: Optional[list[int]], status_code: int) -> None:
    storage.log_embed_access(
        claims.tenant_id,
        endpoint=endpoint,
        org_unit_id=claims.org_unit_id,
        role=claims.role,
        user_ref=claims.user_ref,
        visible_units=visible_units,
        ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        request_id=request.headers.get("x-request-id"),
        status_code=status_code,
    )


# ── HTML embed renderer ──────────────────────────────────────────
def _render_embed_html(
    *,
    title: str,
    primary_color: str,
    summary: dict[str, Any],
    reports: list[dict[str, Any]],
    trends: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    scope_label: str,
    tenant_slug: str,
    token: str,
    api_base: str,
) -> str:
    """HTML minimaliste auto-fit, pas de framework imposé.

    CSS variables --bizzi-primary / --bizzi-font configurables.
    JS inline gère : refresh fetch + WebSocket live feed + post-message resize.
    Aucune dépendance externe, ~3 Ko gzippé.
    """
    cats_by_id = {c["id"]: c for c in categories}

    def _cat_chip(cid: str) -> str:
        c = cats_by_id.get(cid, {"id": cid, "label": cid})
        icon = c.get("icon") or ""
        color = c.get("color") or "var(--bizzi-primary)"
        return (
            f'<span class="b-chip" style="background:{color}1A;color:{color};">'
            f'{icon} {c.get("label", cid)}</span>'
        )

    def _report_row(r: dict[str, Any]) -> str:
        chips = "".join(_cat_chip(c) for c in (r.get("categories") or []))
        prio = int(r.get("priority_score") or 0)
        prio_cls = "p-hi" if prio >= 7 else "p-mid" if prio >= 4 else "p-lo"
        msg = (r.get("cleaned_message") or r.get("raw_message") or "")[:240]
        meta_bits = []
        if r.get("city"):
            meta_bits.append(r["city"])
        if r.get("source"):
            meta_bits.append(r["source"])
        if r.get("emotion"):
            meta_bits.append(r["emotion"])
        meta = " · ".join(meta_bits)
        return (
            f'<li class="b-report"><div class="b-row"><span class="b-prio {prio_cls}">'
            f'{prio}</span>{chips}</div>'
            f'<div class="b-msg">{_escape(msg)}</div>'
            f'<div class="b-meta">{_escape(meta)}</div></li>'
        )

    def _trend_row(t: dict[str, Any]) -> str:
        chip = _cat_chip(t.get("category", ""))
        return (
            f'<li class="b-trend">{chip}'
            f'<span class="b-num">{t.get("total_mentions_24h",0)}</span>'
            f'<span class="b-meta">24h · {t.get("city") or "tout"}</span></li>'
        )

    reports_html = "".join(_report_row(r) for r in reports[:20]) or '<li class="b-empty">Aucune remontée pour ce scope.</li>'
    trends_html = "".join(_trend_row(t) for t in trends[:10]) or '<li class="b-empty">Pas de tendance.</li>'
    alerts_html = (
        "".join(
            f'<li class="b-alert"><b>{_escape(a.get("title",""))}</b>'
            f'<div class="b-meta">{_escape(a.get("description") or "")}</div></li>'
            for a in alerts[:10]
        )
        or '<li class="b-empty">Aucune alerte.</li>'
    )

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)}</title>
<style>
:root {{
  --bizzi-primary: {primary_color};
  --bizzi-font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family: var(--bizzi-font); color:#1a1a1a; background:transparent; }}
.b-wrap {{ padding: 12px 14px; }}
.b-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap:8px; flex-wrap:wrap; }}
.b-title {{ font-size: 14px; font-weight: 600; color: var(--bizzi-primary); margin:0; }}
.b-scope {{ font-size: 11px; color:#666; }}
.b-stats {{ display:flex; gap:14px; font-size:12px; margin-bottom:10px; }}
.b-stats b {{ display:block; font-size:18px; color: var(--bizzi-primary); }}
.b-section {{ margin-top: 14px; }}
.b-section h3 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color:#666; margin: 0 0 6px; }}
ul {{ list-style: none; padding:0; margin:0; }}
.b-report, .b-trend, .b-alert {{ padding:8px 0; border-top: 1px solid #eee; }}
.b-row {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:4px; }}
.b-msg {{ font-size: 13px; line-height: 1.4; }}
.b-meta {{ font-size: 11px; color:#888; margin-top: 2px; }}
.b-chip {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; }}
.b-prio {{ display:inline-block; min-width: 22px; text-align:center; padding: 2px 6px; border-radius: 4px; font-weight:600; font-size: 11px; color:#fff; }}
.p-lo {{ background:#9ca3af; }} .p-mid {{ background:#f59e0b; }} .p-hi {{ background:#dc2626; }}
.b-trend .b-num {{ font-weight:700; margin-left:auto; color: var(--bizzi-primary); }}
.b-empty {{ font-size: 12px; color:#aaa; padding: 6px 0; }}
.b-live-dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:#10b981; box-shadow:0 0 0 0 #10b981aa; animation: pulse 1.6s infinite; margin-right:4px; vertical-align:middle; }}
@keyframes pulse {{ 0%{{box-shadow:0 0 0 0 #10b98166}} 70%{{box-shadow:0 0 0 8px #10b98100}} 100%{{box-shadow:0 0 0 0 #10b98100}} }}
</style>
</head>
<body>
<div class="b-wrap" id="b-wrap">
  <div class="b-header">
    <h1 class="b-title">{_escape(title)}</h1>
    <span class="b-scope"><span class="b-live-dot"></span>{_escape(scope_label)}</span>
  </div>
  <div class="b-stats">
    <div><b>{summary.get("mentions_24h",0)}</b><span>24h</span></div>
    <div><b>{summary.get("mentions_7d",0)}</b><span>7j</span></div>
    <div><b>{len(alerts)}</b><span>alertes</span></div>
  </div>
  <div class="b-section">
    <h3>Tendances</h3>
    <ul id="b-trends">{trends_html}</ul>
  </div>
  <div class="b-section">
    <h3>Alertes</h3>
    <ul id="b-alerts">{alerts_html}</ul>
  </div>
  <div class="b-section">
    <h3>Dernières remontées</h3>
    <ul id="b-reports">{reports_html}</ul>
  </div>
</div>
<script>
(function() {{
  var TOKEN = {token!r};
  var API   = {api_base!r};

  // Auto-fit : envoie sa hauteur au parent.
  function postSize() {{
    var h = document.getElementById('b-wrap').scrollHeight + 24;
    if (window.parent && window.parent !== window) {{
      window.parent.postMessage({{ type: 'bizzi-resize', height: h }}, '*');
    }}
  }}
  new ResizeObserver(postSize).observe(document.getElementById('b-wrap'));
  window.addEventListener('load', postSize);

  // Live feed : WebSocket scopé par JWT.
  try {{
    var wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://')
              + location.host + '/embed/audience/stream?token=' + encodeURIComponent(TOKEN);
    var ws = new WebSocket(wsUrl);
    ws.onmessage = function(ev) {{
      try {{
        var msg = JSON.parse(ev.data);
        if (msg.type === 'report.created') prependReport(msg.data);
      }} catch(e) {{}}
    }};
  }} catch(e) {{ /* live feed best-effort */ }}

  function escape(s) {{
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {{
      return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}}[c];
    }});
  }}
  function prependReport(r) {{
    var ul = document.getElementById('b-reports');
    if (!ul) return;
    var li = document.createElement('li');
    li.className = 'b-report';
    var prio = parseInt(r.priority_score || 0, 10);
    var pcls = prio >= 7 ? 'p-hi' : prio >= 4 ? 'p-mid' : 'p-lo';
    var meta = [r.city, r.source, r.emotion].filter(Boolean).join(' · ');
    li.innerHTML =
      '<div class="b-row"><span class="b-prio '+pcls+'">'+prio+'</span></div>'
      + '<div class="b-msg">'+escape((r.cleaned_message||r.raw_message||'').slice(0,240))+'</div>'
      + '<div class="b-meta">'+escape(meta)+'</div>';
    ul.insertBefore(li, ul.firstChild);
    postSize();
  }}
}})();
</script>
</body>
</html>"""


def _escape(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


# ── Endpoints HTML ───────────────────────────────────────────────
def _render_for_scope(
    request: Request,
    *,
    scope_kind: str,
    scope_id: int,
    token: Optional[str],
) -> Response:
    claims = _verify_jwt_or_401(token)
    _check_scope_consistency(claims, scope_kind, scope_id)

    tslug = resolve_tenant_slug(claims.tenant_id)
    if not tslug:
        _audit(request, claims, f"embed/audience/{scope_kind}/{scope_id}", None, 404)
        raise HTTPException(404, f"tenant_id {claims.tenant_id} not found")

    visible_units = get_visible_units(claims)
    cfg = get_audience_config(tslug)

    summary = {
        "mentions_24h": storage.count_reports(claims.tenant_id, since_hours=24, visible_units=visible_units),
        "mentions_7d": storage.count_reports(claims.tenant_id, since_hours=168, visible_units=visible_units),
    }
    reports = storage.list_reports(claims.tenant_id, visible_units=visible_units, limit=20)
    trends = storage.list_trends(claims.tenant_id, limit=10)
    alerts = storage.list_alerts(claims.tenant_id, status="pending", limit=10)

    primary_color = "#1e40af"  # default fallback ; tenant peut override via UI YAML
    yaml_ui = (cfg.get("ui") or {}) if isinstance(cfg, dict) else {}
    if isinstance(yaml_ui, dict) and yaml_ui.get("primary_color"):
        primary_color = yaml_ui["primary_color"]

    title_map = {"section": "Audience — Section", "federation": "Audience — Fédération", "national": "Audience — National"}
    scope_label_map = {
        "section":    f"section #{scope_id}",
        "federation": f"fédération #{scope_id}",
        "national":   "national",
    }

    html = _render_embed_html(
        title=title_map.get(scope_kind, "Audience"),
        primary_color=primary_color,
        summary=summary,
        reports=reports,
        trends=trends,
        alerts=alerts,
        categories=cfg["categories"],
        scope_label=scope_label_map.get(scope_kind, scope_kind),
        tenant_slug=tslug,
        token=token or "",
        api_base="/api/audience",
    )
    _audit(request, claims, f"embed/audience/{scope_kind}/{scope_id}", visible_units, 200)
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "frame-ancestors *",  # iframe-friendly
        "X-Frame-Options": "ALLOWALL",
    })


@router.get("/audience/section/{section_id}", response_class=HTMLResponse, summary="Embed scopé section")
def embed_section(section_id: int, request: Request, token: str = Query(...)) -> Response:
    return _render_for_scope(request, scope_kind="section", scope_id=section_id, token=token)


@router.get("/audience/federation/{fed_id}", response_class=HTMLResponse, summary="Embed scopé fédération")
def embed_federation(fed_id: int, request: Request, token: str = Query(...)) -> Response:
    return _render_for_scope(request, scope_kind="federation", scope_id=fed_id, token=token)


@router.get("/audience/national/{tenant_id}", response_class=HTMLResponse, summary="Embed scopé national")
def embed_national(tenant_id: int, request: Request, token: str = Query(...)) -> Response:
    return _render_for_scope(request, scope_kind="national", scope_id=tenant_id, token=token)


# ── WebSocket scopé ──────────────────────────────────────────────
@router.websocket("/audience/stream")
async def embed_stream(websocket: WebSocket, token: Optional[str] = Query(None)):
    if not token:
        await websocket.close(code=4001, reason="missing token")
        return
    try:
        claims = decode_jwt(token)
    except JWTError as e:
        await websocket.close(code=4001, reason=f"invalid token: {e}")
        return

    visible_units = get_visible_units(claims)
    await websocket.accept()
    q = event_bus.subscribe(claims.tenant_id)
    try:
        recent = event_bus.recent(claims.tenant_id, limit=20)
        if visible_units is not None:
            recent = [
                e for e in recent
                if (e.get("data") or {}).get("org_unit_id") in visible_units
            ]
        await websocket.send_json({
            "type": "hello",
            "tenant_id": claims.tenant_id,
            "scope_role": claims.role,
            "org_unit_id": claims.org_unit_id,
            "recent": recent,
        })
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=25.0)
                if visible_units is not None:
                    org_id = (ev.get("data") or {}).get("org_unit_id")
                    if org_id is not None and org_id not in visible_units:
                        continue
                await websocket.send_json(ev)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "ts": datetime.utcnow().isoformat() + "Z"})
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("embed stream error: %s", e)
    finally:
        event_bus.unsubscribe(claims.tenant_id, q)


# ── Loader JS distribuable ───────────────────────────────────────
@router.get("/audience/loader.js", response_class=PlainTextResponse, summary="Script bizzi-loader (distribué tel quel)")
def embed_loader_js() -> Response:
    if not os.path.exists(_LOADER_JS_PATH):
        raise HTTPException(500, "loader.js not deployed")
    with open(_LOADER_JS_PATH, "rb") as f:
        body = f.read()
    return Response(
        content=body,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )
