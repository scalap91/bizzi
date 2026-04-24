"""tools/complaint/complaint_agent.py — Gestion des plaintes universelle"""
import logging, httpx, os
from datetime import datetime
from uuid import uuid4
from config.domain_loader import DomainConfig

logger = logging.getLogger("tools.complaint")

PRIORITIES = {
    "urgent": ["urgence","immédiat","avocat","tribunal","procès","médias","scandale","twitter"],
    "high":   ["insatisfait","remboursement","problème grave","erreur","honteux","inacceptable"],
    "normal": ["plainte","réclamation","mécontentement","déçu","pas satisfait"],
    "low":    ["suggestion","amélioration","remarque","commentaire"],
}

TICKETS_DB: dict = {}


class ComplaintAgent:
    """Agent de gestion des plaintes. Classifie, répond, crée des tickets."""

    def __init__(self, domain: DomainConfig):
        self.domain     = domain
        self.admin_email = os.getenv("ADMIN_EMAIL", "admin@org.fr")

    def detect_priority(self, content: str) -> str:
        """Détecte la priorité de la plainte."""
        text = content.lower()
        for priority, keywords in PRIORITIES.items():
            if any(k in text for k in keywords):
                return priority
        return "normal"

    def create_ticket(self, name: str, email: str, content: str, priority: str, channel: str) -> dict:
        """Crée un ticket de plainte."""
        ticket_id = f"TKT-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:6].upper()}"
        ticket = {
            "id":         ticket_id,
            "name":       name,
            "email":      email,
            "content":    content,
            "priority":   priority,
            "channel":    channel,  # email / chat / phone / web
            "status":     "open",
            "domain":     self.domain.domain,
            "org":        self.domain.name,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "notes":      [],
        }
        TICKETS_DB[ticket_id] = ticket
        logger.info(f"[COMPLAINT] Ticket créé : {ticket_id} · priorité={priority} · canal={channel}")
        return ticket

    async def generate_empathy_response(self, name: str, content: str, priority: str) -> str:
        """Génère une réponse empathique via Ollama."""
        prompt = f"""Tu es le responsable des relations clients de {self.domain.name}.
Tu reçois une plainte de {name} (priorité : {priority}).
Plainte : {content[:400]}

Rédige une réponse empathique en français (4-5 phrases) :
1. Accuse réception et exprime de l'empathie sincère
2. Reconnais le problème sans te justifier
3. Explique les prochaines étapes concrètes
4. Donne un délai de traitement réaliste
5. Propose un contact direct si urgent

Termine par : "Cordialement, L'équipe {self.domain.name}"
Réponse :"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post("http://localhost:11434/api/generate",
                    json={"model":"mistral:7b","prompt":prompt,"stream":False,
                          "options":{"temperature":0.4,"num_predict":350}})
                if r.status_code == 200:
                    return r.json().get("response","").strip()
        except Exception as e:
            logger.error(f"[COMPLAINT] Ollama error: {e}")

        delay = "24h" if priority == "urgent" else "48h" if priority == "high" else "5 jours ouvrés"
        return (
            f"Chère {name}, nous avons bien reçu votre message et nous en sommes sincèrement désolés. "
            f"Votre plainte est enregistrée sous haute priorité et sera traitée dans les {delay}. "
            f"Un responsable vous contactera directement. "
            f"Cordialement, L'équipe {self.domain.name}"
        )

    def notify_team(self, ticket: dict) -> bool:
        """Notifie l'équipe selon la priorité."""
        if ticket["priority"] in ("urgent", "high"):
            logger.info(f"[COMPLAINT] Notification équipe → {self.admin_email} · ticket={ticket['id']}")
            # En prod : send_email(admin_email, ...)
            return True
        return False

    async def process(self, name: str, email: str, content: str, channel: str = "web") -> dict:
        """Traite une plainte de bout en bout."""
        priority = self.detect_priority(content)
        ticket   = self.create_ticket(name, email, content, priority, channel)
        response = await self.generate_empathy_response(name, content, priority)
        notified = self.notify_team(ticket)

        return {
            "ticket_id":      ticket["id"],
            "priority":       priority,
            "response":       response,
            "team_notified":  notified,
            "status":         "open",
            "next_action":    f"Traitement sous {'24h' if priority=='urgent' else '48h' if priority=='high' else '5j'}",
            "processed_at":   datetime.utcnow().isoformat(),
        }

    def get_ticket(self, ticket_id: str) -> dict | None:
        return TICKETS_DB.get(ticket_id)

    def update_ticket(self, ticket_id: str, status: str, note: str = "") -> dict | None:
        if ticket_id not in TICKETS_DB:
            return None
        TICKETS_DB[ticket_id]["status"]     = status
        TICKETS_DB[ticket_id]["updated_at"] = datetime.utcnow().isoformat()
        if note:
            TICKETS_DB[ticket_id]["notes"].append({"note": note, "at": datetime.utcnow().isoformat()})
        return TICKETS_DB[ticket_id]

    def list_tickets(self, status: str = None, priority: str = None) -> list:
        tickets = list(TICKETS_DB.values())
        if status:   tickets = [t for t in tickets if t["status"]   == status]
        if priority: tickets = [t for t in tickets if t["priority"] == priority]
        return sorted(tickets, key=lambda t: t["created_at"], reverse=True)
