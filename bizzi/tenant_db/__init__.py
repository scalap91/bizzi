"""tenant_db — couche d'abstraction pour brancher Bizzi sur la BD de chaque tenant.

Pattern : 1 fichier YAML par tenant dans /opt/bizzi/bizzi/tenants/, déclarant
la connexion DB + les queries autorisées (whitelist SQL prédéfini).
L'agent Claude reçoit ces queries comme tools dynamiques.
"""
from .registry import load_tenant, list_tenants, TenantNotFound
from .base import TenantConfig, QueryDef, TenantDBProvider

__all__ = [
    "load_tenant", "list_tenants", "TenantNotFound",
    "TenantConfig", "QueryDef", "TenantDBProvider",
]
