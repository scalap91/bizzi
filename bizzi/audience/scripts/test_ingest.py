"""Test E2E Phase 0 — pipeline ingest sans dépendre du wiring main.py.

Ce script :
  1. Charge directement la fonction _ingest_one (clean → analyze → embed → store)
  2. POSTe 3 messages factices pour le tenant 'lesdemocrates' (id=4)
  3. Vérifie que les rows sont stockées + analyse + bump trends
  4. Affiche un résumé

Lancer :
  cd /opt/bizzi/bizzi && venv/bin/python -m audience.scripts.test_ingest
"""
from __future__ import annotations

import json
import sys

# Ajout du chemin pour exécution directe
sys.path.insert(0, "/opt/bizzi/bizzi")

from audience._db import ensure_schema  # noqa: E402
from audience import tenant_config as tc  # noqa: E402
from audience.storage import count_reports, list_reports, list_trends  # noqa: E402
from audience.tenant_config import resolve_tenant_id  # noqa: E402

# ── Test fixture : config audience inline (le YAML lesdemocrates.yaml
# n'a pas encore la section `audience:` — patch en attente de validation
# Pascal). On monkey-patche tenant_config.get_audience_config pour le test.
_FIXTURE_AUDIENCE_CFG = {
    "enabled": True,
    "monthly_budget_eur": 30,
    "sources": {"chatbot": {"enabled": True}, "facebook": {"enabled": True}, "forms": {"enabled": True}},
    "categories": [
        {"id": "logement",      "label": "Logement"},
        {"id": "securite",      "label": "Sécurité"},
        {"id": "sante",         "label": "Santé"},
        {"id": "transport",     "label": "Transport"},
        {"id": "emploi",        "label": "Emploi"},
        {"id": "ecologie",      "label": "Écologie"},
        {"id": "proprete",      "label": "Propreté"},
        {"id": "pouvoir_achat", "label": "Pouvoir d'achat"},
        {"id": "education",     "label": "Éducation"},
        {"id": "autres",        "label": "Autres"},
    ],
    "category_ids": ["logement", "securite", "sante", "transport", "emploi",
                     "ecologie", "proprete", "pouvoir_achat", "education", "autres"],
    "priority_keywords_boost": {
        5: ["agression", "rats", "hôpital fermé"],
        3: ["plus de médecin", "loyer", "trafic"],
    },
    "alerts": {"threshold_explosion_pct": 30.0, "notify": "pascal@example.fr", "notify_channel": None},
    "content_generation": {
        "enabled": True,
        "auto_propose": {
            "reply_text": True, "facebook_post": True, "improvement_idea": True,
            "synthesis_report": True,
        },
        "require_validation": True,
    },
    "tenant_name": "Les Démocrates",
}

tc.get_audience_config = lambda slug: _FIXTURE_AUDIENCE_CFG  # type: ignore[assignment]
# IMPORTANT : importer routes APRÈS le monkey-patch, mais routes utilise
# tenant_config.get_audience_config par référence à la fonction du module
# au moment de l'appel — donc l'override prend effet (les modules
# routes/storage importent `from .tenant_config import` mais routes._ingest_one
# fait `get_audience_config(slug)` qui résout via le binding du module.
# On vérifie en réimportant explicitement la fonction patchée.
import audience.routes as _routes  # noqa: E402
_routes.get_audience_config = tc.get_audience_config  # type: ignore[attr-defined]
_ingest_one = _routes._ingest_one  # noqa: E402


SAMPLES = [
    {
        "source": "chatbot",
        "raw": "Bonjour, depuis 3 semaines il y a des rats partout dans la rue Pierre Brossolette à Lisses. Ma voisine a été agressée hier près de la boulangerie. Quand est-ce que la mairie va agir ? Je m'appelle Jean Dupont, jean.dupont@example.fr, 06 12 34 56 78.",
        "city": "Lisses",
        "platform": "site_lesdemocrates",
        "author_name": "Jean Dupont",
    },
    {
        "source": "forms",
        "raw": "Le médecin de notre village est parti à la retraite il y a 6 mois. On n'arrive plus à se faire soigner. Plus aucun médecin disponible à 30km. C'est inadmissible.",
        "city": "Bondoufle",
        "platform": "form_contact",
        "author_name": "M. Martin",
    },
    {
        "source": "facebook",
        "raw": "Bravo pour votre dernier communiqué sur le logement, on vous soutient à 100% !",
        "city": None,
        "platform": "fb_page_lesdemocrates",
        "author_name": None,
    },
]


