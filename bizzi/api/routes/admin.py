"""api/routes/admin.py — Endpoints admin Bizzi"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import datetime, timedelta
from database.models import Tenant, Agent, Production, PipelineRun
from database.models import ProductionStatus, PipelineStatus, AgentStatus

router = APIRouter()

# En prod → injecter la session DB via Depends
# Ici on utilise un mock pour la démo
def get_db():
    pass


# ── STATS GLOBALES ────────────────────────────────────────────

@router.get("/stats/global")
async def global_stats():
    """
    Stats globales pour la vue d'ensemble admin.
    Retourne : clients actifs, MRR, productions, poubelle, alertes, uptime.
    """
    # En prod : requêtes SQL réelles
    # SELECT COUNT(*) FROM tenants WHERE is_active = true
    # SELECT SUM(mrr) FROM tenants WHERE is_active = true
    # SELECT COUNT(*) FROM productions WHERE created_at >= date_trunc('month', now())
    # SELECT COUNT(*) FROM productions WHERE status = 'trashed' AND ...

    return {
        "tenants_active":      9,
        "tenants_new_month":   2,
        "mrr":                 5218,
        "mrr_growth":          398,
        "productions_month":   1847,
        "productions_growth":  340,
        "trashed_month":       124,
        "trashed_pct":         6.7,
        "alerts_active":       2,
        "uptime_pct":          99.8,
        "agents_active":       56,
        "pipeline_running":    1,
    }


# ── STATS PAR TENANT ─────────────────────────────────────────

@router.get("/stats/tenants")
async def all_tenants_stats():
    """
    Stats de tous les clients pour le tableau de l'admin.
    Retourne uniquement des métriques — pas de contenu.
    """
    # En prod :
    # SELECT t.*, 
    #   COUNT(p.id) FILTER (WHERE p.status = 'published') as prod_published,
    #   COUNT(p.id) FILTER (WHERE p.status = 'trashed') as prod_trashed,
    #   AVG(p.qa_score) as avg_score,
    #   COUNT(a.id) FILTER (WHERE a.status = 'active') as agents_active
    # FROM tenants t
    # LEFT JOIN productions p ON p.tenant_id = t.id AND p.created_at >= date_trunc('month', now())
    # LEFT JOIN agents a ON a.tenant_id = t.id
    # GROUP BY t.id

    return {
        "tenants": [
            {
                "slug":           "les-democrates",
                "name":           "Les Démocrates",
                "domain":         "politics",
                "plan":           "pro",
                "color":          "#D6140D",
                "site":           "lesdemocrates.org",
                "productions":    284,
                "trashed":        18,
                "trashed_pct":    6.3,
                "agents_active":  6,
                "agents_total":   6,
                "avg_score":      87.0,
                "status":         "ok",
                "mrr":            299,
                "pipeline_status":"scheduled",
                "last_run":       "Il y a 2h",
            },
            {
                "slug":           "onyx-news",
                "name":           "Onyx-news",
                "domain":         "media",
                "plan":           "pro",
                "color":          "#00c896",
                "site":           "onyx-news.fr",
                "productions":    512,
                "trashed":        31,
                "trashed_pct":    6.1,
                "agents_active":  10,
                "agents_total":   10,
                "avg_score":      81.0,
                "status":         "ok",
                "mrr":            299,
                "pipeline_status":"completed",
                "last_run":       "Il y a 2min",
            },
            {
                "slug":           "groupe-helios",
                "name":           "Groupe Helios",
                "domain":         "diagnostic",
                "plan":           "business",
                "color":          "#f59e0b",
                "site":           "groupe-helios.fr",
                "productions":    198,
                "trashed":        9,
                "trashed_pct":    4.5,
                "agents_active":  12,
                "agents_total":   12,
                "avg_score":      90.0,
                "status":         "ok",
                "mrr":            699,
                "pipeline_status":"scheduled",
                "last_run":       "Hier",
            },
            {
                "slug":           "le-reseau-local",
                "name":           "Le Réseau Local",
                "domain":         "media",
                "plan":           "enterprise",
                "color":          "#00c896",
                "site":           "reseaulocal.info",
                "productions":    892,
                "trashed":        54,
                "trashed_pct":    6.1,
                "agents_active":  15,
                "agents_total":   15,
                "avg_score":      79.0,
                "status":         "ok",
                "mrr":            2926,
                "pipeline_status":"running",
                "last_run":       "En cours",
            },
            {
                "slug":           "cabinet-mercier",
                "name":           "Cabinet Mercier",
                "domain":         "diagnostic",
                "plan":           "starter",
                "color":          "#3b82f6",
                "site":           "cabinet-mercier.fr",
                "productions":    127,
                "trashed":        4,
                "trashed_pct":    3.1,
                "agents_active":  3,
                "agents_total":   3,
                "avg_score":      92.0,
                "status":         "ok",
                "mrr":            99,
                "pipeline_status":"completed",
                "last_run":       "Il y a 1h",
            },
            {
                "slug":           "mairie-valbonne",
                "name":           "Mairie de Valbonne",
                "domain":         "custom",
                "plan":           "pro",
                "color":          "#4a5070",
                "site":           "valbonne.fr",
                "productions":    43,
                "trashed":        5,
                "trashed_pct":    11.6,
                "agents_active":  5,
                "agents_total":   7,
                "avg_score":      74.0,
                "status":         "warn",
                "mrr":            299,
                "pipeline_status":"error",
                "last_run":       "Erreur mail",
            },
            {
                "slug":           "cabinet-fontaine",
                "name":           "Cabinet Fontaine",
                "domain":         "diagnostic",
                "plan":           "starter",
                "color":          "#e02d2d",
                "site":           "fontaine-expert.fr",
                "productions":    31,
                "trashed":        8,
                "trashed_pct":    25.8,
                "agents_active":  2,
                "agents_total":   2,
                "avg_score":      68.0,
                "status":         "warn",
                "mrr":            99,
                "pipeline_status":"slow",
                "last_run":       "Ollama lent 45s",
            },
        ]
    }


# ── STATS D'UN TENANT ─────────────────────────────────────────

@router.get("/stats/tenant/{slug}")
async def tenant_stats(slug: str):
    """
    Stats détaillées d'un client.
    Métriques uniquement — pas de contenu (chiffré côté client).
    """
    # En prod : requête SQL sur le tenant + ses agents + ses productions

    return {
        "slug":          slug,
        "productions": {
            "total":     284,
            "published": 248,
            "pending":   18,
            "trashed":   18,
            "trashed_pct": 6.3,
        },
        "quality": {
            "avg_score":   87.0,
            "score_trend": "+4 pts ce mois",
            "qa_passed":   266,
            "qa_rejected": 18,
            "rejection_rate": 6.3,
        },
        "agents": [
            {"name":"Pascal RÉPIR",  "role":"Président",        "status":"active", "productions":0,   "score":None, "color":"#6a0572", "initials":"P"},
            {"name":"Karim BOUCHRA", "role":"Porte-parole",     "status":"active", "productions":0,   "score":87.0, "color":"#374151", "initials":"K"},
            {"name":"Lucas MARTIN",  "role":"Analyste national","status":"active", "productions":142, "score":88.0, "color":"#023e8a", "initials":"L"},
            {"name":"Alice ROY",     "role":"Analyste local",   "status":"active", "productions":98,  "score":85.0, "color":"#2d6a4f", "initials":"A"},
            {"name":"Sico DIA",      "role":"Community Manager","status":"active", "productions":44,  "score":91.0, "color":"#e91e8c", "initials":"S"},
            {"name":"Émile LEFÈVRE", "role":"Juriste",          "status":"idle",   "productions":0,   "score":None, "color":"#4a5070", "initials":"E"},
        ],
        "pipeline": {
            "status":      "scheduled",
            "last_run":    "Il y a 2h",
            "next_run":    "Dans 1h 23min",
            "avg_duration_sec": 187,
            "runs_month":  240,
        },
        "activity": [
            {"type":"pipeline",   "label":"Pipeline terminé · 8 productions · score moy. 87",   "time":"Il y a 2h"},
            {"type":"production", "label":"18 productions en attente de validation",              "time":"Il y a 2h"},
            {"type":"trash",      "label":"3 productions mises à la poubelle · score < 70",      "time":"Il y a 4h"},
            {"type":"pipeline",   "label":"Pipeline terminé · 6 productions · score moy. 84",   "time":"Hier"},
        ]
    }


# ── STATS AGENTS D'UN TENANT ─────────────────────────────────

@router.get("/stats/tenant/{slug}/agents")
async def tenant_agents(slug: str):
    """Statut de chaque agent d'un client."""
    # En prod : SELECT * FROM agents WHERE tenant_id = ...
    return {
        "slug":   slug,
        "agents": [
            {"name":"Pascal RÉPIR",  "role":"Président",        "status":"active", "last_active":"Il y a 5min",  "color":"#6a0572", "initials":"P"},
            {"name":"Karim BOUCHRA", "role":"Porte-parole",     "status":"active", "last_active":"Il y a 2h",    "color":"#374151", "initials":"K"},
            {"name":"Lucas MARTIN",  "role":"Analyste",         "status":"active", "last_active":"Il y a 2h",    "color":"#023e8a", "initials":"L"},
            {"name":"Alice ROY",     "role":"Analyste local",   "status":"active", "last_active":"Il y a 3h",    "color":"#2d6a4f", "initials":"A"},
            {"name":"Sico DIA",      "role":"Community Manager","status":"active", "last_active":"Il y a 1h",    "color":"#e91e8c", "initials":"S"},
            {"name":"Émile LEFÈVRE", "role":"Juriste",          "status":"idle",   "last_active":"Il y a 3 jours","color":"#4a5070","initials":"E"},
        ]
    }


