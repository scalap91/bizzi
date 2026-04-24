"""api/routes/tools_mail_config.py — Configuration mail par tenant"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from tools.email.mail_connector import MailConnector, MailConfig, list_providers

router = APIRouter()

# Stockage des configs mail par tenant (en prod → DB chiffrée)
MAIL_CONFIGS: dict = {}

class MailConfigCreate(BaseModel):
    tenant_id:  str
    agent_slug: str
    email:      str
    password:   str
    provider:   str = "ovh"
    from_name:  Optional[str] = ""
    # Paramètres custom (si provider = "custom")
    imap_host:  Optional[str] = ""
    imap_port:  Optional[int] = 993
    smtp_host:  Optional[str] = ""
    smtp_port:  Optional[int] = 587

class MailTestRequest(BaseModel):
    tenant_id:  str
    agent_slug: str

class SendTestEmail(BaseModel):
    tenant_id:  str
    agent_slug: str
    to:         str
    subject:    str = "Test Bizzi"
    body:       str = "Ceci est un email de test envoyé par Bizzi."


@router.get("/providers")
async def get_providers():
    """Liste tous les providers mail supportés."""
    return {"providers": list_providers()}


@router.post("/configure")
async def configure_mail(data: MailConfigCreate):
    """Configure la boîte mail d'un agent pour un tenant."""
    key = f"{data.tenant_id}_{data.agent_slug}"

    overrides = {}
    if data.provider == "custom":
        if not data.imap_host or not data.smtp_host:
            raise HTTPException(400, "Provider 'custom' nécessite imap_host et smtp_host")
        overrides = {
            "imap_host": data.imap_host,
            "imap_port": data.imap_port,
            "smtp_host": data.smtp_host,
            "smtp_port": data.smtp_port,
        }

    cfg = MailConfig.from_provider(
        provider  = data.provider,
        email     = data.email,
        password  = data.password,
        from_name = data.from_name or data.email,
        **overrides,
    )

    # Stocker sans le mot de passe en clair dans la réponse
    MAIL_CONFIGS[key] = cfg

    return {
        "status":    "configured",
        "tenant_id": data.tenant_id,
        "agent":     data.agent_slug,
        "email":     data.email,
        "provider":  data.provider,
        "imap":      f"{cfg.imap_host}:{cfg.imap_port}",
        "smtp":      f"{cfg.smtp_host}:{cfg.smtp_port}",
    }


@router.post("/test")
async def test_connection(data: MailTestRequest):
    """Teste la connexion IMAP et SMTP d'un agent."""
    key = f"{data.tenant_id}_{data.agent_slug}"
    cfg = MAIL_CONFIGS.get(key)
    if not cfg:
        raise HTTPException(404, f"Aucune config mail pour {data.agent_slug} (tenant={data.tenant_id})")

    connector = MailConnector(cfg)
    result    = connector.test_connection()
    return result


@router.post("/send-test")
async def send_test_email(data: SendTestEmail):
    """Envoie un email de test."""
    key = f"{data.tenant_id}_{data.agent_slug}"
    cfg = MAIL_CONFIGS.get(key)
    if not cfg:
        raise HTTPException(404, f"Aucune config mail pour {data.agent_slug}")

    connector = MailConnector(cfg)
    ok = connector.send(
        to      = data.to,
        subject = data.subject,
        body    = data.body,
    )
    return {"status": "sent" if ok else "failed", "to": data.to}


@router.get("/inbox/{tenant_id}/{agent_slug}")
async def get_inbox(tenant_id: str, agent_slug: str, limit: int = 20, unread_only: bool = False):
    """Récupère les emails de la boîte de l'agent."""
    key = f"{tenant_id}_{agent_slug}"
    cfg = MAIL_CONFIGS.get(key)
    if not cfg:
        raise HTTPException(404, f"Aucune config mail pour {agent_slug}")

    connector = MailConnector(cfg)
    messages  = connector.fetch_inbox(limit=limit, unread_only=unread_only)

    return {
        "agent":    agent_slug,
        "email":    cfg.email,
        "count":    len(messages),
        "messages": [
            {
                "uid":       m.uid,
                "from":      m.from_addr,
                "subject":   m.subject,
                "date":      m.date,
                "preview":   m.body[:200],
                "is_read":   m.is_read,
            }
            for m in messages
        ]
    }


@router.get("/configs/{tenant_id}")
async def list_configs(tenant_id: str):
    """Liste les boîtes configurées pour un tenant."""
    configs = [
        {
            "agent":    key.replace(f"{tenant_id}_", ""),
            "email":    cfg.email,
            "provider": cfg.provider,
            "imap_ok":  bool(cfg.imap_host),
            "smtp_ok":  bool(cfg.smtp_host),
        }
        for key, cfg in MAIL_CONFIGS.items()
        if key.startswith(f"{tenant_id}_")
    ]
    return {"tenant_id": tenant_id, "mail_configs": configs, "count": len(configs)}


@router.delete("/configs/{tenant_id}/{agent_slug}")
async def delete_config(tenant_id: str, agent_slug: str):
    """Supprime la config mail d'un agent."""
    key = f"{tenant_id}_{agent_slug}"
    if key not in MAIL_CONFIGS:
        raise HTTPException(404, "Config introuvable")
    del MAIL_CONFIGS[key]
    return {"status": "deleted", "agent": agent_slug}
