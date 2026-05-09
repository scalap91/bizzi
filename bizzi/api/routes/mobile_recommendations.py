"""api/routes/mobile_recommendations.py — Recommandations + tracking commissions Bizzi Mobile.

C'est THE pierre angulaire du flywheel monétisation : quand un agent perso recommande
un tenant Bizzi, on track le clic, la conversion, et on calcule la commission due.

Endpoints (auth JWT user obligatoire pour user-facing ; admin via X-Admin-Token) :

User :
- POST  /api/mobile/recommendations/recommend           : agent crée une reco
- POST  /api/mobile/recommendations/{id}/clicked        : user a cliqué le deal
- GET   /api/mobile/recommendations/                    : historique reco user
- POST  /api/mobile/recommendations/{id}/conversion     : tenant notifie conversion (idem,
                                                          mais accepté aussi via X-Admin-Token
                                                          ou X-Tenant-Token = TENANT_TOKEN)

Admin (Pascal) :
- GET  /api/mobile/recommendations/admin/stats          : commissions par tenant ce mois
- GET  /api/mobile/recommendations/admin/unpaid         : commissions dues non payées
- POST /api/mobile/recommendations/admin/{id}/mark_paid : marque commission payée
"""
from __future__ import annotations

import os
from typing import Optional, List
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel, Field

from api.routes.auth import get_current_user, _db

router = APIRouter()


# ───────────────────────── Grille commissions ─────────────────────────
# V1 hardcodée. V2 → table dédiée `mobile_commission_rules` configurable par Pascal.
# Format : (tenant, recommendation_type) -> commission € fixe ou % conversion_value
# fixed = montant fixe par conversion ; rate = % de conversion_value

COMMISSION_GRID = {
    # airbizness — voyage
    ("airbizness", "flight"):           {"fixed": 5.00,  "rate": 0.0,  "label": "Vol éco"},
    ("airbizness", "flight_business"):  {"fixed": 15.00, "rate": 0.0,  "label": "Vol business"},
    ("airbizness", "hotel"):            {"fixed": 0.0,   "rate": 0.05, "label": "Hôtel 5%"},
    ("airbizness", "package"):          {"fixed": 25.00, "rate": 0.0,  "label": "Package voyage"},

    # onyx — média
    ("onyx", "subscription_new"):       {"fixed": 0.50,  "rate": 0.0,  "label": "Abonné nouveau"},
    ("onyx", "article_premium"):        {"fixed": 0.10,  "rate": 0.0,  "label": "Article premium lu"},

    # lediagnostiqueur — diagnostic immo
    ("lediagnostiqueur", "rdv"):        {"fixed": 50.00, "rate": 0.0,  "label": "RDV pris"},
    ("lediagnostiqueur", "diagnostic"): {"fixed": 80.00, "rate": 0.0,  "label": "Diagnostic réalisé"},

    # lesdemocrates — politics
    ("lesdemocrates", "adhesion"):      {"fixed": 10.00, "rate": 0.0,  "label": "Adhésion militant"},
    ("lesdemocrates", "don"):           {"fixed": 0.0,   "rate": 0.05, "label": "Don 5%"},
}

DEFAULT_COMMISSION = {"fixed": 0.0, "rate": 0.02, "label": "Default 2%"}


def _calc_commission(tenant: str, rec_type: Optional[str], value: Optional[float]) -> tuple[float, str]:
    """Calcule commission. Retourne (montant, label_règle)."""
    rule = COMMISSION_GRID.get((tenant, rec_type or ""), DEFAULT_COMMISSION)
    fixed = float(rule.get("fixed") or 0.0)
    rate = float(rule.get("rate") or 0.0)
    val = float(value or 0.0)
    commission = fixed + (val * rate)
    return round(commission, 2), rule.get("label", "")


# ───────────────────────── Models ─────────────────────────


class RecommendBody(BaseModel):
    tenant: str = Field(..., min_length=1, max_length=80)
    type: str = Field(..., min_length=1, max_length=80)
    agent_slug: Optional[str] = Field(default=None, max_length=80)
    context: Optional[dict] = None
    deal_link: Optional[str] = Field(default=None, max_length=2000)


class ConversionBody(BaseModel):
    value: float = Field(..., ge=0)
    details: Optional[dict] = None
    # Override commission si tenant l'envoie pré-calculée :
    commission_override: Optional[float] = Field(default=None, ge=0)


