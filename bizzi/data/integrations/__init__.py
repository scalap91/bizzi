"""bizzi.data.integrations — Helpers opt-in pour les modules consommateurs.

Aucun import depuis ces modules n'est forcé dans phone/social/audience.
Les modules consommateurs peuvent CHOISIR d'importer ces helpers pour
profiter des capacités data sans coupler leur code à bizzi.data en dur.

Architecture :
    bizzi.data         (canonique : connecteurs, vues, mémoire, events)
    bizzi.data.integrations.audience  (bridge audience.event_bus → data.events)
    bizzi.data.integrations.phone     (recall pour contact, indexation transcript)
    bizzi.data.integrations.social    (data.view → post context, KPI mining)
"""
from . import audience, phone, social  # noqa: F401

__all__ = ["audience", "phone", "social"]
