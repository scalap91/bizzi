"""
tools/email/mail_connector.py
==============================
Connecteur mail universel configurable par provider.
Chaque organisation utilise son propre hébergeur.

Providers supportés :
  - ovh        → ssl0.ovh.net
  - brevo      → smtp.brevo.com
  - google     → smtp.gmail.com / Google Workspace
  - microsoft  → smtp.office365.com
  - custom     → paramètres manuels

Usage :
    connector = MailConnector.from_domain_config(domain_config, tenant_env)
    emails    = await connector.fetch_inbox("lucas.martin@lesdemocrates.org")
    await connector.send(to="...", subject="...", body="...")
"""

import imaplib
import smtplib
import email
import logging
import os
from email.mime.text    import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header       import decode_header
from dataclasses        import dataclass
from datetime           import datetime
from typing             import Optional

logger = logging.getLogger("tools.mail")

# ── Paramètres par provider ───────────────────────────────────
PROVIDER_SETTINGS = {
    "ovh": {
        "imap_host": "ssl0.ovh.net",
        "imap_port": 993,
        "smtp_host": "ssl0.ovh.net",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "OVH Mail",
    },
    "brevo": {
        "imap_host": "imap.brevo.com",
        "imap_port": 993,
        "smtp_host": "smtp.brevo.com",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "Brevo",
    },
    "google": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "Google / Gmail",
    },
    "microsoft": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "Microsoft 365 / Outlook",
    },
    "ionos": {
        "imap_host": "imap.ionos.fr",
        "imap_port": 993,
        "smtp_host": "smtp.ionos.fr",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "IONOS",
    },
    "infomaniak": {
        "imap_host": "mail.infomaniak.com",
        "imap_port": 993,
        "smtp_host": "mail.infomaniak.com",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "Infomaniak",
    },
    "custom": {
        "imap_host": "",
        "imap_port": 993,
        "smtp_host": "",
        "smtp_port": 587,
        "imap_ssl":  True,
        "smtp_tls":  True,
        "label":     "Personnalisé",
    },
}


@dataclass
class MailConfig:
    """Configuration mail d'un agent ou d'une organisation."""
    provider:  str
    email:     str
    password:  str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    imap_ssl:  bool = True
    smtp_tls:  bool = True
    from_name: str  = ""

    @classmethod
    def from_provider(cls, provider: str, email: str, password: str,
                      from_name: str = "", **overrides) -> "MailConfig":
        """Crée une config à partir du nom du provider."""
        settings = PROVIDER_SETTINGS.get(provider, PROVIDER_SETTINGS["custom"]).copy()
        settings.update(overrides)
        return cls(
            provider  = provider,
            email     = email,
            password  = password,
            from_name = from_name or email,
            imap_host = settings["imap_host"],
            imap_port = settings["imap_port"],
            smtp_host = settings["smtp_host"],
            smtp_port = settings["smtp_port"],
            imap_ssl  = settings["imap_ssl"],
            smtp_tls  = settings["smtp_tls"],
        )


@dataclass
class EmailMessage:
    """Email entrant parsé."""
    uid:         str
    from_addr:   str
    to_addr:     str
    subject:     str
    body:        str
    date:        str
    is_read:     bool = False
    attachments: list = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []


