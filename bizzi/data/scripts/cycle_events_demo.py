"""scripts/cycle_events_demo.py — Cycle complet Phase 1.

Démontre le bus events bizzi.data avec routing YAML :
  1. Charge les events_routes du tenant (handlers déclarés dans le yaml)
  2. Publie un event audience.alert.raised → handler 'log' + 'store_in_memory'
  3. Publie un event call.completed → idem
  4. Liste les events récents + leur status
  5. Recherche dans memory_vector ce qui a été indexé

Usage :
    BIZZI_DOMAINS_DIR=/tmp/bizzi-data-test \
        /opt/bizzi/bizzi/venv/bin/python -m data.scripts.cycle_events_demo
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

from data import events as data_events  # noqa: E402
from data import memory_vector  # noqa: E402
from data.integrations import audience as audience_int, phone as phone_int  # noqa: E402


TENANT_SLUG = os.environ.get("TENANT", "lesdemocrates")
TENANT_ID   = int(os.environ.get("TENANT_ID", "4"))


def main() -> int:
    print(f"== Cycle events demo — tenant={TENANT_SLUG} (id={TENANT_ID}) ==\n")

    print("→ 1) Charger les events_routes du YAML…")
    n = data_events.configure_from_yaml(TENANT_SLUG, TENANT_ID)
    print(f"   {n} handlers enregistrés depuis events_routes\n")

    print("→ 2) Publish audience.alert.raised (via integrations.audience)…")
    fake_alert = {
        "id":           42,
        "alert_type":   "trend_spike",
        "category":     "social",
        "city":         "Paris",
        "metric_value": 87.5,
        "threshold":    50.0,
        "title":        "Pic d'opinions sur la réforme retraites",
        "description":  "+150% en 24h vs moyenne 7j",
    }
    ev = audience_int.publish_audience_alert(TENANT_ID, fake_alert)
    print(f"   event #{ev['id']} status={ev['status']} "
          f"handlers={[r.get('handler') for r in ev.get('handler_results') or []]}")

    print("\n→ 3) Publish call.completed (via integrations.phone)…")
    ev2 = phone_int.publish_call_completed(
        TENANT_ID, call_id=999, contact_phone="+33612345678",
        duration_sec=312, outcome="member_signup", use_case="member_recruitment",
    )
    print(f"   event #{ev2['id']} status={ev2['status']}")

    print("\n→ 4) list_events tenant — derniers 5…")
    recent = data_events.list_events(TENANT_ID, limit=5)
    for e in recent:
        print(f"   #{e['id']} kind={e['kind']:30s} status={e['status']:10s} "
              f"{e['published_at']}")

    print("\n→ 5) list_kinds tenant — types vus…")
    kinds = data_events.list_kinds(TENANT_ID)
    for k in kinds:
        print(f"   {k['kind']:35s}  count={k['n']:4d}  last={k['last_seen']}")

    print("\n→ 6) memory_search 'pic' (devrait remonter l'audience.alert)…")
    hits = memory_vector.memory_search(TENANT_ID, "pic opinions retraites", k=3)
    for h in hits:
        print(f"   memory#{h['id']} kind={h.get('kind')} score={h.get('score'):.3f} "
              f"{(h.get('text') or '')[:80]}")

    print("\n✓ Cycle complet terminé")
    return 0


if __name__ == "__main__":
    sys.exit(main())
