"""bizzi.data.events — Bus d'évènements persistant cross-module.

Diffère de `bizzi.audience.event_bus` (qui est in-memory, ring buffer 100,
limité au feed WebSocket du command center). Ici on persiste en DB pour
permettre :

  - une trace permanente des évènements métier (audit, replay)
  - du routing déclaratif tenant via YAML (`events_routes:`)
  - une fan-out vers handlers Python enregistrés (publish/subscribe)
  - un bridging avec `audience.event_bus` (publish in-memory ET persiste)

Phase 1 : pas de Redis pub-sub multi-process. Single uvicorn worker
suffit. Phase 2 : LISTEN/NOTIFY Postgres ou Redis si workers > 1.

Schéma `data_events` (idempotent au boot via ensure_schema()) :
    id, tenant_id, kind, payload jsonb, source_module, correlation_id,
    published_at, processed_at, status (pending|processed|failed),
    handler_results jsonb, error
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from collections import defaultdict
from typing import Any, Callable, Optional

from psycopg2.extras import Json, RealDictCursor

from ._db import get_conn
from .semantic import load_data_config


logger = logging.getLogger("bizzi.data.events")


# ── Schema (idempotent) ──────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS data_events (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL,
    kind            TEXT NOT NULL,
    payload         JSONB DEFAULT '{}'::jsonb,
    source_module   TEXT,
    correlation_id  TEXT,
    status          TEXT DEFAULT 'pending',
    handler_results JSONB DEFAULT '[]'::jsonb,
    error           TEXT,
    published_at    TIMESTAMPTZ DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    CONSTRAINT data_events_status_chk
        CHECK (status IN ('pending','processed','failed','partial'))
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_data_events_tenant_kind "
    "ON data_events(tenant_id, kind, published_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_data_events_status "
    "ON data_events(status, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_data_events_correlation "
    "ON data_events(correlation_id) WHERE correlation_id IS NOT NULL",
]


_schema_ensured = False
_schema_lock = threading.Lock()


def ensure_schema() -> None:
    """Crée la table data_events si absente. Idempotent + thread-safe."""
    global _schema_ensured
    if _schema_ensured:
        return
    with _schema_lock:
        if _schema_ensured:
            return
        with get_conn() as c, c.cursor() as cur:
            cur.execute(_DDL)
            for ddl in _DDL_INDEXES:
                cur.execute(ddl)
            c.commit()
        _schema_ensured = True


# ── Handler registry (in-process) ────────────────────────────────
# Multi-tenant : (tenant_id|None, kind|None) -> [handlers]
# - tenant_id=None : handler global (tous tenants)
# - kind=None      : handler wildcard (tous kinds)
HandlerFn = Callable[[dict[str, Any]], Any]
_HANDLERS: dict[tuple[Optional[int], Optional[str]], list[HandlerFn]] = defaultdict(list)


def subscribe(
    handler: HandlerFn,
    *,
    tenant_id: Optional[int] = None,
    kind: Optional[str] = None,
) -> None:
    """Enregistre un handler synchrone.

    Le handler reçoit l'event dict complet et retourne une valeur
    JSON-sérialisable (loggée dans handler_results). Toute exception
    est capturée — l'event passe en status='partial' ou 'failed' selon
    le nombre de handlers réussis.
    """
    _HANDLERS[(tenant_id, kind)].append(handler)


def unsubscribe_all(tenant_id: Optional[int] = None, kind: Optional[str] = None) -> int:
    """Vide les handlers pour la combinaison donnée. Retourne le nb supprimé."""
    key = (tenant_id, kind)
    n = len(_HANDLERS.get(key, []))
    _HANDLERS[key] = []
    return n


def list_handlers() -> list[dict[str, Any]]:
    return [
        {"tenant_id": tid, "kind": k, "count": len(v)}
        for (tid, k), v in _HANDLERS.items() if v
    ]


def _matching_handlers(tenant_id: int, kind: str) -> list[HandlerFn]:
    out: list[HandlerFn] = []
    # Ordre : précis → vague (tenant+kind, tenant+*, *+kind, *+*)
    for key in [
        (tenant_id, kind),
        (tenant_id, None),
        (None, kind),
        (None, None),
    ]:
        out.extend(_HANDLERS.get(key, []))
    return out


# ── Publish / Process ────────────────────────────────────────────
def publish(
    tenant_id: int,
    kind: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    source_module: Optional[str] = None,
    correlation_id: Optional[str] = None,
    process_now: bool = True,
) -> dict[str, Any]:
    """Persiste l'event puis (par défaut) exécute les handlers in-process.

    Retourne le dict event final (avec id, status, handler_results).

    process_now=False : insère en status='pending', à traiter par un job
    externe (utile si on veut un découplage strict).
    """
    ensure_schema()
    payload = payload or {}

    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO data_events
               (tenant_id, kind, payload, source_module, correlation_id, status)
               VALUES (%s, %s, %s, %s, %s, 'pending')
               RETURNING id, tenant_id, kind, payload, source_module,
                         correlation_id, status, published_at""",
            (tenant_id, kind, Json(payload), source_module, correlation_id),
        )
        ev = dict(cur.fetchone())
        c.commit()

    # Bridge in-memory audience event_bus si dispo (best-effort).
    try:
        from ..audience import event_bus as _audience_bus
        _audience_bus.publish(tenant_id, {
            "type": f"data.{kind}",
            "data": payload,
            "event_id": ev["id"],
        })
    except Exception:  # noqa: BLE001
        pass  # audience module pas installé ou erreur non bloquante

    if process_now:
        ev = process_event(ev["id"])
    return _serialize(ev)


