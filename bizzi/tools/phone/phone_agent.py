"""tools/phone/phone_agent.py — Agent téléphonique universel via Twilio"""
import logging, os, httpx
from datetime import datetime
from config.domain_loader import DomainConfig

logger = logging.getLogger("tools.phone")

# Intentions détectables à la voix
VOICE_INTENTS = {
    "complaint":  ["plainte","problème","insatisfait","réclamation","remboursement"],
    "rgpd":       ["données","supprimer","informations","rgpd"],
    "info":       ["information","renseignement","question","comment","horaires"],
    "urgent":     ["urgent","urgence","immédiat","maintenant","grave"],
    "human":      ["humain","personne","conseiller","parler à quelqu'un","transfert"],
}

TRANSFER_NUMBERS = {
    "complaint": os.getenv("PHONE_COMPLAINT", ""),
    "urgent":    os.getenv("PHONE_URGENT",    ""),
    "human":     os.getenv("PHONE_HUMAN",     ""),
    "default":   os.getenv("PHONE_DEFAULT",   ""),
}


class PhoneAgent:
    """Agent téléphonique universel. Accueil vocal + TTS + transfert."""

    def __init__(self, domain: DomainConfig):
        self.domain      = domain
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token  = os.getenv("TWILIO_AUTH_TOKEN",  "")
        self.phone_from  = os.getenv("TWILIO_PHONE_FROM",  "")

    @property
    def greeting(self) -> str:
        return (
            f"Bonjour et bienvenue chez {self.domain.name}. "
            f"{self.domain.tagline}. "
            f"Pour une information, dites 1. "
            f"Pour une réclamation, dites 2. "
            f"Pour parler à un conseiller, dites 3. "
            f"Pour toute autre demande, restez en ligne."
        )

    def detect_intent(self, transcription: str) -> str:
        """Détecte l'intention depuis la transcription vocale."""
        text = transcription.lower()
        for intent, keywords in VOICE_INTENTS.items():
            if any(k in text for k in keywords):
                return intent
        # Détecter les chiffres
        if "1" in text or "un" in text:   return "info"
        if "2" in text or "deux" in text: return "complaint"
        if "3" in text or "trois" in text:return "human"
        return "info"

    async def generate_voice_response(self, transcription: str, intent: str) -> str:
        """Génère une réponse vocale via Ollama."""
        prompt = f"""Tu es la standardiste téléphonique de {self.domain.name}.
Tu réponds à un appel. Intention détectée : {intent}.
Message du correspondant : {transcription}

Génère une réponse vocale naturelle, courte (2-3 phrases max).
Adapte au contexte {self.domain.domain}.
Si urgent ou plainte → propose un transfert vers un conseiller.
Si info simple → réponds directement.
Réponse :"""

        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post("http://localhost:11434/api/generate",
                    json={"model":"mistral:7b","prompt":prompt,"stream":False,
                          "options":{"temperature":0.4,"num_predict":150}})
                if r.status_code == 200:
                    return r.json().get("response","").strip()
        except Exception as e:
            logger.error(f"[PHONE] Ollama error: {e}")

        return f"Merci de votre appel chez {self.domain.name}. Un conseiller va prendre en charge votre demande."

    def twiml_greeting(self) -> str:
        """Génère le TwiML d'accueil pour Twilio."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="fr-FR" voice="Polly.Lea-Neural">{self.greeting}</Say>
  <Gather input="speech dtmf" timeout="5" action="/api/tools/phone/handle" method="POST" language="fr-FR">
    <Say language="fr-FR" voice="Polly.Lea-Neural">Je vous écoute.</Say>
  </Gather>
  <Say language="fr-FR" voice="Polly.Lea-Neural">Nous n'avons pas reçu votre réponse. Au revoir.</Say>
</Response>"""

    async def twiml_handle(self, speech_result: str = "", digits: str = "") -> str:
        """Gère la réponse de l'appelant et génère le TwiML de traitement."""
        transcription = speech_result or digits
        intent = self.detect_intent(transcription)
        transfer_num  = TRANSFER_NUMBERS.get(intent) or TRANSFER_NUMBERS["default"]

        if intent in ("complaint", "urgent", "human") and transfer_num:
            response_text = await self.generate_voice_response(transcription, intent)
            return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="fr-FR" voice="Polly.Lea-Neural">{response_text}</Say>
  <Say language="fr-FR" voice="Polly.Lea-Neural">Je vous transfère vers un conseiller.</Say>
  <Dial>{transfer_num}</Dial>
</Response>"""

        response_text = await self.generate_voice_response(transcription, intent)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="fr-FR" voice="Polly.Lea-Neural">{response_text}</Say>
  <Say language="fr-FR" voice="Polly.Lea-Neural">Merci d'avoir contacté {self.domain.name}. Au revoir.</Say>
</Response>"""

    async def process_call(self, caller: str, speech: str = "", digits: str = "") -> dict:
        """Log et traitement complet d'un appel entrant."""
        intent  = self.detect_intent(speech or digits or "")
        twiml   = await self.twiml_handle(speech, digits)
        result  = {
            "caller":       caller,
            "intent":       intent,
            "transcription":speech or digits,
            "twiml":        twiml,
            "transferred":  intent in TRANSFER_NUMBERS and bool(TRANSFER_NUMBERS.get(intent)),
            "timestamp":    datetime.utcnow().isoformat(),
        }
        logger.info(f"[PHONE] Appel de {caller} · intent={intent} · transfert={result['transferred']}")
        return result
