import os
"""
API Comments — Onyx Infos
- POST /api/comments : créer un commentaire (modération IA auto)
- GET  /api/comments?production_id=X : lister les commentaires approuvés
- POST /api/comments/{id}/like : incrémenter le compteur de likes
- (admin) GET/POST /api/comments/admin : modération manuelle
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from openai import AsyncOpenAI
import hashlib, logging, json, re

router = APIRouter()
logger = logging.getLogger("api.comments")

engine = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))
openai_client = AsyncOpenAI(
    api_key=open("/opt/bizzi/bizzi/.env").read().split("OPENAI_API_KEY=")[1].split("\n")[0].strip()
)

# ── Anti-spam config ──────────────────────────────────────────
MAX_COMMENTS_PER_HOUR_PER_IP = 5
MAX_LENGTH = 2000
MIN_LENGTH = 3


def hash_ip(ip: str) -> str:
    """Hash SHA-256 tronqué de l'IP (RGPD-friendly)."""
    return hashlib.sha256(ip.encode()).hexdigest()[:64]


# ── Modèles Pydantic ──────────────────────────────────────────
class CommentCreate(BaseModel):
    production_id: int
    author_name: str = Field(..., min_length=2, max_length=80)
    author_email: Optional[str] = Field(None, max_length=200)
    content: str = Field(..., min_length=MIN_LENGTH, max_length=MAX_LENGTH)
    parent_id: Optional[int] = None
    # Honeypot anti-bot : champ caché côté front, doit rester vide
    website: Optional[str] = ""


class CommentOut(BaseModel):
    id: int
    parent_id: Optional[int]
    author_name: str
    content: str
    likes: int
    created_at: str
    replies: List["CommentOut"] = []


CommentOut.model_rebuild()


# ── Modération IA via GPT-4o mini ─────────────────────────────
async def moderate_comment(content: str, author_name: str) -> dict:
    """
    Retourne {decision: 'approve'|'reject'|'review', reason: str, category: str}.
    Catégories: ok, insulte, spam, hors_sujet, illegal, doute
    """
    prompt = f"""Tu es modérateur de commentaires pour un site d'actualité francais.
Analyse ce commentaire et retourne UNIQUEMENT un JSON valide (rien d'autre) :
{{"decision":"approve|reject|review","category":"ok|insulte|spam|hors_sujet|illegal|doute","reason":"explication courte"}}

Règles:
- "approve" si commentaire constructif, opinion respectueuse, question, désaccord poli
- "reject" si insultes graves, racisme, appel à la haine, spam évident, contenu illégal
- "review" en cas de doute (sarcasme limite, propos grossiers mais pas haineux, etc.)

Pseudo: {author_name}
Commentaire: {content[:1500]}"""

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)
        return {
            "decision": result.get("decision", "review"),
            "category": result.get("category", "doute"),
            "reason": result.get("reason", ""),
        }
    except Exception as e:
        logger.error(f"[MOD] {e}")
        # Erreur IA → on met en review pour modération manuelle
        return {"decision": "review", "category": "doute", "reason": f"IA fail: {e}"}


# ── Filtres rapides (avant IA) ────────────────────────────────
URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)
SPAM_KEYWORDS = {"viagra", "casino", "porn", "bitcoin gratuit", "free money", "click here"}


def quick_spam_check(content: str) -> Optional[str]:
    """Filtres rapides anti-spam. Retourne raison si spam, None sinon."""
    lower = content.lower()
    # Trop d'URLs
    urls = URL_PATTERN.findall(content)
    if len(urls) > 2:
        return "trop d'URLs"
    # Mots-clés spam
    for kw in SPAM_KEYWORDS:
        if kw in lower:
            return f"mot-clé spam: {kw}"
    # Tout en majuscules sur > 30 chars
    if len(content) > 30 and content.upper() == content and any(c.isalpha() for c in content):
        ratio_caps = sum(1 for c in content if c.isupper()) / max(1, sum(1 for c in content if c.isalpha()))
        if ratio_caps > 0.85:
            return "abus majuscules"
    # Répétition de caractères (aaaaaaaaaa)
    if re.search(r"(.)\1{9,}", content):
        return "répétition caractères"
    return None


# ── Rate-limit IP ─────────────────────────────────────────────
def check_rate_limit(conn, ip_hash: str) -> bool:
    """True si OK, False si limite dépassée."""
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    count = conn.execute(
        text("SELECT COUNT(*) FROM comments WHERE ip_hash=:h AND created_at >= :since"),
        {"h": ip_hash, "since": one_hour_ago},
    ).scalar()
    return count < MAX_COMMENTS_PER_HOUR_PER_IP


