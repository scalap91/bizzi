"""bizzi.data.integrations.phone — Helpers data pour module bizzi.phone.

Cas d'usage :
  1. Avant un appel sortant, l'agent veut le CONTEXTE complet du contact :
     - Tout ce que le tenant sait sur lui (data_views, ex: dossier ERP)
     - Tout ce que Bizzi a déjà observé (memory_vector + agent_memories)

  2. Après un appel, l'agent veut INDEXER le transcript pour future RAG.

  3. À la fin d'un appel, l'agent veut PUBLIER un event 'call.completed'
     qui peut déclencher des actions (alerte audience, post social, …).

L'API ici est intentionnellement à fonction unique par cas d'usage —
le module phone (autre sub-Claude) peut l'importer ou pas.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .. import events, memory_vector, views


logger = logging.getLogger("bizzi.data.integrations.phone")


def context_for_contact(
    tenant_slug: str,
    tenant_id: int,
    contact_phone: str,
    *,
    k_memories: int = 5,
    extra_views: Optional[list[tuple[str, dict[str, Any]]]] = None,
) -> dict[str, Any]:
    """Compile un contexte complet pour un appel.

    Retourne :
      {
        "memories": [...]  # top-k memory_vector hits sur le numéro
        "views":    {view_name: rows}
        "errors":   [...]  # vues/recherches qui ont échoué (best-effort)
      }

    `extra_views` permet au caller de spécifier des vues à exécuter avec leurs
    paramètres, ex: [("dossier_par_telephone", {"phone": contact_phone})].
    """
    out: dict[str, Any] = {"memories": [], "views": {}, "errors": []}

    try:
        out["memories"] = memory_vector.memory_search(
            tenant_id=tenant_id,
            query=contact_phone,
            k=k_memories,
        )
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"memory_search: {e}")

    for view_name, params in (extra_views or []):
        try:
            out["views"][view_name] = views.execute_view(tenant_slug, view_name, params)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"view {view_name}: {e}")

    return out


def index_call_transcript(
    tenant_id: int,
    call_id: int,
    transcript: str,
    *,
    agent_id: Optional[int] = None,
    contact_phone: Optional[str] = None,
    summary: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Pousse le transcript en memory_vector kind='call_transcript'.

    Le summary (si fourni) est concaténé en tête pour booster la recherche.
    Retourne l'id memory inséré.
    """
    text = transcript
    if summary:
        text = f"[résumé] {summary}\n\n[transcript] {transcript}"
    md = dict(metadata or {})
    md.update({"call_id": call_id})
    if contact_phone:
        md["contact_phone"] = contact_phone
    return memory_vector.memory_store(
        tenant_id=tenant_id,
        text=text[:8000],
        agent_id=agent_id,
        kind="call_transcript",
        source_ref=f"phone_call:{call_id}",
        metadata=md,
    )


def publish_call_completed(
    tenant_id: int,
    call_id: int,
    *,
    contact_phone: Optional[str] = None,
    duration_sec: Optional[int] = None,
    outcome: Optional[str] = None,
    use_case: Optional[str] = None,
    agent_id: Optional[int] = None,
    cost_eur: Optional[float] = None,
) -> dict[str, Any]:
    """Publie l'event 'call.completed' dans le bus data.

    Les handlers tenant configurés via events_routes peuvent réagir
    (ex: store_in_memory, alerte audience, ...).
    """
    payload = {
        "call_id":       call_id,
        "contact_phone": contact_phone,
        "duration_sec":  duration_sec,
        "outcome":       outcome,
        "use_case":      use_case,
        "agent_id":      agent_id,
        "cost_eur":      cost_eur,
    }
    return events.publish(
        tenant_id=tenant_id,
        kind="call.completed",
        payload={k: v for k, v in payload.items() if v is not None},
        source_module="phone",
        correlation_id=f"call:{call_id}",
    )


def recall_for_contact(
    tenant_id: int,
    contact_phone: str,
    *,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Recherche sémantique des interactions passées avec ce contact."""
    return memory_vector.memory_search(
        tenant_id=tenant_id,
        query=contact_phone,
        k=k,
    )
