"""tools/email/email_agent.py — Agent email universel"""
import logging, httpx, smtplib, os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config.domain_loader import DomainConfig

logger = logging.getLogger("tools.email")

CATEGORIES = {
    "complaint":  ["plainte","réclamation","insatisfait","problème","scandale","remboursement","honte"],
    "rgpd":       ["données","supprimer","rgpd","accès","effacer","portabilité","rectifier"],
    "join":       ["rejoindre","adhérer","membre","inscription","candidature","bénévolat"],
    "press":      ["presse","journaliste","média","interview","communiqué","attaché de presse"],
    "partner":    ["partenariat","collaboration","accord","contrat","business"],
    "support":    ["aide","assistance","question","comment","explication","information"],
    "spam":       ["promotion","offre exclusive","cliquez ici","gagnez","félicitations"],
}

AUTO_REPLIES = {
    "complaint": "Nous avons bien reçu votre réclamation et y donnons suite dans les 48h. Un conseiller vous contactera.",
    "rgpd":      "Votre demande RGPD a été enregistrée. Conformément au RGPD, nous y répondrons dans un délai de 30 jours.",
    "join":      "Merci de votre intérêt ! Votre demande d'adhésion a été reçue. Nous revenons vers vous sous 72h.",
    "press":     "Merci de votre intérêt médiatique. Notre service presse vous contactera dans les 24h.",
    "partner":   "Merci pour votre proposition de partenariat. Nous l'examinerons et vous répondrons sous 5 jours ouvrés.",
    "support":   "Merci pour votre message. Notre équipe vous répond dans les meilleurs délais.",
    "spam":      None,  # Pas de réponse aux spams
}

ESCALATE_CATEGORIES = {"complaint", "rgpd", "press", "partner"}


class EmailAgent:
    """Agent email universel. Classifie, répond, escalade."""

    def __init__(self, domain: DomainConfig):
        self.domain     = domain
        self.smtp_host  = os.getenv("SMTP_HOST", "localhost")
        self.smtp_port  = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user  = os.getenv("SMTP_USER", "")
        self.smtp_pass  = os.getenv("SMTP_PASS", "")
        self.from_email = os.getenv("FROM_EMAIL", f"contact@{domain.name.lower().replace(' ','')}.fr")

    def classify(self, subject: str, body: str) -> str:
        """Classifie l'email entrant."""
        text = (subject + " " + body).lower()
        for cat, keywords in CATEGORIES.items():
            if any(k in text for k in keywords):
                return cat
        return "support"

    async def generate_reply(self, sender: str, subject: str, body: str, category: str) -> str:
        """Génère une réponse personnalisée via Ollama."""
        base = AUTO_REPLIES.get(category, AUTO_REPLIES["support"])
        if not base:
            return ""

        prompt = f"""Tu es l'agent email de {self.domain.name}.
Tu réponds à un email de catégorie : {category}.
Expéditeur : {sender}
Sujet : {subject}
Message : {body[:500]}

Rédige une réponse professionnelle et bienveillante en français.
Commence par saluer l'expéditeur par son prénom si possible.
Base de réponse : {base}
Termine par : "Cordialement, L'équipe {self.domain.name}"
Réponse (4-6 phrases max) :"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post("http://localhost:11434/api/generate",
                    json={"model":"mistral:7b","prompt":prompt,"stream":False,
                          "options":{"temperature":0.4,"num_predict":300}})
                if r.status_code == 200:
                    return r.json().get("response","").strip()
        except Exception as e:
            logger.error(f"[EMAIL] Ollama error: {e}")

        return f"{base}\n\nCordialement,\nL'équipe {self.domain.name}"

    def send_email(self, to: str, subject: str, body: str) -> bool:
        """Envoie un email via SMTP."""
        if not self.smtp_user:
            logger.info(f"[EMAIL] SMTP non configuré — email simulé vers {to}")
            return True
        try:
            msg = MIMEMultipart()
            msg["From"]    = self.from_email
            msg["To"]      = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
            logger.info(f"[EMAIL] Envoyé → {to}")
            return True
        except Exception as e:
            logger.error(f"[EMAIL] SMTP error: {e}")
            return False

    async def process(self, sender: str, subject: str, body: str) -> dict:
        """Traite un email entrant de bout en bout."""
        category  = self.classify(subject, body)
        should_reply  = AUTO_REPLIES.get(category) is not None
        should_escalate = category in ESCALATE_CATEGORIES

        reply_text = ""
        reply_sent = False

        if should_reply:
            reply_text = await self.generate_reply(sender, subject, body, category)
            reply_sent = self.send_email(
                to      = sender,
                subject = f"RE : {subject}",
                body    = reply_text,
            )

        # Notifier l'admin si escalade nécessaire
        if should_escalate:
            admin_email = os.getenv("ADMIN_EMAIL", "admin@org.fr")
            self.send_email(
                to      = admin_email,
                subject = f"[ESCALADE {category.upper()}] {subject}",
                body    = f"Email reçu de : {sender}\nCatégorie : {category}\n\nMessage :\n{body}\n\nRéponse envoyée :\n{reply_text}",
            )

        result = {
            "sender":           sender,
            "subject":          subject,
            "category":         category,
            "should_escalate":  should_escalate,
            "reply_sent":       reply_sent,
            "reply_preview":    reply_text[:200] if reply_text else None,
            "processed_at":     datetime.utcnow().isoformat(),
        }

        logger.info(f"[EMAIL] {sender} → catégorie={category} escalade={should_escalate} réponse={reply_sent}")
        return result
