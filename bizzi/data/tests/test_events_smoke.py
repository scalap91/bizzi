"""Phase 1 smoke tests — events bus + integrations.

Lancer :
    BIZZI_DOMAINS_DIR=/tmp/bizzi-data-test \
        /opt/bizzi/bizzi/venv/bin/python -m data.tests.test_events_smoke
"""
from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

from data import events as data_events  # noqa: E402
from data import memory_vector  # noqa: E402
from data.integrations import phone as phone_int  # noqa: E402
from data.integrations import social as social_int  # noqa: E402


_PASS = 0
_FAIL = 0
_FAILS: list[str] = []


def _assert(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1; print(f"  ✓ {label}")
    else:
        _FAIL += 1; _FAILS.append(label); print(f"  ✗ {label}")


def _run(name, fn):
    print(f"\n— {name} —")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        global _FAIL
        _FAIL += 1; _FAILS.append(f"{name}: {e}")
        print(f"  ✗ EXCEPTION: {e}")
        traceback.print_exc()


# Test tenant_id = 99999 (artificiel pour ne pas polluer prod tenants).
TENANT_ID = 99999


def test_events_publish_no_handler():
    """Un event sans handler doit rester en status='pending'."""
    data_events.unsubscribe_all()
    ev = data_events.publish(TENANT_ID, "test.no_handler", {"x": 1},
                             source_module="smoke")
    _assert(ev["status"] == "pending", "no handler → status=pending")
    _assert(ev["kind"] == "test.no_handler", "kind correct")
    _assert(ev["payload"]["x"] == 1, "payload conservé")


def test_events_subscribe_and_dispatch():
    data_events.unsubscribe_all()
    received = []

    def h_ok(ev):
        received.append(ev["id"])
        return {"ok": True, "echo": ev["payload"]}

    data_events.subscribe(h_ok, tenant_id=TENANT_ID, kind="test.dispatch")
    ev = data_events.publish(TENANT_ID, "test.dispatch", {"k": "v"})
    _assert(ev["status"] == "processed", "handler ok → status=processed")
    _assert(len(received) == 1, "handler invoqué 1x")
    _assert(received[0] == ev["id"], "handler reçoit l'event courant")


def test_events_handler_failure():
    data_events.unsubscribe_all()

    def h_ok(_ev): return "yay"

    def h_ko(_ev):
        raise RuntimeError("boom")

    data_events.subscribe(h_ok, tenant_id=TENANT_ID, kind="test.partial")
    data_events.subscribe(h_ko, tenant_id=TENANT_ID, kind="test.partial")
    ev = data_events.publish(TENANT_ID, "test.partial", {})
    _assert(ev["status"] == "partial", "1 ok + 1 ko → status=partial")
    _assert("boom" in (ev.get("error") or ""), "error message capturé")


def test_events_yaml_routes():
    """Vérifie que configure_from_yaml enregistre bien les handlers."""
    data_events.unsubscribe_all()
    n = data_events.configure_from_yaml("lesdemocrates", TENANT_ID)
    _assert(n >= 1, f"configure_from_yaml charge ≥1 handler (got {n})")

    ev = data_events.publish(
        TENANT_ID, "audience.alert.raised",
        {"title": "Sujet test", "description": "smoke"},
    )
    _assert(ev["status"] in ("processed", "partial"),
            f"status final ∈ processed/partial (got {ev['status']})")

    # store_in_memory doit avoir produit un memory_id
    handlers = ev.get("handler_results") or []
    has_mem = any("memory_id" in (h.get("result") or {}) for h in handlers if h.get("ok"))
    _assert(has_mem, "store_in_memory handler a produit un memory_id")


def test_phone_integration():
    data_events.unsubscribe_all()
    ev = phone_int.publish_call_completed(
        TENANT_ID, call_id=12345, duration_sec=120, outcome="member_signup",
    )
    _assert(ev["kind"] == "call.completed", "phone publishes call.completed")
    _assert(ev["payload"]["call_id"] == 12345, "payload contient call_id")

    mid = phone_int.index_call_transcript(
        TENANT_ID, call_id=12345, transcript="Bonjour, je suis intéressé...",
        summary="Prospect qualifié",
    )
    _assert(isinstance(mid, int) and mid > 0, "index_call_transcript renvoie int>0")


def test_social_integration():
    data_events.unsubscribe_all()
    ev = social_int.publish_post_event(
        TENANT_ID, "published", post_id=777, networks=["tiktok"],
        caption="Test caption", template_id="article_clip",
    )
    _assert(ev["kind"] == "social.post.published",
            "social publishes social.post.published")
    _assert(ev["payload"]["post_id"] == 777, "payload post_id")

    mid = social_int.index_post_published(
        TENANT_ID, post_id=777, networks=["tiktok"],
        caption="Some caption text", template_id="article_clip",
    )
    _assert(mid > 0, "index_post_published OK")


def test_events_list():
    rows = data_events.list_events(TENANT_ID, limit=10)
    _assert(isinstance(rows, list), "list_events list")
    _assert(len(rows) >= 1, "des events existent pour le tenant test")

    kinds = data_events.list_kinds(TENANT_ID)
    _assert(any(k["kind"] == "call.completed" for k in kinds),
            "call.completed apparaît dans list_kinds")


# Cleanup à la fin pour ne pas polluer la DB
def cleanup():
    from data._db import get_conn
    with get_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM data_events WHERE tenant_id = %s", (TENANT_ID,))
        cur.execute("DROP TABLE IF EXISTS memory_99999")
        c.commit()


def main() -> int:
    print("== Smoke tests bizzi.data Phase 1 ==")
    _run("events_publish_no_handler",   test_events_publish_no_handler)
    _run("events_subscribe_and_dispatch", test_events_subscribe_and_dispatch)
    _run("events_handler_failure",      test_events_handler_failure)
    _run("events_yaml_routes",          test_events_yaml_routes)
    _run("phone_integration",           test_phone_integration)
    _run("social_integration",          test_social_integration)
    _run("events_list",                 test_events_list)

    try:
        cleanup()
    except Exception as e:  # noqa: BLE001
        print(f"⚠ cleanup failed: {e}")

    print(f"\n== Résultat : {_PASS} OK / {_FAIL} KO ==")
    if _FAILS:
        print("Échecs :")
        for f in _FAILS:
            print(f"  - {f}")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
