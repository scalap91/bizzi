"""api/routes/tools_knowledge.py — API compétences des agents"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from tools.knowledge.knowledge_engine import KnowledgeEngine

router = APIRouter()

ALLOWED_TYPES = {
    "pdf": "pdf", "txt": "txt", "md": "txt", "csv": "csv",
    "docx": "docx", "doc": "doc", "xlsx": "xlsx", "xls": "xls",
}

class URLAdd(BaseModel):
    url:      str
    label:    Optional[str] = ""
    category: Optional[str] = "general"

class URLDelete(BaseModel):
    url: str

# ── Documents ─────────────────────────────────────────────────

@router.post("/{slug}/documents/upload")
async def upload_document(
    slug: str,
    file: UploadFile = File(...),
):
    """Upload un document dans le bureau de l'agent."""
    ext = file.filename.split(".")[-1].lower() if file.filename else ""
    if ext not in ALLOWED_TYPES:
        raise HTTPException(400, f"Type non supporté : .{ext}. Acceptés : {', '.join(ALLOWED_TYPES)}")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10 Mo max
        raise HTTPException(413, "Fichier trop volumineux (max 10 Mo)")

    engine = KnowledgeEngine(agent_slug=slug)
    entry  = engine.save_document(file.filename, content, ALLOWED_TYPES[ext])

    return {
        "status":   "uploaded",
        "agent":    slug,
        "document": entry,
    }

@router.get("/{slug}/documents")
async def list_documents(slug: str):
    """Liste les documents de l'agent."""
    engine = KnowledgeEngine(agent_slug=slug)
    return {"agent": slug, "documents": engine.list_documents()}

@router.delete("/{slug}/documents/{filename}")
async def delete_document(slug: str, filename: str):
    """Supprime un document."""
    engine = KnowledgeEngine(agent_slug=slug)
    ok     = engine.delete_document(filename)
    if not ok:
        raise HTTPException(404, f"Document '{filename}' introuvable")
    return {"status": "deleted", "filename": filename}

# ── URLs ──────────────────────────────────────────────────────

@router.post("/{slug}/urls")
async def add_url(slug: str, data: URLAdd):
    """Ajoute une URL à la bibliothèque de l'agent."""
    engine = KnowledgeEngine(agent_slug=slug)
    entry  = engine.add_url(data.url, data.label, data.category)
    return {"status": "added", "agent": slug, "url": entry}

@router.get("/{slug}/urls")
async def list_urls(slug: str):
    """Liste les URLs de l'agent."""
    engine = KnowledgeEngine(agent_slug=slug)
    return {"agent": slug, "urls": engine.list_urls()}

@router.delete("/{slug}/urls")
async def delete_url(slug: str, data: URLDelete):
    """Supprime une URL."""
    engine = KnowledgeEngine(agent_slug=slug)
    engine.delete_url(data.url)
    return {"status": "deleted", "url": data.url}

@router.get("/{slug}/urls/fetch")
async def fetch_url(slug: str, url: str):
    """Récupère et prévisualise le contenu d'une URL."""
    engine  = KnowledgeEngine(agent_slug=slug)
    content = await engine.fetch_url_content(url)
    return {"url": url, "preview": content[:500], "total_chars": len(content)}

# ── Mémoire ───────────────────────────────────────────────────

@router.get("/{slug}/memory")
async def get_memory(slug: str, topic: str = "", limit: int = 10):
    """Récupère la mémoire de l'agent."""
    engine = KnowledgeEngine(agent_slug=slug)
    return {"agent": slug, "memory": engine.get_memory(topic, limit)}

# ── Contexte ──────────────────────────────────────────────────

@router.get("/{slug}/context")
async def get_context(slug: str, topic: str = ""):
    """Retourne le contexte enrichi pour un sujet donné."""
    engine  = KnowledgeEngine(agent_slug=slug)
    context = await engine.get_context(topic)
    return {
        "agent":   slug,
        "topic":   topic,
        "context": context,
        "chars":   len(context),
    }

# ── Stats ─────────────────────────────────────────────────────

@router.get("/{slug}/stats")
async def knowledge_stats(slug: str):
    """Statistiques de la bibliothèque de l'agent."""
    engine = KnowledgeEngine(agent_slug=slug)
    return engine.stats()
