"""bizzi.audience.routes — Endpoints FastAPI /api/audience/*.

ENGINE UNIVERSEL — aucune logique métier dans ce fichier. Toute la
configuration vient du YAML tenant via tenant_config.get_audience_config().

Les schemas Pydantic ci-dessous sont stables : le frontend SvelteKit du
command center s'appuiera dessus. Les `categories` sont volontairement
typées `list[str]` côté API (id seulement) — le rendu icône/couleur se
fait côté UI à partir de GET /api/audience/categories.

Wiring : ajouté à api/main.py via include_router(prefix='/api/audience').
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import event_bus, storage
from ._db import ensure_schema
from .ingestion.chatbot import parse_chatbot_payload
from .ingestion.facebook import parse_facebook_payload
from .ingestion.forms import parse_form_payload
from .ingestion.webhook import parse_generic_webhook
from .nlp import analyze, clean_and_anonymize, embed
from .tenant_config import (
    get_audience_config, resolve_tenant_id, resolve_tenant_slug,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Schéma DB créé au boot (idempotent).
ensure_schema()


# ── Schemas (stables — consommés par l'UI) ───────────────────────
class IngestBody(BaseModel):
    tenant_id: Optional[int] = None
    tenant_slug: Optional[str] = None
    source: str = Field(..., description="chatbot|forms|facebook|webhook|email|...")
    raw_message: str = Field(..., min_length=1)
    platform: Optional[str] = None
    author_name: Optional[str] = None
    author_external_id: Optional[str] = None
    city: Optional[str] = None
    org_unit_id: Optional[int] = Field(None, description="Unité org (section/fédération) à laquelle rattacher la remontée — sert au scoping iframe.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class CategorySchema(BaseModel):
    id: str
    label: str
    icon: Optional[str] = None
    color: Optional[str] = None


class ReportSchema(BaseModel):
    id: int
    tenant_id: int
    source: str
    platform: Optional[str] = None
    author_name: Optional[str] = None
    author_external_id: Optional[str] = None
    city: Optional[str] = None
    org_unit_id: Optional[int] = None
    raw_message: str
    cleaned_message: Optional[str] = None
    categories: list[str] = []
    subcategory: Optional[str] = None
    emotion: Optional[str] = None
    keywords: list[str] = []
    priority_score: int = 0
    language: Optional[str] = None
    metadata: dict[str, Any] = {}
    created_at: Optional[str] = None


class TrendSchema(BaseModel):
    id: int
    tenant_id: int
    category: str
    city: Optional[str] = None
    total_mentions_24h: int = 0
    total_mentions_7d: int = 0
    total_mentions_30d: int = 0
    trend_score: float = 0.0
    evolution_pct_7d: float = 0.0
    top_keywords: list[str] = []
    top_emotion: Optional[str] = None
    last_updated: Optional[str] = None


class AlertSchema(BaseModel):
    id: int
    tenant_id: int
    alert_type: str
    category: Optional[str] = None
    city: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    title: str
    description: Optional[str] = None
    status: str
    generated_content_proposals: list[dict[str, Any]] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ── Tenant resolution helper ─────────────────────────────────────
def _resolve_tenant(tenant_id: Optional[int], tenant_slug: Optional[str]) -> tuple[int, str]:
    if tenant_id is None and tenant_slug is None:
        raise HTTPException(400, "tenant_id ou tenant_slug requis")
    if tenant_id is None:
        rid = resolve_tenant_id(tenant_slug)  # type: ignore[arg-type]
        if rid is None:
            raise HTTPException(404, f"tenant inconnu : slug={tenant_slug}")
        tenant_id = rid
    if tenant_slug is None:
        tenant_slug = resolve_tenant_slug(tenant_id)
        if tenant_slug is None:
            raise HTTPException(404, f"tenant inconnu : id={tenant_id}")
    return int(tenant_id), tenant_slug


# ── INGEST (générique) ───────────────────────────────────────────
def _ingest_one(
    tenant_id: int,
    tenant_slug: str,
    source: str,
    raw_message: str,
    *,
    platform: Optional[str],
    author_name: Optional[str],
    author_external_id: Optional[str],
    city: Optional[str],
    metadata: dict[str, Any],
    org_unit_id: Optional[int] = None,
) -> dict[str, Any]:
    cfg = get_audience_config(tenant_slug)
    cleaned = clean_and_anonymize(raw_message)
    analysis = analyze(
        cleaned.cleaned,
        categories=cfg["categories"],
        priority_keywords_boost=cfg["priority_keywords_boost"],
        tenant_name=cfg["tenant_name"],
    )
    vec, mode = embed(cleaned.cleaned)

    md = dict(metadata or {})
    md["redactions"] = cleaned.redactions
    md["analysis_model"] = analysis.pop("model", None)
    md["embed_mode"] = mode

    row = storage.insert_report(
        tenant_id,
        source=source,
        raw_message=raw_message,
        cleaned_message=cleaned.cleaned,
        analysis=analysis,
        embedding=vec,
        platform=platform,
        author_name=author_name,
        author_external_id=author_external_id,
        city=city,
        org_unit_id=org_unit_id,
        metadata=md,
    )
    return row


@router.post("/ingest", response_model=ReportSchema, summary="Ingest générique (toutes sources)")
def ingest(body: IngestBody) -> dict[str, Any]:
    tenant_id, tenant_slug = _resolve_tenant(body.tenant_id, body.tenant_slug)
    return _ingest_one(
        tenant_id, tenant_slug,
        body.source, body.raw_message,
        platform=body.platform,
        author_name=body.author_name,
        author_external_id=body.author_external_id,
        city=body.city,
        org_unit_id=body.org_unit_id,
        metadata=body.metadata,
    )


@router.post("/webhook/{provider}", summary="Webhook source (facebook, zendesk, trustpilot, ...)")
def ingest_webhook(provider: str, payload: dict[str, Any], tenant_id: Optional[int] = None,
                   tenant_slug: Optional[str] = None) -> dict[str, Any]:
    tid, tslug = _resolve_tenant(tenant_id, tenant_slug)
    if provider == "facebook":
        msgs = parse_facebook_payload(payload, tenant_id=tid)
        if not msgs:
            return {"ingested": 0}
        out = []
        for m in msgs:
            out.append(_ingest_one(
                tid, tslug, m.source, m.raw_message,
                platform=m.platform, author_name=m.author_name,
                author_external_id=m.author_external_id, city=m.city,
                metadata=m.metadata,
            ))
        return {"ingested": len(out), "reports": out}
    if provider == "chatbot":
        m = parse_chatbot_payload(payload, tenant_id=tid)
    elif provider == "forms":
        m = parse_form_payload(payload, tenant_id=tid)
    else:
        m = parse_generic_webhook(provider, payload, tenant_id=tid)
    if not m.raw_message:
        raise HTTPException(400, "payload sans message exploitable")
    return _ingest_one(
        tid, tslug, m.source, m.raw_message,
        platform=m.platform, author_name=m.author_name,
        author_external_id=m.author_external_id, city=m.city,
        metadata=m.metadata,
    )


# ── READ endpoints ───────────────────────────────────────────────
@router.get("/categories", response_model=list[CategorySchema], summary="Catégories du YAML tenant")
def get_categories(tenant_id: Optional[int] = None, tenant_slug: Optional[str] = None) -> list[dict]:
    _, slug = _resolve_tenant(tenant_id, tenant_slug)
    cfg = get_audience_config(slug)
    return cfg["categories"]


@router.get("/reports", response_model=list[ReportSchema], summary="Liste des remontées")
def get_reports(
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    category: Optional[str] = None,
    city: Optional[str] = None,
    source: Optional[str] = None,
    emotion: Optional[str] = None,
    min_priority: Optional[int] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    tid, _ = _resolve_tenant(tenant_id, tenant_slug)
    return storage.list_reports(
        tid, category=category, city=city, source=source,
        emotion=emotion, min_priority=min_priority,
        limit=limit, offset=offset,
    )


@router.get("/trends", response_model=list[TrendSchema], summary="Tendances temps réel")
def get_trends(
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    tid, _ = _resolve_tenant(tenant_id, tenant_slug)
    return storage.list_trends(tid, city=city, limit=limit)


@router.get("/alerts", response_model=list[AlertSchema], summary="Alertes")
def get_alerts(
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    status: Optional[str] = "pending",
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    tid, _ = _resolve_tenant(tenant_id, tenant_slug)
    return storage.list_alerts(tid, status=status, limit=limit)


@router.post("/alerts/{alert_id}/dismiss", summary="Marque une alerte comme dismissed")
def dismiss(
    alert_id: int,
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
) -> dict[str, Any]:
    tid, _ = _resolve_tenant(tenant_id, tenant_slug)
    ok = storage.dismiss_alert(tid, alert_id)
    if not ok:
        raise HTTPException(404, "alert not found")
    return {"alert_id": alert_id, "status": "dismissed"}


# ── UI dedicated endpoints ───────────────────────────────────────
@router.get("/summary", summary="Widget HOME compact (top sujet, alertes count, mentions 24h)")
def summary(
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
) -> dict[str, Any]:
    tid, slug = _resolve_tenant(tenant_id, tenant_slug)
    trends = storage.list_trends(tid, limit=5)
    alerts = storage.list_alerts(tid, status="pending", limit=100)
    return {
        "tenant_id": tid,
        "tenant_slug": slug,
        "mentions_24h": storage.count_reports(tid, since_hours=24),
        "mentions_7d": storage.count_reports(tid, since_hours=168),
        "top_trends": trends[:3],
        "alerts_pending_count": len(alerts),
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/dashboard", summary="Vue dédiée plein écran (tout-en-un)")
def dashboard(
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    reports_limit: int = Query(50, ge=1, le=500),
    trends_limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    tid, slug = _resolve_tenant(tenant_id, tenant_slug)
    cfg = get_audience_config(slug)
    return {
        "tenant_id": tid,
        "tenant_slug": slug,
        "tenant_name": cfg["tenant_name"],
        "categories": cfg["categories"],
        "content_generation": cfg["content_generation"],
        "summary": {
            "mentions_24h": storage.count_reports(tid, since_hours=24),
            "mentions_7d": storage.count_reports(tid, since_hours=168),
        },
        "reports": storage.list_reports(tid, limit=reports_limit),
        "trends": storage.list_trends(tid, limit=trends_limit),
        "alerts": storage.list_alerts(tid, status="pending"),
        "live_recent": event_bus.recent(tid, limit=20),
    }


@router.get("/contextual", summary="Recherche par similarité d'embedding sur un sujet")
def contextual(
    topic: str = Query(..., min_length=2),
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    tid, _ = _resolve_tenant(tenant_id, tenant_slug)
    vec, mode = embed(topic)
    results = storage.search_by_embedding(tid, vec, limit=limit)
    return {"topic": topic, "embed_mode": mode, "results": results}


@router.get("/agent/{agent_id}/relevant", summary="Reports pertinents pour un agent (Phase 1 stub)")
def agent_relevant(
    agent_id: int,
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    tid, _ = _resolve_tenant(tenant_id, tenant_slug)
    # Phase 1 : filtrer selon agent.specialty / metier / categories.
    # Pour l'instant : derniers reports tenant, frontend appliquera sa logique.
    return {
        "agent_id": agent_id,
        "tenant_id": tid,
        "phase": "stub",
        "reports": storage.list_reports(tid, limit=limit),
    }


@router.post("/content/propose", summary="Force génération propositions (Phase 1 stub)")
def content_propose(
    alert_id: int,
    tenant_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
) -> dict[str, Any]:
    tid, slug = _resolve_tenant(tenant_id, tenant_slug)
    cfg = get_audience_config(slug)
    return {
        "alert_id": alert_id,
        "tenant_id": tid,
        "phase": "stub",
        "auto_propose_enabled": cfg["content_generation"]["auto_propose"],
        "proposals": [],
    }


# ── WebSocket live feed ──────────────────────────────────────────
@router.websocket("/stream")
async def stream(websocket: WebSocket, tenant_id: Optional[int] = None, tenant_slug: Optional[str] = None):
    """Live feed WebSocket — push chaque nouveau report en JSON.

    Frame format : {"type": "report.created", "data": {...ReportSchema...}}
    Première frame envoyée à la connexion : {"type": "hello", "tenant_id": ..., "recent": [...]}.
    Ping JSON toutes les 25s pour keepalive.
    """
    try:
        tid, slug = _resolve_tenant(tenant_id, tenant_slug)
    except HTTPException as e:
        await websocket.close(code=4001, reason=e.detail)
        return

    await websocket.accept()
    q = event_bus.subscribe(tid)
    try:
        await websocket.send_json({
            "type": "hello",
            "tenant_id": tid,
            "tenant_slug": slug,
            "recent": event_bus.recent(tid, limit=20),
        })
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=25.0)
                await websocket.send_json(ev)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "ts": datetime.utcnow().isoformat() + "Z"})
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("audience stream error: %s", e)
    finally:
        event_bus.unsubscribe(tid, q)