def main() -> int:
    print("== ensure_schema ==")
    print(json.dumps(ensure_schema(), indent=2, default=str))

    tslug = "lesdemocrates"
    tid = resolve_tenant_id(tslug)
    if tid is None:
        print(f"ERROR: tenant slug '{tslug}' inconnu en DB")
        return 1
    print(f"== tenant resolved : {tslug} -> id={tid}")

    before_count = count_reports(tid, since_hours=24)
    print(f"== count avant : {before_count}")

    inserted_ids: list[int] = []
    for i, s in enumerate(SAMPLES, start=1):
        print(f"\n-- sample #{i} ({s['source']}) --")
        row = _ingest_one(
            tid, tslug,
            s["source"], s["raw"],
            platform=s["platform"], author_name=s["author_name"],
            author_external_id=None, city=s["city"],
            metadata={"test": True},
        )
        print(json.dumps({
            "id": row["id"],
            "categories": row["categories"],
            "subcategory": row["subcategory"],
            "emotion": row["emotion"],
            "keywords": row["keywords"],
            "priority_score": row["priority_score"],
            "language": row["language"],
            "redactions": row["metadata"].get("redactions"),
            "analysis_model": row["metadata"].get("analysis_model"),
            "embed_mode": row["metadata"].get("embed_mode"),
        }, indent=2, ensure_ascii=False))
        inserted_ids.append(row["id"])

    after_count = count_reports(tid, since_hours=24)
    delta = after_count - before_count
    print(f"\n== count après : {after_count} (delta={delta}) — attendu : 3 ==")

    print("\n== reports récents (top 3) ==")
    recent = list_reports(tid, limit=3)
    for r in recent:
        print(f"  id={r['id']} src={r['source']} cats={r['categories']} prio={r['priority_score']} city={r['city']}")

    print("\n== trends top 5 ==")
    trends = list_trends(tid, limit=5)
    for t in trends:
        print(f"  cat={t['category']} city={t['city']} 24h={t['total_mentions_24h']} 7d={t['total_mentions_7d']}")

    # Bonus : test rendu HTML embed avec JWT scopé section
    print("\n== embed HTML render (scope section, simulé sans HTTP) ==")
    try:
        from audience.auth import encode_jwt, decode_jwt
        from audience.iframe_embed import _render_embed_html
        from audience.orghierarchy_client import get_visible_units
        from audience.tenant_config import resolve_tenant_slug

        tok = encode_jwt({"tenant_id": tid, "role": "secretaire_section", "org_unit_id": 12})
        claims = decode_jwt(tok)
        vu = get_visible_units(claims)
        slug = resolve_tenant_slug(tid)
        cfg = _FIXTURE_AUDIENCE_CFG  # YAML pas encore en place — utilise fixture
        html = _render_embed_html(
            title="Audience — Section",
            primary_color="#1e40af",
            summary={"mentions_24h": count_reports(tid, 24, visible_units=vu),
                     "mentions_7d": count_reports(tid, 168, visible_units=vu)},
            reports=list_reports(tid, visible_units=vu, limit=10),
            trends=list_trends(tid, limit=5),
            alerts=[],
            categories=cfg["categories"],
            scope_label=f"section #12 ({slug})",
            tenant_slug=slug,
            token=tok,
            api_base="/api/audience",
        )
        print(f"HTML rendered : {len(html)} chars, contains 'b-wrap': {'b-wrap' in html}")
    except Exception as e:
        print(f"embed render FAIL: {e}")
        ok_embed = False
    else:
        ok_embed = True

    ok = delta == 3 and ok_embed
    print(f"\n== RESULT : {'OK' if ok else 'FAIL'} ==")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
