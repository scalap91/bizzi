"""Génération de propositions de contenu (Phase 1).

Universel : les types de propositions activés sont définis dans le YAML
tenant `audience.content_generation.auto_propose` :

  reply_text       : tous tenants — réponse type prête à envoyer
  facebook_post    : politique/marque
  ticket_zendesk   : SAV
  faq_entry        : SaaS / support
  bug_issue        : SaaS dev (issue GitHub/GitLab/Linear)
  improvement_idea : tous
  synthesis_report : tous (rapport hebdo)
  video_clip       : politique/marque (délègue à bizzi.social.video_generator)

Phase 0 : seuls les stubs et la signature. Phase 1 : implémenter chaque
generator + plug bizzi.social.video_generator pour `video_clip`.
"""
from __future__ import annotations

from typing import Any, Optional


PROPOSAL_TYPES = {
    "reply_text", "facebook_post", "ticket_zendesk", "faq_entry",
    "bug_issue", "improvement_idea", "synthesis_report", "video_clip",
}


def propose_for_alert(
    tenant_id: int,
    alert: dict[str, Any],
    *,
    auto_propose: dict[str, bool],
    sample_reports: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Génère la liste des propositions pour une alerte.

    Phase 0 : retourne une coquille vide [{"type": t, "status": "stub"}]
    par type activé dans `auto_propose`. Phase 1 : appelle gpt-4o-mini /
    bizzi.social.video_generator selon le type.
    """
    proposals: list[dict[str, Any]] = []
    for ptype in PROPOSAL_TYPES:
        if not auto_propose.get(ptype):
            continue
        proposals.append({
            "type": ptype,
            "status": "stub",  # Phase 1 : 'draft' avec contenu réel
            "tenant_id": tenant_id,
            "alert_id": alert.get("id"),
            "category": alert.get("category"),
        })
    return proposals
