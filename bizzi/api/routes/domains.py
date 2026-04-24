"""api/routes/domains.py"""
from fastapi import APIRouter, HTTPException
from config.domain_loader import DomainLoader

router = APIRouter()

@router.get("/list")
async def list_domains():
    return {"domains": DomainLoader.list_available()}

@router.get("/{domain_name}")
async def get_domain(domain_name: str):
    try:
        cfg = DomainLoader.load_domain(domain_name)
        return {
            "domain":   cfg.domain,
            "name":     cfg.name,
            "tagline":  cfg.tagline,
            "agents":   [{"id": a.id, "title": a.title, "role": a.role, "required": a.required} for a in cfg.agents],
            "pipeline": {"schedule": cfg.pipeline.schedule, "steps": cfg.pipeline.steps},
            "output":   {"type": cfg.output.type, "score_min": cfg.output.validation_score_min, "formats": cfg.output.formats},
            "ui":       {"primary_color": cfg.ui.primary_color, "vocabulary": vars(cfg.ui.vocabulary)},
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