class MailConnector:
    """
    Connecteur IMAP/SMTP universel.
    S'adapte à n'importe quel provider.
    """

    def __init__(self, config: MailConfig):
        self.cfg = config

    # ── IMAP — Lecture ────────────────────────────────────────

    def fetch_inbox(self, limit: int = 20, unread_only: bool = False) -> list[EmailMessage]:
        """Récupère les emails de la boîte de réception."""
        messages = []
        try:
            if self.cfg.imap_ssl:
                imap = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port)
            else:
                imap = imaplib.IMAP4(self.cfg.imap_host, self.cfg.imap_port)

            imap.login(self.cfg.email, self.cfg.password)
            imap.select("INBOX")

            criteria = "UNSEEN" if unread_only else "ALL"
            _, data  = imap.search(None, criteria)
            uids     = data[0].split()[-limit:]  # Derniers N emails

            for uid in reversed(uids):
                try:
                    _, msg_data = imap.fetch(uid, "(RFC822)")
                    raw_email   = msg_data[0][1]
                    msg         = email.message_from_bytes(raw_email)

                    body = self._extract_body(msg)
                    messages.append(EmailMessage(
                        uid       = uid.decode(),
                        from_addr = self._decode_header(msg.get("From", "")),
                        to_addr   = self._decode_header(msg.get("To", "")),
                        subject   = self._decode_header(msg.get("Subject", "(Sans objet)")),
                        body      = body,
                        date      = msg.get("Date", ""),
                        is_read   = False,
                    ))
                except Exception as e:
                    logger.warning(f"[MAIL] Erreur parsing email {uid}: {e}")

            imap.logout()
            logger.info(f"[MAIL] {self.cfg.email} → {len(messages)} emails récupérés")

        except imaplib.IMAP4.error as e:
            logger.error(f"[MAIL] IMAP error {self.cfg.email}: {e}")
        except Exception as e:
            logger.error(f"[MAIL] Erreur connexion {self.cfg.email}: {e}")

        return messages

    def mark_as_read(self, uid: str):
        """Marque un email comme lu."""
        try:
            if self.cfg.imap_ssl:
                imap = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port)
            else:
                imap = imaplib.IMAP4(self.cfg.imap_host, self.cfg.imap_port)
            imap.login(self.cfg.email, self.cfg.password)
            imap.select("INBOX")
            imap.store(uid.encode(), "+FLAGS", "\\Seen")
            imap.logout()
        except Exception as e:
            logger.warning(f"[MAIL] mark_as_read error: {e}")

    # ── SMTP — Envoi ──────────────────────────────────────────

    def send(self, to: str, subject: str, body: str,
             cc: str = "", reply_to: str = "", html: bool = False) -> bool:
        """Envoie un email via SMTP."""
        try:
            msg = MIMEMultipart("alternative" if html else "mixed")
            msg["From"]    = f"{self.cfg.from_name} <{self.cfg.email}>"
            msg["To"]      = to
            msg["Subject"] = subject
            if cc:        msg["Cc"]       = cc
            if reply_to:  msg["Reply-To"] = reply_to

            if html:
                msg.attach(MIMEText(body, "html", "utf-8"))
            else:
                msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
                server.ehlo()
                if self.cfg.smtp_tls:
                    server.starttls()
                server.login(self.cfg.email, self.cfg.password)
                recipients = [to] + ([cc] if cc else [])
                server.sendmail(self.cfg.email, recipients, msg.as_string())

            logger.info(f"[MAIL] Envoyé : {self.cfg.email} → {to} | {subject[:50]}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error(f"[MAIL] Auth SMTP échouée pour {self.cfg.email}")
        except smtplib.SMTPException as e:
            logger.error(f"[MAIL] SMTP error: {e}")
        except Exception as e:
            logger.error(f"[MAIL] Erreur envoi: {e}")

        return False

    def test_connection(self) -> dict:
        """Teste les connexions IMAP et SMTP."""
        result = {
            "email":    self.cfg.email,
            "provider": self.cfg.provider,
            "imap":     False,
            "smtp":     False,
            "error":    None,
        }
        # Test IMAP
        try:
            if self.cfg.imap_ssl:
                imap = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port)
            else:
                imap = imaplib.IMAP4(self.cfg.imap_host, self.cfg.imap_port)
            imap.login(self.cfg.email, self.cfg.password)
            imap.logout()
            result["imap"] = True
        except Exception as e:
            result["error"] = f"IMAP: {e}"

        # Test SMTP
        try:
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
                server.ehlo()
                if self.cfg.smtp_tls:
                    server.starttls()
                server.login(self.cfg.email, self.cfg.password)
            result["smtp"] = True
        except Exception as e:
            result["error"] = (result["error"] or "") + f" SMTP: {e}"

        logger.info(f"[MAIL] Test {self.cfg.email} → IMAP:{result['imap']} SMTP:{result['smtp']}")
        return result

    # ── Utils ─────────────────────────────────────────────────

    @staticmethod
    def _decode_header(value: str) -> str:
        """Décode les headers email (UTF-8, Base64, etc.)."""
        if not value:
            return ""
        parts = decode_header(value)
        decoded = []
        for part, encoding in parts:
            if isinstance(part, bytes):
                try:
                    decoded.append(part.decode(encoding or "utf-8", errors="ignore"))
                except:
                    decoded.append(part.decode("utf-8", errors="ignore"))
            else:
                decoded.append(str(part))
        return " ".join(decoded)

    @staticmethod
    def _extract_body(msg) -> str:
        """Extrait le corps texte d'un email multipart."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body = part.get_payload(decode=True).decode(charset, errors="ignore")
                        break
                    except:
                        pass
        else:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body = msg.get_payload(decode=True).decode(charset, errors="ignore")
            except:
                body = str(msg.get_payload())
        return body.strip()

    # ── Factory depuis le domaine ──────────────────────────────

    @classmethod
    def from_env(cls, agent_slug: str, tenant_id: str = "") -> Optional["MailConnector"]:
        """
        Crée un connecteur depuis les variables d'environnement.

        Variables attendues (préfixées par le tenant si multi-tenant) :
            MAIL_PROVIDER    = ovh | brevo | google | microsoft | custom
            MAIL_USER        = email@domaine.fr
            MAIL_PASS        = mot_de_passe
            MAIL_FROM_NAME   = Nom Affiché
            # Si custom :
            MAIL_IMAP_HOST   = imap.mondomaine.fr
            MAIL_SMTP_HOST   = smtp.mondomaine.fr
        """
        prefix   = f"{tenant_id.upper()}_" if tenant_id else ""
        provider = os.getenv(f"{prefix}MAIL_PROVIDER",  os.getenv("MAIL_PROVIDER", ""))
        user     = os.getenv(f"{prefix}MAIL_USER_{agent_slug.upper().replace('-','_')}",
                             os.getenv(f"{prefix}MAIL_USER",
                             os.getenv("MAIL_USER", "")))
        password = os.getenv(f"{prefix}MAIL_PASS_{agent_slug.upper().replace('-','_')}",
                             os.getenv(f"{prefix}MAIL_PASS",
                             os.getenv("MAIL_PASS", "")))
        from_name = os.getenv(f"{prefix}MAIL_FROM_NAME", "")

        if not provider or not user or not password:
            logger.warning(f"[MAIL] Config manquante pour {agent_slug} (tenant={tenant_id})")
            return None

        overrides = {}
        if provider == "custom":
            overrides["imap_host"] = os.getenv(f"{prefix}MAIL_IMAP_HOST", "")
            overrides["smtp_host"] = os.getenv(f"{prefix}MAIL_SMTP_HOST", "")

        cfg = MailConfig.from_provider(provider, user, password, from_name, **overrides)
        return cls(cfg)


def list_providers() -> list[dict]:
    """Liste tous les providers disponibles."""
    return [
        {"id": k, "label": v["label"],
         "imap": f"{v['imap_host']}:{v['imap_port']}",
         "smtp": f"{v['smtp_host']}:{v['smtp_port']}"}
        for k, v in PROVIDER_SETTINGS.items()
    ]
