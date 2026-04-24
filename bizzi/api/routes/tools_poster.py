"""api/routes/tools_poster.py"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from tools.poster.poster_agent import PosterAgent, PosterConfig
from config.domain_loader import DomainLoader

router = APIRouter()

class PosterRequest(BaseModel):
    title:        str
    subtitle:     Optional[str]  = None
    body:         Optional[str]  = None
    footer:       Optional[str]  = None
    logo_url:     Optional[str]  = None
    format:       str            = "a4"
    style:        str            = "modern"
    accent_color: Optional[str]  = None
    hashtags:     Optional[list] = None
    domain:       str            = "media"

@router.post("/generate")
async def generate_poster(data: PosterRequest):
    try: cfg = DomainLoader.load_domain(data.domain)
    except: cfg = DomainLoader.load_domain("media")
    agent  = PosterAgent(cfg)
    poster_cfg = PosterConfig(
        title=data.title, subtitle=data.subtitle, body=data.body,
        footer=data.footer, logo_url=data.logo_url, format=data.format,
        style=data.style, accent_color=data.accent_color,
        org_name=cfg.name, hashtags=data.hashtags,
    )
    result = await agent.generate(poster_cfg)
    return {"status": "generated", "poster": {k: v for k, v in result.items() if k != "html"}, "html_length": len(result.get("html",""))}

@router.post("/preview", response_class=HTMLResponse)
async def preview_poster(data: PosterRequest):
    """Retourne le HTML de l'affiche directement pour prévisualisation."""
    try: cfg = DomainLoader.load_domain(data.domain)
    except: cfg = DomainLoader.load_domain("media")
    agent = PosterAgent(cfg)
    poster_cfg = PosterConfig(
        title=data.title, subtitle=data.subtitle, body=data.body,
        format=data.format, style=data.style, accent_color=data.accent_color,
        org_name=cfg.name, hashtags=data.hashtags,
    )
    result = await agent.generate(poster_cfg)
    return HTMLResponse(content=result["html"])