# ── ALERTES ───────────────────────────────────────────────────

@router.get("/alerts")
async def get_alerts():
    """Liste des alertes actives sur tous les tenants."""
    return {
        "alerts": [
            {
                "id":       1,
                "tenant":   "Mairie de Valbonne",
                "slug":     "mairie-valbonne",
                "type":     "mail_config",
                "severity": "warning",
                "message":  "Aucune boîte IMAP configurée · distribution bloquée",
                "since":    "Il y a 2 jours",
            },
            {
                "id":       2,
                "tenant":   "Cabinet Fontaine",
                "slug":     "cabinet-fontaine",
                "type":     "ollama_slow",
                "severity": "warning",
                "message":  "Latence Ollama 45s · normale < 8s",
                "since":    "Il y a 3h",
            },
        ]
    }


# ── PIPELINES ─────────────────────────────────────────────────

@router.get("/pipelines")
async def get_pipelines():
    """État des pipelines de tous les tenants."""
    return {
        "pipelines": [
            {"tenant":"Le Réseau Local",   "status":"running",   "step":"7/10 · validation", "duration":"3min 14s"},
            {"tenant":"Les Démocrates",    "status":"scheduled", "step":"Dans 1h 23min",      "duration":"—"},
            {"tenant":"Onyx-news",         "status":"completed", "step":"8 productions · 81", "duration":"4min 38s"},
            {"tenant":"Groupe Helios",     "status":"scheduled", "step":"Pipeline 8h00",      "duration":"—"},
            {"tenant":"Cabinet Mercier",   "status":"completed", "step":"3 rapports envoyés", "duration":"2min 11s"},
            {"tenant":"Mairie de Valbonne","status":"error",     "step":"IMAP non configuré", "duration":"—"},
            {"tenant":"Cabinet Fontaine",  "status":"slow",      "step":"Ollama 45s",         "duration":"+45s"},
        ]
    }
