"""tenant_db/base.py — interfaces et data-classes partagées."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class QueryDef:
    """Définition déclarative d'une query autorisée pour un tenant."""
    name: str
    sql: str
    params: list[str] = field(default_factory=list)
    description: str = ""
    returns: str = "rows"  # "rows" | "row" | "scalar" | "count"
    max_rows: int = 50     # cap sur LIMIT pour éviter dump massif


@dataclass
class LLMConfig:
    """Config LLM par tenant (modèle Claude, max_tokens, température)."""
    model: str = "claude-haiku-4-5"
    max_tokens: int = 1024
    temperature: float = 0.7


@dataclass
class RateLimitConfig:
    """Config de rate-limit par tenant."""
    max_per_day: int = 100
    max_tokens_per_day: int = 200000


@dataclass
class TenantConfig:
    """Config statique d'un tenant : connexion DB + queries + LLM + rate-limit."""
    slug: str
    db_type: str           # "postgres" | "mysql" | "sqlite" (extensible)
    db_dsn: str            # ex: "host=127.0.0.1 dbname=airbizness user=bizzi_reader password=..."
    queries: dict[str, QueryDef]
    metadata: dict[str, Any] = field(default_factory=dict)
    # Champs étendus pour le chat agent multi-tenant
    agent_persona: str = ""
    system_prompt: str = ""
    llm: LLMConfig = field(default_factory=LLMConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)


class TenantDBProvider:
    """Interface : exécute une query nommée avec ses params, retourne un résultat sérialisable."""

    def __init__(self, config: TenantConfig):
        self.config = config

    def execute(self, query_name: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass
