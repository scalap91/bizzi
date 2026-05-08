"""Event bus in-memory pour le live feed WebSocket /api/audience/stream.

Phase 0 = process unique uvicorn (workers=1). Suffisant pour le command
center : tenants isolés, ring buffer 100 derniers events, abonnés
asynchrones. Phase 1 → migrer sur Redis pub-sub si workers>1.

Isolation stricte : un subscriber ne reçoit QUE les events de son tenant.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


_MAX_BUFFER = 100
_MAX_QUEUE = 64

# tenant_id -> deque[event]
_BUFFERS: dict[int, deque[dict[str, Any]]] = {}
# tenant_id -> set[asyncio.Queue]
_SUBS: dict[int, set[asyncio.Queue]] = {}


def publish(tenant_id: int, event: dict[str, Any]) -> None:
    """Diffuse un event aux abonnés du tenant + ajoute au ring buffer."""
    buf = _BUFFERS.setdefault(tenant_id, deque(maxlen=_MAX_BUFFER))
    buf.append(event)

    subs = _SUBS.get(tenant_id) or set()
    dead: list[asyncio.Queue] = []
    for q in subs:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # subscriber lent : on le déconnecte (il pourra rejoindre)
            dead.append(q)
    for q in dead:
        subs.discard(q)


def recent(tenant_id: int, limit: int = 20) -> list[dict[str, Any]]:
    buf = _BUFFERS.get(tenant_id) or deque()
    items = list(buf)
    return items[-limit:]


def subscribe(tenant_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    _SUBS.setdefault(tenant_id, set()).add(q)
    return q


def unsubscribe(tenant_id: int, q: asyncio.Queue) -> None:
    subs = _SUBS.get(tenant_id)
    if subs:
        subs.discard(q)
