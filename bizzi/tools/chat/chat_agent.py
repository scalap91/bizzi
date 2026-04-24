"""tools/chat/chat_agent.py — Agent de chat visiteur universel"""
import logging, httpx
from datetime import datetime
from config.domain_loader import DomainConfig

logger = logging.getLogger("tools.chat")

INTENTS = {
    "complaint": ["plainte","problème","insatisfait","réclamation","honte","choqué","scandale"],
    "rgpd":      ["données personnelles","supprimer mon compte","mes informations","rgpd","droit d'accès","effacer"],
    "join":      ["adhérer","rejoindre","membre","inscription","s'inscrire","comment rejoindre"],
    "contact":   ["contacter","parler à","humain","conseiller","rappel","rendez-vous"],
    "pricing":   ["prix","tarif","coût","combien","offre","abonnement"],
}
ACTIONS = {
    "complaint": ["Déposer une plainte formelle","Parler à un conseiller","Consulter notre politique"],
    "rgpd":      ["Faire une demande d'accès","Demander la suppression","Contacter le DPO"],
    "join":      ["Voir nos offres","S'inscrire maintenant","Poser une question"],
    "contact":   ["Demander un rappel","Envoyer un email","Prendre rendez-vous"],
    "pricing":   ["Voir les tarifs","Parler à un conseiller","Essai gratuit"],
    "info":      ["En savoir plus","Nous contacter","FAQ"],
}

class ChatAgent:
    def __init__(self, domain: DomainConfig, session_id: str):
        self.domain     = domain
        self.session_id = session_id
        self.history: list[dict] = []

    def detect_intent(self, msg: str) -> str:
        ml = msg.lower()
        for intent, kws in INTENTS.items():
            if any(k in ml for k in kws): return intent
        return "info"

    async def reply(self, message: str) -> dict:
        intent  = self.detect_intent(message)
        ctx     = "\n".join(f"{m['role'].upper()} : {m['content']}" for m in self.history[-6:])
        rules   = "\n".join(f"- {r}" for r in self.domain.editorial_rules)
        prompt  = f"""Tu es l'agent d'accueil de {self.domain.name}. Tagline : {self.domain.tagline}.
Règles : {rules}
Réponds en français, 3-4 phrases max, professionnel et chaleureux.
Historique :\n{ctx or "(début)"}
VISITEUR : {message}\nAGENT :"""

        text = ""
        needs_human = False
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post("http://localhost:11434/api/generate",
                    json={"model":"mistral:7b","prompt":prompt,"stream":False,"options":{"temperature":0.5,"num_predict":250}})
                if r.status_code == 200:
                    text = r.json().get("response","").strip()
        except Exception as e:
            logger.error(f"[CHAT] {e}")
            text = f"Bonjour ! Difficulté technique. Contactez-nous directement. L'équipe {self.domain.name}"
            needs_human = True

        if any(k in text.lower() for k in ["je ne sais pas","impossible","transférer","contactez"]):
            needs_human = True

        self.history += [
            {"role":"user",      "content":message, "time":datetime.utcnow().isoformat()},
            {"role":"assistant", "content":text,    "time":datetime.utcnow().isoformat()},
        ]
        return {"session_id":self.session_id,"response":text,"intent":intent,
                "suggested_actions":ACTIONS.get(intent,ACTIONS["info"]),"needs_human":needs_human,
                "timestamp":datetime.utcnow().isoformat()}

SESSIONS: dict[str, ChatAgent] = {}
def get_session(session_id: str, domain: DomainConfig) -> ChatAgent:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = ChatAgent(domain=domain, session_id=session_id)
    return SESSIONS[session_id]
