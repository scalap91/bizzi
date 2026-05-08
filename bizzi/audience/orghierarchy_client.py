"""Coordination avec le futur module bizzi-orghierarchy.

Ce dernier publiera `get_visible_units(claims)` qui retourne la liste
des `org_unit_id` visibles par le porteur du JWT (sa propre unité +
descendants, ou tout le tenant pour un admin).

Phase 0 : stub local — l'implémentation réelle viendra du sub-Claude
bizzi-orghierarchy. La signature est figée pour permettre la transition
sans casser les appelants.

Règles Phase 0 :
- role 'admin' / 'national' : None (= pas de filtre, voit tout le tenant)
- role 'federation'         : [org_unit_id] (Phase 1 = + descendants)
- role 'section' / 'secretaire_section' / 'membre' : [org_unit_id]
- pas de org_unit_id : []  (aucun accès)
"""
from __future__ import annotations

from typing import Optional

from .auth import JWTClaims


_BROAD_ROLES = {"admin", "national", "tenant_admin", "owner"}


def get_visible_units(claims: JWTClaims) -> Optional[list[int]]:
    """Retourne la liste des org_unit_id visibles par le porteur.

    None  → pas de filtre (admin)
    []    → aucun accès (claim mal formé)
    [int] → filtre strict
    """
    if claims.role in _BROAD_ROLES:
        return None
    if claims.org_unit_id is None:
        return []
    # Phase 0 : unique unité. Phase 1 : appel module bizzi-orghierarchy
    # pour récupérer les descendants.
    return [int(claims.org_unit_id)]