def process_event(event_id: int) -> dict[str, Any]:
    """Charge un event pending et exécute ses handlers. Idempotent : un
    event déjà processed reste tel quel."""
    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM data_events WHERE id = %s",
            (event_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"event_id {event_id} introuvable")
        ev = dict(row)

    if ev["status"] != "pending":
        return _serialize(ev)

    handlers = _matching_handlers(ev["tenant_id"], ev["kind"])
    results: list[dict[str, Any]] = []
    n_ok = 0
    n_ko = 0
    for h in handlers:
        try:
            res = h(ev)
            results.append({
                "handler": getattr(h, "__name__", repr(h)),
                "ok": True,
                "result": _safe_json(res),
            })
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            results.append({
                "handler": getattr(h, "__name__", repr(h)),
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc()[-400:],
            })
            n_ko += 1
            logger.exception("handler %s failed for event %s", h, ev["id"])

    if n_ko == 0 and n_ok == 0:
        # Aucun handler enregistré : status reste 'pending' avec note.
        new_status = "pending"
    elif n_ko == 0:
        new_status = "processed"
    elif n_ok == 0:
        new_status = "failed"
    else:
        new_status = "partial"

    error_summary = (
        "; ".join(r["error"] for r in results if not r["ok"])[:1000]
        if n_ko else None
    )

    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """UPDATE data_events
               SET status = %s,
                   handler_results = %s,
                   error = %s,
                   processed_at = CASE WHEN %s = 'pending' THEN NULL ELSE now() END
               WHERE id = %s
               RETURNING *""",
            (new_status, Json(results), error_summary, new_status, event_id),
        )
        ev = dict(cur.fetchone())
        c.commit()
    return _serialize(ev)


def replay_pending(tenant_id: Optional[int] = None, limit: int = 100) -> list[dict[str, Any]]:
    """Re-traite les events pending (utile après ajout de nouveaux handlers
    ou redémarrage)."""
    where = ["status = 'pending'"]
    params: list[Any] = []
    if tenant_id is not None:
        where.append("tenant_id = %s"); params.append(tenant_id)
    sql = (
        f"SELECT id FROM data_events "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY published_at LIMIT %s"
    )
    params.append(int(limit))
    with get_conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        ids = [r[0] for r in cur.fetchall()]
    out = []
    for eid in ids:
        try:
            out.append(process_event(eid))
        except Exception as e:  # noqa: BLE001
            logger.exception("replay event %s failed: %s", eid, e)
    return out


