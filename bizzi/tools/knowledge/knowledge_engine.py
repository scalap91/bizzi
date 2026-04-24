"""
tools/knowledge/knowledge_engine.py
=====================================
Moteur de compétences des agents.

Chaque agent dispose d'une bibliothèque personnelle :
  - Documents uploadés (PDF, Word, Excel, CSV, TXT)
  - URLs à consulter (articles, lois, rapports en ligne)
  - Mémoire interne (ce qu'il a déjà produit et appris)

Avant de produire un contenu, l'agent consulte
sa bibliothèque et injecte le contexte pertinent
dans son prompt.

Usage :
    engine  = KnowledgeEngine(agent_slug="lucas-martin")
    context = await engine.get_context(topic="Déserts médicaux")
    result  = await agent.produce(topic, context=context)
"""

import os, json, logging, httpx, asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tools.knowledge")

# Dossier racine des compétences
KNOWLEDGE_ROOT = Path(os.getenv("KNOWLEDGE_ROOT", "bureau/competences"))


class KnowledgeEngine:
    """
    Bibliothèque personnelle d'un agent.
    Gère documents, URLs et mémoire.
    """

    def __init__(self, agent_slug: str):
        self.slug     = agent_slug
        self.base_dir = KNOWLEDGE_ROOT / agent_slug
        self.docs_dir = self.base_dir / "documents"
        self.urls_file  = self.base_dir / "urls.json"
        self.index_file = self.base_dir / "index.json"
        self.memory_file= self.base_dir / "memory.json"

        # Créer les dossiers si inexistants
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._init_files()

    def _init_files(self):
        """Initialise les fichiers JSON si absents."""
        for path, default in [
            (self.urls_file,   {"urls": []}),
            (self.index_file,  {"documents": [], "urls": [], "last_updated": None}),
            (self.memory_file, {"entries": [], "total": 0}),
        ]:
            if not path.exists():
                path.write_text(json.dumps(default, ensure_ascii=False, indent=2))

    # ── DOCUMENTS ────────────────────────────────────────────

    def save_document(self, filename: str, content: bytes, doc_type: str = "pdf") -> dict:
        """Sauvegarde un document uploadé."""
        safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")
        filepath  = self.docs_dir / safe_name

        filepath.write_bytes(content)

        # Extraire le texte selon le type
        text = self._extract_text(filepath, doc_type)

        # Ajouter à l'index
        index = self._load_json(self.index_file)
        entry = {
            "filename":    safe_name,
            "type":        doc_type,
            "size":        len(content),
            "text_preview":text[:500] if text else "",
            "char_count":  len(text),
            "added_at":    datetime.utcnow().isoformat(),
            "filepath":    str(filepath),
        }
        index["documents"].append(entry)
        index["last_updated"] = datetime.utcnow().isoformat()
        self._save_json(self.index_file, index)

        logger.info(f"[KNOWLEDGE] {self.slug} ← document : {safe_name} ({len(content)//1024}Ko)")
        return entry

    def _extract_text(self, filepath: Path, doc_type: str) -> str:
        """Extrait le texte d'un document selon son type."""
        try:
            if doc_type in ("txt", "md", "csv"):
                return filepath.read_text(encoding="utf-8", errors="ignore")

            elif doc_type == "pdf":
                # PyMuPDF si disponible, sinon texte brut
                try:
                    import fitz
                    doc  = fitz.open(str(filepath))
                    text = "\n".join(page.get_text() for page in doc)
                    doc.close()
                    return text
                except ImportError:
                    return f"[PDF : {filepath.name} — installer pymupdf pour extraire le texte]"

            elif doc_type in ("docx", "doc"):
                try:
                    import docx
                    d    = docx.Document(str(filepath))
                    return "\n".join(p.text for p in d.paragraphs)
                except ImportError:
                    return f"[Word : {filepath.name} — installer python-docx pour extraire le texte]"

            elif doc_type in ("xlsx", "xls"):
                try:
                    import openpyxl
                    wb   = openpyxl.load_workbook(str(filepath), read_only=True)
                    rows = []
                    for ws in wb.worksheets:
                        for row in ws.iter_rows(values_only=True):
                            rows.append(" | ".join(str(c) for c in row if c is not None))
                    return "\n".join(rows)
                except ImportError:
                    return f"[Excel : {filepath.name} — installer openpyxl pour extraire les données]"

        except Exception as e:
            logger.warning(f"[KNOWLEDGE] Extraction texte échouée pour {filepath.name}: {e}")

        return ""

    def list_documents(self) -> list:
        """Liste tous les documents de l'agent."""
        index = self._load_json(self.index_file)
        return index.get("documents", [])

    def delete_document(self, filename: str) -> bool:
        """Supprime un document."""
        filepath = self.docs_dir / filename
        if filepath.exists():
            filepath.unlink()
            index = self._load_json(self.index_file)
            index["documents"] = [d for d in index["documents"] if d["filename"] != filename]
            self._save_json(self.index_file, index)
            logger.info(f"[KNOWLEDGE] {self.slug} ✕ document supprimé : {filename}")
            return True
        return False

    # ── URLS ─────────────────────────────────────────────────

    def add_url(self, url: str, label: str = "", category: str = "general") -> dict:
        """Ajoute une URL à consulter."""
        urls_data = self._load_json(self.urls_file)
        entry = {
            "url":      url,
            "label":    label or url,
            "category": category,
            "added_at": datetime.utcnow().isoformat(),
            "last_fetched": None,
            "preview":  "",
        }
        urls_data["urls"].append(entry)
        self._save_json(self.urls_file, urls_data)
        logger.info(f"[KNOWLEDGE] {self.slug} ← URL : {label or url}")
        return entry

    def list_urls(self) -> list:
        return self._load_json(self.urls_file).get("urls", [])

    def delete_url(self, url: str) -> bool:
        data = self._load_json(self.urls_file)
        data["urls"] = [u for u in data["urls"] if u["url"] != url]
        self._save_json(self.urls_file, data)
        return True

    async def fetch_url_content(self, url: str) -> str:
        """Télécharge et retourne le contenu d'une URL."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(url, headers={"User-Agent": "CoreAgents-Bot/1.0"})
                if r.status_code == 200:
                    text = r.text
                    # Nettoyage HTML basique
                    import re
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text[:3000]  # 3000 chars max par URL
        except Exception as e:
            logger.warning(f"[KNOWLEDGE] Fetch échoué {url}: {e}")
        return ""

    # ── MÉMOIRE ───────────────────────────────────────────────

    def add_memory(self, content: str, topic: str, source: str = "pipeline") -> dict:
        """Ajoute une entrée en mémoire (ce que l'agent a produit/appris)."""
        memory = self._load_json(self.memory_file)
        entry  = {
            "topic":      topic,
            "content":    content[:1000],
            "source":     source,
            "added_at":   datetime.utcnow().isoformat(),
        }
        memory["entries"].append(entry)
        memory["total"] = len(memory["entries"])
        # Garder les 100 dernières entrées
        if len(memory["entries"]) > 100:
            memory["entries"] = memory["entries"][-100:]
        self._save_json(self.memory_file, memory)
        return entry

    def get_memory(self, topic: str = "", limit: int = 5) -> list:
        """Récupère les entrées mémoire pertinentes pour un sujet."""
        memory  = self._load_json(self.memory_file)
        entries = memory.get("entries", [])
        if topic:
            # Filtrer par pertinence simple (mots-clés)
            topic_words = topic.lower().split()
            scored = []
            for e in entries:
                score = sum(1 for w in topic_words if w in e.get("content","").lower() or w in e.get("topic","").lower())
                if score > 0:
                    scored.append((score, e))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [e for _, e in scored[:limit]]
        return entries[-limit:]

    # ── CONTEXTE POUR LE PROMPT ───────────────────────────────

    async def get_context(self, topic: str, max_chars: int = 4000) -> str:
        """
        Point d'entrée principal.
        Retourne un contexte enrichi pour le prompt de l'agent.
        Combine documents + URLs + mémoire.
        """
        parts = []
        used  = 0

        # 1. Documents pertinents
        docs = self.list_documents()
        if docs:
            parts.append("=== DOCUMENTS DE RÉFÉRENCE ===")
            for doc in docs[:5]:  # 5 docs max
                preview = doc.get("text_preview", "")
                if preview and used + len(preview) < max_chars:
                    parts.append(f"\n📄 {doc['filename']} :\n{preview}")
                    used += len(preview)

        # 2. Contenu des URLs
        urls = self.list_urls()
        if urls:
            parts.append("\n=== SOURCES EN LIGNE ===")
            tasks = [self.fetch_url_content(u["url"]) for u in urls[:3]]  # 3 URLs max
            contents = await asyncio.gather(*tasks, return_exceptions=True)
            for url_entry, content in zip(urls[:3], contents):
                if isinstance(content, str) and content and used + len(content) < max_chars:
                    label = url_entry.get("label", url_entry["url"])
                    parts.append(f"\n🔗 {label} :\n{content[:800]}")
                    used += len(content[:800])

        # 3. Mémoire pertinente
        memories = self.get_memory(topic, limit=3)
        if memories:
            parts.append("\n=== MÉMOIRE DE TRAVAIL ===")
            for m in memories:
                snippet = m.get("content","")[:300]
                if used + len(snippet) < max_chars:
                    parts.append(f"\n🧠 [{m.get('topic','')}] : {snippet}")
                    used += len(snippet)

        if not parts:
            return ""

        context = "\n".join(parts)
        logger.info(f"[KNOWLEDGE] {self.slug} → contexte {len(context)} chars pour '{topic}'")
        return context

    def stats(self) -> dict:
        """Statistiques de la bibliothèque de l'agent."""
        docs   = self.list_documents()
        urls   = self.list_urls()
        memory = self._load_json(self.memory_file)
        return {
            "agent":          self.slug,
            "documents":      len(docs),
            "total_size_kb":  sum(d.get("size",0) for d in docs) // 1024,
            "urls":           len(urls),
            "memory_entries": memory.get("total", 0),
            "last_updated":   self._load_json(self.index_file).get("last_updated"),
        }

    # ── UTILS ─────────────────────────────────────────────────

    def _load_json(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except:
            return {}

    def _save_json(self, path: Path, data: dict):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
