"""tools/rgpd/rgpd_agent.py — Gestion des droits RGPD (UE 2016/679)"""
import logging, os
from datetime import datetime, timedelta
from uuid import uuid4
from config.domain_loader import DomainConfig

logger = logging.getLogger("tools.rgpd")

# Droits reconnus par le RGPD (Art. 15-21)
RIGHTS = {
    "access":      {"article": "Art.15", "label": "Droit d'accès",          "delay_days": 30},
    "delete":      {"article": "Art.17", "label": "Droit à l'effacement",    "delay_days": 30},
    "rectify":     {"article": "Art.16", "label": "Droit de rectification",  "delay_days": 30},
    "portability": {"article": "Art.20", "label": "Droit à la portabilité",  "delay_days": 30},
    "oppose":      {"article": "Art.21", "label": "Droit d'opposition",      "delay_days": 30},
    "restrict":    {"article": "Art.18", "label": "Droit de limitation",     "delay_days": 30},
}

REQUESTS_DB: dict = {}


class RGPDAgent:
    """
    Agent RGPD — Gère les droits des personnes conformément au RGPD (UE) 2016/679.
    Délai légal de réponse : 30 jours (extensible à 3 mois si complexité).
    """

    def __init__(self, domain: DomainConfig):
        self.domain      = domain
        self.dpo_email   = os.getenv("DPO_EMAIL", os.getenv("ADMIN_EMAIL", "dpo@org.fr"))
        self.org_email   = os.getenv("FROM_EMAIL", f"rgpd@{domain.name.lower().replace(' ','')}.fr")

    def _detect_right(self, content: str) -> str:
        """Détecte le type de droit demandé."""
        text = content.lower()
        if any(k in text for k in ["supprimer","effacer","droit à l'oubli","suppression"]):
            return "delete"
        if any(k in text for k in ["rectifier","corriger","modifier","rectification"]):
            return "rectify"
        if any(k in text for k in ["portabilité","exporter","télécharger mes données"]):
            return "portability"
        if any(k in text for k in ["opposer","opposition","refuser le traitement"]):
            return "oppose"
        if any(k in text for k in ["limiter","restreindre","limitation"]):
            return "restrict"
        return "access"  # Défaut : droit d'accès

    def create_request(self, name: str, email: str, right: str, content: str) -> dict:
        """Enregistre une demande RGPD."""
        req_id    = f"RGPD-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:6].upper()}"
        deadline  = datetime.utcnow() + timedelta(days=RIGHTS[right]["delay_days"])
        right_cfg = RIGHTS[right]

        request = {
            "id":          req_id,
            "name":        name,
            "email":       email,
            "right":       right,
            "right_label": right_cfg["label"],
            "article":     right_cfg["article"],
            "content":     content,
            "status":      "pending",
            "domain":      self.domain.domain,
            "org":         self.domain.name,
            "created_at":  datetime.utcnow().isoformat(),
            "deadline":    deadline.isoformat(),
            "deadline_str":deadline.strftime("%d/%m/%Y"),
            "dpo_notified":False,
            "notes":       [],
        }

        REQUESTS_DB[req_id] = request
        logger.info(f"[RGPD] Demande créée : {req_id} · droit={right} · délai={deadline.strftime('%d/%m/%Y')}")
        return request

    def generate_acknowledgment(self, request: dict) -> str:
        """Génère la lettre d'accusé de réception conforme RGPD."""
        right_cfg = RIGHTS[request["right"]]
        return f"""Madame, Monsieur {request['name']},

Nous accusons réception de votre demande d'exercice de votre {right_cfg['label']} 
({right_cfg['article']} du Règlement Général sur la Protection des Données — RGPD UE 2016/679) 
enregistrée ce jour sous la référence : {request['id']}.

Votre demande :
{request['content'][:300]}

Nous vous informons que :

1. Votre demande a été enregistrée et transmise à notre Délégué à la Protection des Données (DPO).

2. Nous avons 30 jours à compter de ce jour pour y répondre, soit au plus tard le {request['deadline_str']}.
   Ce délai peut être prolongé de deux mois supplémentaires si la complexité ou le nombre de 
   demandes le justifie, auquel cas nous vous en informerons dans le délai initial.

3. En cas de non-réponse dans ce délai, vous avez la possibilité de saisir la CNIL :
   Commission Nationale de l'Informatique et des Libertés
   3 Place de Fontenoy, TSA 80715, 75334 PARIS CEDEX 07
   www.cnil.fr — Tél. : 01 53 73 22 22

Référence de votre demande : {request['id']}
Organisation : {request['org']}
Date : {datetime.utcnow().strftime('%d/%m/%Y')}

Cordialement,
Le Délégué à la Protection des Données
{request['org']}
{self.dpo_email}"""

    def generate_compliance_letter(self, request: dict, data_summary: str = "") -> str:
        """Génère la lettre de conformité (réponse officielle)."""
        right = request["right"]
        right_cfg = RIGHTS[right]

        actions = {
            "access":      f"Vous trouverez en pièce jointe l'ensemble des données personnelles vous concernant que nous détenons.",
            "delete":      f"Vos données personnelles ont été supprimées de l'ensemble de nos systèmes.",
            "rectify":     f"Vos données personnelles ont été rectifiées conformément à votre demande.",
            "portability": f"Vous trouverez en pièce jointe vos données au format JSON, conformément à votre droit à la portabilité.",
            "oppose":      f"Votre opposition au traitement de vos données personnelles a été prise en compte.",
            "restrict":    f"Le traitement de vos données personnelles a été limité conformément à votre demande.",
        }

        return f"""Madame, Monsieur {request['name']},

Suite à votre demande du {request['created_at'][:10]} (référence : {request['id']}),
relative à l'exercice de votre {right_cfg['label']} ({right_cfg['article']} RGPD),

Nous avons procédé au traitement de votre demande :

{actions.get(right, "Votre demande a été traitée.")}

{data_summary if data_summary else ""}

Cette action a été réalisée dans le respect du Règlement Général sur la Protection des Données 
(Règlement UE 2016/679) et de la loi Informatique et Libertés modifiée.

Si vous estimez que le traitement de vos données personnelles n'est pas conforme au RGPD, 
vous disposez du droit d'introduire une réclamation auprès de la CNIL (www.cnil.fr).

Cordialement,
Le Délégué à la Protection des Données
{request['org']}
{self.dpo_email}
Date : {datetime.utcnow().strftime('%d/%m/%Y')}"""

    def notify_dpo(self, request: dict) -> bool:
        """Notifie le DPO de la nouvelle demande."""
        logger.info(f"[RGPD] Notification DPO → {self.dpo_email} · {request['id']}")
        REQUESTS_DB[request["id"]]["dpo_notified"] = True
        # En prod : send_email(dpo_email, subject, body)
        return True

    async def process(self, name: str, email: str, content: str) -> dict:
        """Traite une demande RGPD de bout en bout."""
        right   = self._detect_right(content)
        request = self.create_request(name, email, right, content)
        ack     = self.generate_acknowledgment(request)
        self.notify_dpo(request)

        return {
            "request_id":        request["id"],
            "right":             right,
            "right_label":       RIGHTS[right]["label"],
            "article":           RIGHTS[right]["article"],
            "deadline":          request["deadline_str"],
            "acknowledgment":    ack,
            "dpo_notified":      True,
            "cnil_recourse":     "www.cnil.fr — 01 53 73 22 22",
            "processed_at":      datetime.utcnow().isoformat(),
        }

    def get_request(self, req_id: str) -> dict | None:
        return REQUESTS_DB.get(req_id)

    def complete_request(self, req_id: str, data_summary: str = "") -> dict | None:
        if req_id not in REQUESTS_DB:
            return None
        req = REQUESTS_DB[req_id]
        req["status"]       = "completed"
        req["completed_at"] = datetime.utcnow().isoformat()
        req["compliance_letter"] = self.generate_compliance_letter(req, data_summary)
        return req

    def list_requests(self, status: str = None) -> list:
        requests = list(REQUESTS_DB.values())
        if status:
            requests = [r for r in requests if r["status"] == status]
        return sorted(requests, key=lambda r: r["created_at"], reverse=True)

    def overdue_requests(self) -> list:
        """Retourne les demandes en retard (délai dépassé)."""
        now = datetime.utcnow()
        return [
            r for r in REQUESTS_DB.values()
            if r["status"] == "pending" and datetime.fromisoformat(r["deadline"]) < now
        ]
