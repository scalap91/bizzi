"""api/routes/tenant.py"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import secrets

router = APIRouter()

TENANTS_DB: dict = {}

class TenantCreate(BaseModel):
    name:   str
    domain: str
    plan:   str = "starter"   # starter / pro / business / enterprise
    email:  str

@router.post("/create")
async def create_tenant(data: TenantCreate):
    tenant_id = data.name.lower().replace(" ", "-")
    if tenant_id in TENANTS_DB:
        raise HTTPException(status_code=409, detail=f"Tenant '{tenant_id}' existe déjà")

    token = secrets.token_urlsafe(32)
    tenant = {
        "id":         tenant_id,
        "name":       data.name,
        "domain":     data.domain,
        "plan":       data.plan,
        "email":      data.email,
        "token":      token,
        "created_at": datetime.utcnow().isoformat(),
        "status":     "active",
        "usage": {
            "pipeline_runs":   0,
            "content_produced":0,
            "chat_messages":   0,
            "emails_processed":0,
            "posters_generated":0,
            "calls_handled":   0,
        }
    }
    TENANTS_DB[tenant_id] = tenant
    return {"status": "created", "tenant_id": tenant_id, "token": token, "tenant": tenant}

@router.get("/{tenant_id}/status")
async def tenant_status(tenant_id: str):
    if tenant_id not in TENANTS_DB:
        raise HTTPException(status_code=404, detail="Tenant introuvable")
    t = TENANTS_DB[tenant_id]
    return {"id": tenant_id, "name": t["name"], "domain": t["domain"], "plan": t["plan"], "status": t["status"]}

@router.get("/{tenant_id}/usage")
async def tenant_usage(tenant_id: str):
    if tenant_id not in TENANTS_DB:
        raise HTTPException(status_code=404, detail="Tenant introuvable")
    return {"tenant_id": tenant_id, "usage": TENANTS_DB[tenant_id]["usage"]}