# ── Lecture ──────────────────────────────────────────────────────
def list_events(
    tenant_id: int,
    *,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if kind:
        where.append("kind = %s"); params.append(kind)
    if status:
        where.append("status = %s"); params.append(status)
    sql = (
        "SELECT * FROM data_events WHERE " + " AND ".join(where)
        + " ORDER BY published_at DESC LIMIT %s OFFSET %s"
    )
    params.extend([int(limit), int(offset)])
    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [_serialize(dict(r)) for r in cur.fetchall()]


def list_kinds(tenant_id: int) -> list[dict[str, Any]]:
    """Liste les types d'events vus pour un tenant + leur count."""
    with get_conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT kind, count(*) AS n,
                      max(published_at) AS last_seen
               FROM data_events
               WHERE tenant_id = %s
               GROUP BY kind ORDER BY n DESC""",
            (tenant_id,),
        )
        return [_serialize(dict(r)) for r in cur.fetchall()]


# ── YAML routes ──────────────────────────────────────────────────
def configure_from_yaml(tenant_slug: str, tenant_id: int) -> int:
    """Lit la section `events_routes` du YAML tenant et enregistre les
    handlers correspondants. Retourne le nb de handlers enregistrés.

    Format YAML attendu :

        events_routes:
          - event: payment.confirmed
            then: { execute_view: dossier_facturable, store_in_memory: true }
          - event: article.published
            then: { log: true, social_clip: tiktok }

    Note : on n'utilise PAS la clé `on:` car pyyaml/YAML-1.1 parse
    `on`/`off`/`yes`/`no` comme booléens. Utiliser `event:`.

    Phase 1 : on supporte uniquement deux primitives :
      - log:                écrit un log structuré
      - store_in_memory:    pousse l'event dans memory_vector
    Les autres (audience_alert, social_clip…) sont laissés à des modules
    consommateurs qui peuvent appeler subscribe() de leur côté.
    """
    # On (re)lit le YAML directement plutôt que d'étendre SemanticSchema —
    # events_routes n'a pas vocation à être interrogé en boucle (chargé 1x au boot).
    import yaml as _yaml
    from .semantic import _yaml_path_for_tenant
    # load_data_config valide aussi que le YAML est cohérent (data_sources etc.).
    load_data_config(tenant_slug)
    with open(_yaml_path_for_tenant(tenant_slug), encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}
    raw = data.get("events_routes") or []

    n = 0
    for route in raw:
        # Compat : `event:` (préféré) ou `on:` (devient bool True via YAML 1.1)
        kind = route.get("event") or route.get("on") or route.get(True)
        actions = route.get("then") or {}
        if not kind or not isinstance(kind, str):
            continue
        for action_name, action_value in actions.items():
            handler = _build_action_handler(action_name, action_value, tenant_id)
            if handler:
                subscribe(handler, tenant_id=tenant_id, kind=kind)
                n += 1
    return n


def _build_action_handler(
    name: str,
    value: Any,
    tenant_id: int,
) -> Optional[HandlerFn]:
    """Fabrique un handler à partir d'une action YAML."""
    if name == "log":
        def _h_log(ev: dict[str, Any]) -> dict[str, Any]:
            logger.info("event:%s tenant=%s payload=%s",
                        ev.get("kind"), ev.get("tenant_id"),
                        json.dumps(ev.get("payload"), default=str)[:200])
            return {"action": "log", "ok": True}
        return _h_log

    if name == "store_in_memory":
        from .memory_vector import memory_store

        def _h_store(ev: dict[str, Any]) -> dict[str, Any]:
            payload = ev.get("payload") or {}
            text = (
                payload.get("text")
                or payload.get("title")
                or payload.get("description")
                or json.dumps(payload, default=str, ensure_ascii=False)
            )[:4000]
            mid = memory_store(
                tenant_id=ev["tenant_id"],
                text=text,
                kind=f"event:{ev['kind']}",
                source_ref=f"data_event:{ev['id']}",
                metadata={"event_id": ev["id"], "kind": ev["kind"]},
            )
            return {"action": "store_in_memory", "memory_id": mid}
        return _h_store

    # Primitive inconnue : on log un warning et on enregistre un no-op
    # qui marque l'action en "ignored" pour la traçabilité.
    def _h_unknown(_ev: dict[str, Any]) -> dict[str, Any]:
        return {"action": name, "ok": False, "ignored": True,
                "reason": f"primitive '{name}' non implémentée Phase 1"}
    return _h_unknown


# ── Helpers ──────────────────────────────────────────────────────
def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in ("published_at", "processed_at", "last_seen"):
        v = out.get(k)
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


def _safe_json(v: Any) -> Any:
    try:
        json.dumps(v, default=str)
        return v
    except Exception:  # noqa: BLE001
        return str(v)