# ── POST /comments ────────────────────────────────────────────
@router.post("/comments")
async def create_comment(data: CommentCreate, request: Request):
    # Honeypot : si rempli = bot
    if data.website:
        logger.warning(f"[HONEYPOT] bot détecté: {data.website}")
        # On retourne 200 pour pas alerter le bot mais on n'enregistre rien
        return {"status": "published", "message": "Commentaire publié"}

    # Récup IP réelle (via proxy Nginx)
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    ip_h = hash_ip(ip)
    user_agent = (request.headers.get("user-agent", "") or "")[:300]

    # Filtre rapide
    spam_reason = quick_spam_check(data.content)
    if spam_reason:
        logger.info(f"[SPAM] {spam_reason} · {data.author_name}")
        # On insère en status='spam' pour les stats mais on ne montre pas
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO comments
                (production_id, parent_id, author_name, author_email, content,
                 status, ip_hash, user_agent, moderation_reason)
                VALUES
                (:pid, :par, :nm, :em, :ct, 'spam', :ip, :ua, :reason)
            """), {
                "pid": data.production_id, "par": data.parent_id,
                "nm": data.author_name.strip(), "em": data.author_email,
                "ct": data.content.strip(),
                "ip": ip_h, "ua": user_agent, "reason": spam_reason,
            })
            conn.commit()
        return {"status": "rejected", "message": "Commentaire refusé (spam détecté)"}

    # Vérif existence article + rate limit
    with engine.connect() as conn:
        prod = conn.execute(
            text("SELECT id FROM productions WHERE id=:pid LIMIT 1"),
            {"pid": data.production_id},
        ).fetchone()
        if not prod:
            raise HTTPException(404, "Article introuvable")

        if not check_rate_limit(conn, ip_h):
            raise HTTPException(429, "Trop de commentaires, réessaye dans 1h")

        # Vérif parent_id si réponse
        if data.parent_id:
            par = conn.execute(
                text("SELECT id FROM comments WHERE id=:pid AND production_id=:prod LIMIT 1"),
                {"pid": data.parent_id, "prod": data.production_id},
            ).fetchone()
            if not par:
                raise HTTPException(400, "Commentaire parent introuvable")

    # Modération IA
    mod = await moderate_comment(data.content.strip(), data.author_name.strip())

    if mod["decision"] == "approve":
        status = "approved"
        approved_at = datetime.utcnow()
    elif mod["decision"] == "reject":
        status = "rejected"
        approved_at = None
    else:
        status = "pending"
        approved_at = None

    # Insertion
    with engine.connect() as conn:
        new_id = conn.execute(text("""
            INSERT INTO comments
            (production_id, parent_id, author_name, author_email, content,
             status, ip_hash, user_agent, moderation_reason, approved_at)
            VALUES
            (:pid, :par, :nm, :em, :ct, :st, :ip, :ua, :reason, :appr)
            RETURNING id
        """), {
            "pid": data.production_id, "par": data.parent_id,
            "nm": data.author_name.strip(), "em": data.author_email,
            "ct": data.content.strip(),
            "st": status,
            "ip": ip_h, "ua": user_agent,
            "reason": f"{mod['category']}: {mod['reason']}",
            "appr": approved_at,
        }).fetchone()[0]
        conn.commit()

    logger.info(f"[COMMENT] #{new_id} · {status} · {mod['category']}")

    if status == "approved":
        return {"status": "published", "id": new_id, "message": "Commentaire publié"}
    elif status == "rejected":
        return {"status": "rejected", "id": new_id, "message": f"Commentaire refusé ({mod['category']})"}
    else:
        return {"status": "pending", "id": new_id, "message": "En attente de modération"}


# ── GET /comments ─────────────────────────────────────────────
@router.get("/comments")
async def list_comments(production_id: int, limit: int = 100):
    """Retourne les commentaires approuvés d'un article, structurés en threads."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, parent_id, author_name, content, likes, created_at
            FROM comments
            WHERE production_id = :pid AND status = 'approved'
            ORDER BY created_at ASC
            LIMIT :lim
        """), {"pid": production_id, "lim": limit}).fetchall()

    # Construire la structure thread (1 niveau de réponses)
    by_id = {}
    roots = []
    for r in rows:
        item = {
            "id": r[0], "parent_id": r[1], "author_name": r[2],
            "content": r[3], "likes": r[4], "created_at": str(r[5]),
            "replies": [],
        }
        by_id[r[0]] = item
        if r[1] is None:
            roots.append(item)
        else:
            parent = by_id.get(r[1])
            if parent:
                parent["replies"].append(item)

    return {"total": len(rows), "comments": roots}


# ── POST /comments/{id}/like ──────────────────────────────────
@router.post("/comments/{comment_id}/like")
async def like_comment(comment_id: int):
    with engine.connect() as conn:
        result = conn.execute(text("""
            UPDATE comments SET likes = likes + 1
            WHERE id = :id AND status = 'approved'
            RETURNING likes
        """), {"id": comment_id}).fetchone()
        conn.commit()
        if not result:
            raise HTTPException(404, "Commentaire introuvable")
        return {"id": comment_id, "likes": result[0]}


# ── GET /comments/count?production_id=X ──────────────────────
@router.get("/comments/count")
async def count_comments(production_id: int):
    with engine.connect() as conn:
        n = conn.execute(text("""
            SELECT COUNT(*) FROM comments
            WHERE production_id=:pid AND status='approved'
        """), {"pid": production_id}).scalar()
    return {"production_id": production_id, "count": n}
