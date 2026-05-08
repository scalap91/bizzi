"""
moteur/team_loader.py
======================
Charge l'équipe complète d'un tenant : (DomainConfig, list[Agent]).

Le DomainConfig vient du YAML (`domains/<tenant_slug>.yaml`), les Agents
viennent de la table `agents` (peuplée par `scripts/sync_agents.py`).
Chaque Agent reçoit son `system_prompt` DB en `custom_prompt`, ce qui
écrase le template du métier dans `Agent.prompt`.

Usage :
    config, team = load_team("onyx")
    room = MeetingRoom(domain=config, agents=team)
    report = await room.run(agenda=["Économie", "Sport"])
"""

import os
from sqlalchemy import create_engine, text

from agents.base_agent import Agent
from config.domain_loader import DomainLoader, DomainConfig


_DB = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))

_DOMAINS_DIR = os.path.join(os.path.dirname(__file__), "..", "domains")


def _yaml_path_for(tenant_slug: str) -> str:
    return os.path.join(_DOMAINS_DIR, f"{tenant_slug}.yaml")


def load_team(tenant_slug: str) -> tuple[DomainConfig, list[Agent]]:
    """Retourne (DomainConfig, list[Agent]) pour un tenant."""
    yaml_path = _yaml_path_for(tenant_slug)
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(
            f"YAML introuvable pour tenant '{tenant_slug}' : {yaml_path}"
        )
    config = DomainLoader(yaml_path).load()

    with _DB.connect() as conn:
        t = conn.execute(
            text("SELECT id FROM tenants WHERE slug = :s"),
            {"s": tenant_slug},
        ).fetchone()
        if not t:
            raise ValueError(f"Tenant '{tenant_slug}' introuvable en DB")
        tenant_id = t[0]

        rows = conn.execute(text("""
            SELECT slug, name, agent_id, specialty, system_prompt, status
            FROM agents
            WHERE tenant_id = :tid
            ORDER BY
                CASE role
                    WHEN 'direction'    THEN 1
                    WHEN 'validation'   THEN 2
                    WHEN 'distribution' THEN 3
                    WHEN 'verification' THEN 4
                    WHEN 'production'   THEN 5
                    ELSE 9
                END,
                name
        """), {"tid": tenant_id}).fetchall()

    agents = [
        Agent(
            slug          = r[0],
            name          = r[1],
            agent_id      = r[2],
            domain        = config,
            specialty     = r[3] or "",
            custom_prompt = r[4] or "",
            status        = (r[5] or "active") if isinstance(r[5], str) else (r[5].value if r[5] else "active"),
        )
        for r in rows
    ]
    return config, agents


def get_tenant_id(tenant_slug: str) -> int:
    with _DB.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM tenants WHERE slug = :s"),
            {"s": tenant_slug},
        ).fetchone()
    if not row:
        raise ValueError(f"Tenant '{tenant_slug}' introuvable")
    return row[0]