class MarkPaidBody(BaseModel):
    paid: bool = True


# ───────────────────────── Admin auth ─────────────────────────

ADMIN_TOKEN = os.getenv("MOBILE_ADMIN_TOKEN") or os.getenv("BIZZI_ADMIN_TOKEN", "")


async def require_admin(x_admin_token: str = Header(default="")) -> bool:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="admin_not_configured")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="invalid_admin_token")
    return True


def _row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "agent_slug": r["agent_slug"],
        "recommended_tenant": r["recommended_tenant"],
        "recommendation_type": r["recommendation_type"],
        "context": r.get("context") or {},
        "deal_link": r.get("deal_link"),
        "user_clicked": r["user_clicked"],
        "clicked_at": r["clicked_at"].isoformat() if r.get("clicked_at") else None,
        "conversion": r["conversion"],
        "conversion_at": r["conversion_at"].isoformat() if r.get("conversion_at") else None,
        "conversion_value": float(r["conversion_value"]) if r.get("conversion_value") is not None else None,
        "conversion_details": r.get("conversion_details") or {},
        "commission_due": float(r["commission_due"]) if r.get("commission_due") is not None else 0.0,
        "commission_paid": r["commission_paid"],
        "commission_paid_at": r["commission_paid_at"].isoformat() if r.get("commission_paid_at") else None,
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


# ───────────────────────── User routes ─────────────────────────


@router.post("/recommend")
async def recommend(body: RecommendBody, user=Depends(get_current_user)):
    """L'agent fait une recommandation vers un tenant Bizzi.

    Insert dans mobile_recommendations + estime commission probable (au cas où conversion).
    Retourne {recommendation_id, deal_link, commission_estimated}.
    """
    estimated, label = _calc_commission(body.tenant, body.type, None)

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO mobile_recommendations
              (user_id, agent_slug, recommended_tenant, recommendation_type,
               context, deal_link)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id, user_id, agent_slug, recommended_tenant, recommendation_type,
                      context, deal_link, user_clicked, clicked_at, conversion,
                      conversion_at, conversion_value, conversion_details,
                      commission_due, commission_paid, commission_paid_at, created_at
            """,
            (
                user["id"],
                body.agent_slug,
                body.tenant,
                body.type,
                psycopg2.extras.Json(body.context or {}),
                body.deal_link,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    out = _row_to_dict(row)
    out["commission_estimated"] = estimated
    out["commission_rule_label"] = label
    return out


@router.get("/")
async def list_my_recommendations(
    user=Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=500),
    only_clicked: bool = Query(default=False),
):
    """Historique des recommandations du user courant."""
    sql = (
        "SELECT id, user_id, agent_slug, recommended_tenant, recommendation_type, "
        "context, deal_link, user_clicked, clicked_at, conversion, conversion_at, "
        "conversion_value, conversion_details, commission_due, commission_paid, "
        "commission_paid_at, created_at "
        "FROM mobile_recommendations WHERE user_id=%s "
    )
    params: list = [user["id"]]
    if only_clicked:
        sql += "AND user_clicked = TRUE "
    sql += "ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return {"count": len(rows), "recommendations": [_row_to_dict(r) for r in rows]}


@router.post("/{recommendation_id}/clicked")
async def track_click(recommendation_id: int, user=Depends(get_current_user)):
    """User a cliqué sur le lien — UPDATE clicked_at, idempotent."""
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE mobile_recommendations
            SET user_clicked = TRUE,
                clicked_at = COALESCE(clicked_at, NOW())
            WHERE id=%s AND user_id=%s
            RETURNING id, user_clicked, clicked_at, recommended_tenant, deal_link
            """,
            (recommendation_id, user["id"]),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="recommendation_not_found")
    return {
        "id": row["id"],
        "clicked": row["user_clicked"],
        "clicked_at": row["clicked_at"].isoformat() if row["clicked_at"] else None,
        "tenant": row["recommended_tenant"],
        "deal_link": row["deal_link"],
    }


@router.post("/{recommendation_id}/conversion")
async def track_conversion(
    recommendation_id: int,
    body: ConversionBody,
    authorization: str = Header(default=""),
    x_admin_token: str = Header(default=""),
    x_tenant_token: str = Header(default=""),
):
    """Tenant ou user notifie conversion. Calcule commission_due.

    Auth : 3 chemins acceptés :
    1. JWT mobile user (Authorization Bearer) — l'user lui-même confirme
    2. X-Admin-Token = MOBILE_ADMIN_TOKEN — Pascal admin
    3. X-Tenant-Token = un des TENANT_TOKENS (env BIZZI_TENANT_<TENANT>_TOKEN)

    Idempotent : si conversion déjà enregistrée, retourne l'existante.
    """
    # Auth check (au moins 1 chemin)
    authed = False
    auth_method = None

    if x_admin_token and ADMIN_TOKEN and x_admin_token == ADMIN_TOKEN:
        authed = True
        auth_method = "admin"

    if not authed and x_tenant_token:
        # On accepte un token tenant générique (env)
        # Pour V1 : on accepte n'importe quel token non vide qui matche un env BIZZI_TENANT_*_TOKEN
        for k, v in os.environ.items():
            if k.startswith("BIZZI_TENANT_") and k.endswith("_TOKEN") and v == x_tenant_token:
                authed = True
                auth_method = f"tenant:{k}"
                break

    user_id_from_jwt: Optional[int] = None
    if not authed and authorization and authorization.lower().startswith("bearer "):
        try:
            user = await get_current_user(authorization=authorization)
            user_id_from_jwt = user["id"]
            authed = True
            auth_method = "user"
        except HTTPException:
            pass

    if not authed:
        raise HTTPException(status_code=401, detail="missing_auth_for_conversion")

    # Lecture row pour calcul commission
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, user_id, recommended_tenant, recommendation_type, conversion, "
            "conversion_value, commission_due "
            "FROM mobile_recommendations WHERE id=%s",
            (recommendation_id,),
        )
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="recommendation_not_found")

        # Si user_method, vérifier ownership
        if auth_method == "user" and existing["user_id"] != user_id_from_jwt:
            raise HTTPException(status_code=403, detail="not_your_recommendation")

        if existing["conversion"]:
            # Idempotence
            return {
                "id": existing["id"],
                "already_converted": True,
                "commission_due": float(existing["commission_due"] or 0.0),
                "conversion_value": float(existing["conversion_value"] or 0.0),
            }

        # Calc commission
        if body.commission_override is not None:
            commission = round(float(body.commission_override), 2)
            rule_label = "override"
        else:
            commission, rule_label = _calc_commission(
                existing["recommended_tenant"], existing["recommendation_type"], body.value
            )

        cur.execute(
            """
            UPDATE mobile_recommendations
            SET conversion = TRUE,
                conversion_at = NOW(),
                conversion_value = %s,
                conversion_details = %s::jsonb,
                commission_due = %s
            WHERE id=%s
            RETURNING id, recommended_tenant, recommendation_type, conversion_value,
                      commission_due, commission_paid, conversion_at
            """,
            (
                body.value,
                psycopg2.extras.Json(body.details or {}),
                commission,
                recommendation_id,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    return {
        "id": row["id"],
        "tenant": row["recommended_tenant"],
        "type": row["recommendation_type"],
        "conversion_value": float(row["conversion_value"]),
        "commission_due": float(row["commission_due"]),
        "commission_rule": rule_label,
        "conversion_at": row["conversion_at"].isoformat() if row["conversion_at"] else None,
        "auth_method": auth_method,
    }


# ───────────────────────── Admin routes ─────────────────────────


@router.get("/admin/stats")
async def admin_stats(
    month: str = Query(default="current", description="YYYY-MM ou 'current'"),
    _admin: bool = Depends(require_admin),
):
    """Stats commissions par tenant pour le mois donné.

    Retourne par tenant :
    - reco_count, click_count, conversion_count (funnel)
    - conversion_value_total
    - commission_due_total, commission_paid_total
    """
    if month == "current":
        date_filter = (
            "DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())"
        )
        params: list = []
    else:
        # Validation YYYY-MM
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(400, "month must be 'current' or YYYY-MM")
        date_filter = "TO_CHAR(created_at, 'YYYY-MM') = %s"
        params = [month]

    sql = f"""
        SELECT recommended_tenant,
               COUNT(*)                                         AS reco_count,
               SUM(CASE WHEN user_clicked THEN 1 ELSE 0 END)   AS click_count,
               SUM(CASE WHEN conversion THEN 1 ELSE 0 END)     AS conversion_count,
               COALESCE(SUM(conversion_value), 0)              AS conversion_value_total,
               COALESCE(SUM(commission_due), 0)                AS commission_due_total,
               COALESCE(SUM(CASE WHEN commission_paid THEN commission_due ELSE 0 END), 0) AS commission_paid_total
        FROM mobile_recommendations
        WHERE {date_filter}
        GROUP BY recommended_tenant
        ORDER BY commission_due_total DESC
    """

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    breakdown = []
    total_due = 0.0
    total_paid = 0.0
    for r in rows:
        due = float(r["commission_due_total"] or 0)
        paid = float(r["commission_paid_total"] or 0)
        total_due += due
        total_paid += paid
        breakdown.append({
            "tenant": r["recommended_tenant"],
            "reco_count": int(r["reco_count"]),
            "click_count": int(r["click_count"] or 0),
            "conversion_count": int(r["conversion_count"] or 0),
            "conversion_value_total": float(r["conversion_value_total"] or 0),
            "commission_due_total": due,
            "commission_paid_total": paid,
            "commission_unpaid": round(due - paid, 2),
            "click_rate": round(
                (r["click_count"] or 0) / r["reco_count"] * 100, 1
            ) if r["reco_count"] else 0,
            "conversion_rate": round(
                (r["conversion_count"] or 0) / r["reco_count"] * 100, 1
            ) if r["reco_count"] else 0,
        })

    return {
        "month": month,
        "tenants": breakdown,
        "total": {
            "commission_due": round(total_due, 2),
            "commission_paid": round(total_paid, 2),
            "commission_unpaid": round(total_due - total_paid, 2),
        },
    }


@router.get("/admin/unpaid")
async def admin_unpaid(
    tenant: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=1000),
    _admin: bool = Depends(require_admin),
):
    """Liste les commissions dues NON payées (pour facturation tenant)."""
    sql = (
        "SELECT id, user_id, agent_slug, recommended_tenant, recommendation_type, "
        "context, conversion_value, commission_due, conversion_at, created_at "
        "FROM mobile_recommendations "
        "WHERE conversion = TRUE AND commission_paid = FALSE "
    )
    params: list = []
    if tenant:
        sql += "AND recommended_tenant = %s "
        params.append(tenant)
    sql += "ORDER BY conversion_at ASC LIMIT %s"
    params.append(limit)

    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    total = sum(float(r["commission_due"] or 0) for r in rows)
    return {
        "count": len(rows),
        "total_unpaid": round(total, 2),
        "items": [
            {
                "id": r["id"],
                "tenant": r["recommended_tenant"],
                "type": r["recommendation_type"],
                "user_id": r["user_id"],
                "agent_slug": r["agent_slug"],
                "conversion_value": float(r["conversion_value"] or 0),
                "commission_due": float(r["commission_due"] or 0),
                "converted_at": r["conversion_at"].isoformat() if r["conversion_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.post("/admin/{recommendation_id}/mark_paid")
async def admin_mark_paid(
    recommendation_id: int,
    body: MarkPaidBody,
    _admin: bool = Depends(require_admin),
):
    """Marque une commission comme payée (ou non). Set commission_paid_at = NOW() si paid."""
    with _db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE mobile_recommendations
            SET commission_paid = %s,
                commission_paid_at = CASE WHEN %s THEN NOW() ELSE NULL END
            WHERE id=%s AND conversion = TRUE
            RETURNING id, recommended_tenant, commission_due, commission_paid, commission_paid_at
            """,
            (body.paid, body.paid, recommendation_id),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="recommendation_not_found_or_no_conversion")
    return {
        "id": row["id"],
        "tenant": row["recommended_tenant"],
        "commission_due": float(row["commission_due"]),
        "commission_paid": row["commission_paid"],
        "commission_paid_at": row["commission_paid_at"].isoformat() if row["commission_paid_at"] else None,
    }


@router.get("/admin/grid")
async def admin_grid(_admin: bool = Depends(require_admin)):
    """Retourne la grille commissions actuelle (V1 hardcodée)."""
    return {
        "default": DEFAULT_COMMISSION,
        "rules": [
            {
                "tenant": k[0],
                "type": k[1],
                "fixed": v["fixed"],
                "rate": v["rate"],
                "label": v["label"],
            }
            for k, v in COMMISSION_GRID.items()
        ],
        "note": "V1 hardcodée. V2 prévue : table mobile_commission_rules configurable par YAML tenant.",
    }
