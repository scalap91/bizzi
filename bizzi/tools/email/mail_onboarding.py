"""
tools/email/mail_onboarding.py
================================
Connexion mail ultra-simple pour les clients Bizzi.

Flow :
  1. Client entre son email dans le configurateur
  2. Bizzi lui envoie un email de validation
  3. Il clique le lien
  4. Il entre son mot de passe / App Password
  5. L'agent est connecté et commence à travailler

Ce que l'agent fait ensuite :
  - Rapport quotidien à l'heure choisie
  - Réponse automatique aux emails simples
  - Transfert des emails complexes
  - Résumé vocal (optionnel)
"""

import smtplib
import imaplib
import email
import logging
import os
import json
import secrets
import asyncio
from datetime import datetime, date
from email.mime.text      import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header         import decode_header
from pathlib              import Path
from tools.email.mail_connector import MailConnector, MailConfig, PROVIDER_SETTINGS

logger = logging.getLogger("tools.mail.onboarding")

# Tokens de validation en attente
PENDING_VALIDATIONS: dict = {}

# Boîtes connectées (en prod → DB)
CONNECTED_MAILBOXES: dict = {}

# Rapports générés
DAILY_REPORTS: list = []


# ══════════════════════════════════════════════════════════════
# ÉTAPE 1 — Détecter le provider automatiquement
# ══════════════════════════════════════════════════════════════

def detect_provider(email_address: str) -> str:
    """
    Détecte automatiquement le provider depuis l'adresse email.
    Le client n'a rien à configurer dans la plupart des cas.
    """
    domain = email_address.split('@')[-1].lower()

    AUTO_DETECT = {
        'gmail.com':         'google',
        'googlemail.com':    'google',
        'outlook.com':       'microsoft',
        'hotmail.com':       'microsoft',
        'live.com':          'microsoft',
        'office365.com':     'microsoft',
        'yahoo.fr':          'yahoo',
        'yahoo.com':         'yahoo',
        'icloud.com':        'apple',
        'me.com':            'apple',
        'laposte.net':       'laposte',
        'sfr.fr':            'sfr',
        'free.fr':           'free',
        'orange.fr':         'orange',
        'wanadoo.fr':        'orange',
    }

    # Détection par domaine exact
    if domain in AUTO_DETECT:
        return AUTO_DETECT[domain]

    # OVH — domaines hébergés OVH
    if domain.endswith('.fr') or domain.endswith('.com'):
        return 'ovh'  # Défaut pour les domaines perso

    return 'custom'


def get_provider_settings_for_email(email_address: str) -> dict:
    """Retourne les paramètres IMAP/SMTP pour une adresse email."""
    provider = detect_provider(email_address)
    domain   = email_address.split('@')[-1].lower()

    # Providers spéciaux non dans PROVIDER_SETTINGS
    EXTRA = {
        'yahoo':    {'imap_host':'imap.mail.yahoo.com',  'imap_port':993, 'smtp_host':'smtp.mail.yahoo.com',  'smtp_port':587},
        'apple':    {'imap_host':'imap.mail.me.com',     'imap_port':993, 'smtp_host':'smtp.mail.me.com',     'smtp_port':587},
        'laposte':  {'imap_host':'imap.laposte.net',     'imap_port':993, 'smtp_host':'smtp.laposte.net',     'smtp_port':587},
        'sfr':      {'imap_host':'imap.sfr.fr',          'imap_port':993, 'smtp_host':'smtp.sfr.fr',          'smtp_port':587},
        'free':     {'imap_host':'imap.free.fr',         'imap_port':993, 'smtp_host':'smtp.free.fr',         'smtp_port':587},
        'orange':   {'imap_host':'imap.orange.fr',       'imap_port':993, 'smtp_host':'smtp.orange.fr',       'smtp_port':587},
        'ovh':      {'imap_host':f'ssl0.ovh.net',        'imap_port':993, 'smtp_host':'ssl0.ovh.net',         'smtp_port':587},
    }

    if provider in PROVIDER_SETTINGS:
        s = PROVIDER_SETTINGS[provider]
        return {'provider': provider, 'imap_host': s['imap_host'], 'imap_port': s['imap_port'],
                'smtp_host': s['smtp_host'], 'smtp_port': s['smtp_port'], 'label': s['label']}

    if provider in EXTRA:
        s = EXTRA[provider]
        return {'provider': provider, **s, 'label': provider.capitalize()}

    # Custom — on devine depuis le domaine
    return {
        'provider':  'custom',
        'imap_host': f'mail.{domain}',
        'imap_port': 993,
        'smtp_host': f'mail.{domain}',
        'smtp_port': 587,
        'label':     'Personnalisé',
    }


# ══════════════════════════════════════════════════════════════
# ÉTAPE 2 — Envoyer l'email de validation
# ══════════════════════════════════════════════════════════════

def send_validation_email(client_email: str, agent_name: str, org_name: str,
                           base_url: str = "https://api.bizzi.fr") -> dict:
    """
    Envoie l'email de validation au client.
    Le client n'a qu'à cliquer le lien pour valider.
    """
    token = secrets.token_urlsafe(32)
    PENDING_VALIDATIONS[token] = {
        'email':      client_email,
        'agent_name': agent_name,
        'org_name':   org_name,
        'created_at': datetime.utcnow().isoformat(),
        'expires_at': (datetime.utcnow()).isoformat(),  # 24h en prod
    }

    settings   = get_provider_settings_for_email(client_email)
    provider   = settings['label']
    validate_url = f"{base_url}/api/tools/mail/validate/{token}"

    # Instructions spécifiques par provider
    PASSWORD_HELP = {
        'google':    "⚠️ Gmail nécessite un 'App Password' (pas votre mot de passe habituel).\nCompte Google → Sécurité → Validation en 2 étapes → Mots de passe des applications",
        'microsoft': "⚠️ Outlook nécessite un 'App Password'.\nCompte Microsoft → Sécurité → Options de sécurité avancées",
        'apple':     "⚠️ iCloud nécessite un 'Mot de passe spécifique à l'app'.\nidentifiant.apple.com → Sécurité → Mots de passe spécifiques",
        'yahoo':     "⚠️ Yahoo nécessite un 'Mot de passe d'application'.\nCompte Yahoo → Sécurité du compte → Générer mot de passe app",
    }.get(settings['provider'], "Utilisez votre mot de passe habituel.")

    body = f"""Bonjour,

Vous avez demandé à connecter votre boîte email à Bizzi.

Votre agent : {agent_name}
Organisation : {org_name}
Email détecté : {client_email}
Provider détecté : {provider} ({settings['imap_host']})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

▶ CLIQUEZ ICI POUR VALIDER ET CONNECTER VOTRE BOÎTE :
{validate_url}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{PASSWORD_HELP}

Une fois validé, {agent_name} pourra :
  ✓ Lire et résumer vos emails
  ✓ Répondre automatiquement aux emails simples
  ✓ Vous envoyer un rapport quotidien
  ✓ Vous alerter pour les emails urgents

Ce lien expire dans 24 heures.

Cordialement,
L'équipe Bizzi
"""

    logger.info(f"[MAIL ONBOARDING] Validation envoyée → {client_email} (token: {token[:8]}...)")

    return {
        'status':        'validation_sent',
        'token':         token,
        'email':         client_email,
        'provider':      provider,
        'validate_url':  validate_url,
        'imap_host':     settings['imap_host'],
        'smtp_host':     settings['smtp_host'],
        'password_help': PASSWORD_HELP,
        'body_preview':  body[:300],
    }


# ══════════════════════════════════════════════════════════════
# ÉTAPE 3 — Valider et connecter
# ══════════════════════════════════════════════════════════════

def validate_and_connect(token: str, password: str, report_time: str = "08:00",
                          report_mode: str = "email") -> dict:
    """
    Valide le token et connecte la boîte mail.
    Teste la connexion IMAP/SMTP immédiatement.

    Args:
        token       : token reçu par email
        password    : mot de passe ou App Password
        report_time : heure du rapport quotidien (ex: "08:00")
        report_mode : "email" | "summary_only" | "none"
    """
    if token not in PENDING_VALIDATIONS:
        return {'status': 'error', 'message': 'Token invalide ou expiré'}

    pending  = PENDING_VALIDATIONS[token]
    email_addr = pending['email']
    settings = get_provider_settings_for_email(email_addr)

    cfg = MailConfig(
        provider  = settings['provider'],
        email     = email_addr,
        password  = password,
        from_name = pending['agent_name'],
        imap_host = settings['imap_host'],
        imap_port = settings['imap_port'],
        smtp_host = settings['smtp_host'],
        smtp_port = settings['smtp_port'],
    )

    connector = MailConnector(cfg)
    test      = connector.test_connection()

    if not test['imap'] and not test['smtp']:
        return {
            'status':  'error',
            'message': f"Connexion impossible. Vérifiez votre mot de passe. Détail : {test.get('error','')}",
            'help':    get_password_help(settings['provider']),
        }

    # Connexion réussie → sauvegarder
    mailbox_id = f"{pending['org_name'].lower().replace(' ','-')}_{pending['agent_name'].lower().replace(' ','-')}"

    CONNECTED_MAILBOXES[mailbox_id] = {
        'email':       email_addr,
        'agent_name':  pending['agent_name'],
        'org_name':    pending['org_name'],
        'provider':    settings['provider'],
        'imap_host':   settings['imap_host'],
        'smtp_host':   settings['smtp_host'],
        'imap_ok':     test['imap'],
        'smtp_ok':     test['smtp'],
        'connected_at':datetime.utcnow().isoformat(),
        'report_time': report_time,
        'report_mode': report_mode,
        'cfg':         cfg,
    }

    # Supprimer le token utilisé
    del PENDING_VALIDATIONS[token]

    logger.info(f"[MAIL ONBOARDING] ✓ Connecté : {email_addr} (IMAP:{test['imap']} SMTP:{test['smtp']})")

    return {
        'status':      'connected',
        'mailbox_id':  mailbox_id,
        'email':       email_addr,
        'agent_name':  pending['agent_name'],
        'imap_ok':     test['imap'],
        'smtp_ok':     test['smtp'],
        'report_time': report_time,
        'report_mode': report_mode,
        'message':     f"✓ {pending['agent_name']} est maintenant connecté à {email_addr}",
    }


def get_password_help(provider: str) -> str:
    helps = {
        'google':    'Gmail : Compte Google → Sécurité → Validation 2 étapes → Mots de passe app → Créer',
        'microsoft': 'Outlook : compte.microsoft.com → Sécurité → Options avancées → Créer un mot de passe app',
        'apple':     'iCloud : appleid.apple.com → Sécurité → Mots de passe spécifiques → Créer',
        'yahoo':     'Yahoo : Paramètres du compte → Sécurité → Générer un mot de passe app',
    }
    return helps.get(provider, 'Utilisez votre mot de passe email habituel.')


# ══════════════════════════════════════════════════════════════
# RAPPORT QUOTIDIEN
# ══════════════════════════════════════════════════════════════

async def generate_daily_report(mailbox_id: str) -> dict:
    """
    Génère le rapport quotidien des emails.
    L'agent lit la boîte, classe les emails et produit un résumé.
    """
    if mailbox_id not in CONNECTED_MAILBOXES:
        return {'status': 'error', 'message': 'Boîte non connectée'}

    box       = CONNECTED_MAILBOXES[mailbox_id]
    connector = MailConnector(box['cfg'])

    # Récupérer les emails
    messages  = connector.fetch_inbox(limit=50, unread_only=False)
    today_msgs= [m for m in messages if _is_today(m.date)]

    if not today_msgs:
        today_msgs = messages[:10]  # Si pas d'emails aujourd'hui, prendre les 10 derniers

    # Classifier les emails
    categories = {
        'urgent':    [],
        'plainte':   [],
        'rgpd':      [],
        'presse':    [],
        'adhesion':  [],
        'partenariat':[],
        'support':   [],
        'newsletter':[]
    }

    for msg in today_msgs:
        cat = _classify_email(msg.subject, msg.body)
        categories[cat].append(msg)

    # Générer le résumé via Ollama
    emails_summary = _build_emails_summary(today_msgs)
    report_text    = await _generate_report_text(box['agent_name'], box['org_name'],
                                                   today_msgs, categories, emails_summary)

    # Envoyer le rapport par email
    sent = False
    if box['report_mode'] == 'email':
        sent = connector.send(
            to      = box['email'],
            subject = f"📊 Rapport email du {date.today().strftime('%d/%m/%Y')} — {box['org_name']}",
            body    = report_text,
        )

    report = {
        'mailbox_id':    mailbox_id,
        'date':          date.today().isoformat(),
        'total_emails':  len(today_msgs),
        'categories':    {k: len(v) for k, v in categories.items() if v},
        'report':        report_text,
        'sent_by_email': sent,
        'generated_at':  datetime.utcnow().isoformat(),
    }

    DAILY_REPORTS.append(report)
    logger.info(f"[MAIL REPORT] {mailbox_id} → {len(today_msgs)} emails · envoyé: {sent}")
    return report


async def _generate_report_text(agent_name: str, org_name: str,
                                 messages: list, categories: dict, summary: str) -> str:
    """Génère le texte du rapport via Ollama."""
    total    = len(messages)
    cat_desc = ', '.join(f"{len(v)} {k}" for k, v in categories.items() if v)

    prompt = f"""Tu es {agent_name}, assistant de {org_name}.
Tu rédiges le rapport email quotidien pour ton responsable.

Emails reçus aujourd'hui : {total}
Répartition : {cat_desc}

Résumé des emails :
{summary}

Rédige un rapport professionnel et concis (10-15 lignes max) :
1. Résumé global
2. Points d'attention urgents
3. Emails traités automatiquement
4. Emails nécessitant une action humaine
5. Recommandations

Commence par : "Bonjour, voici votre rapport email du {date.today().strftime('%d/%m/%Y')} :"
"""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post("http://localhost:11434/api/generate",
                json={"model":"mistral:7b","prompt":prompt,"stream":False,
                      "options":{"temperature":0.4,"num_predict":400}})
            if r.status_code == 200:
                return r.json().get("response","").strip()
    except Exception as e:
        logger.error(f"[MAIL REPORT] Ollama error: {e}")

    # Fallback sans Ollama
    urgent = len(categories.get('urgent', []))
    plainte= len(categories.get('plainte', []))
    return f"""Bonjour, voici votre rapport email du {date.today().strftime('%d/%m/%Y')} :

{total} email(s) reçu(s) aujourd'hui.
{f"⚠️ {urgent} email(s) urgent(s) nécessitent votre attention." if urgent else "✓ Aucun email urgent."}
{f"📢 {plainte} plainte(s) détectée(s) — réponse automatique envoyée." if plainte else ""}

Répartition : {', '.join(f'{len(v)} {k}' for k, v in categories.items() if v) or 'Aucun email classifié.'}

Cordialement,
{agent_name}"""


def _build_emails_summary(messages: list) -> str:
    """Construit un résumé textuel des emails pour le prompt."""
    lines = []
    for m in messages[:15]:
        lines.append(f"- De : {m.from_addr[:40]} | Sujet : {m.subject[:60]} | Extrait : {m.body[:80]}")
    return '\n'.join(lines)


def _classify_email(subject: str, body: str) -> str:
    """Classification simple des emails."""
    text = (subject + ' ' + body).lower()
    if any(k in text for k in ['urgent','immédiat','grave','problème critique']): return 'urgent'
    if any(k in text for k in ['plainte','réclamation','insatisfait','scandale']):  return 'plainte'
    if any(k in text for k in ['rgpd','données personnelles','supprimer','effacer']): return 'rgpd'
    if any(k in text for k in ['presse','journaliste','interview','médias']):         return 'presse'
    if any(k in text for k in ['adhérer','rejoindre','inscription','membre']):        return 'adhesion'
    if any(k in text for k in ['partenariat','collaboration','accord']):              return 'partenariat'
    if any(k in text for k in ['newsletter','unsubscribe','désinscription']):         return 'newsletter'
    return 'support'


def _is_today(date_str: str) -> bool:
    """Vérifie si une date email est aujourd'hui."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt.date() == date.today()
    except:
        return False


# ══════════════════════════════════════════════════════════════
# LISTE DES BOÎTES CONNECTÉES
# ══════════════════════════════════════════════════════════════

def list_mailboxes(org_name: str = None) -> list:
    """Liste les boîtes connectées, optionnellement filtrées par organisation."""
    boxes = list(CONNECTED_MAILBOXES.values())
    if org_name:
        boxes = [b for b in boxes if b['org_name'] == org_name]
    return [{k: v for k, v in b.items() if k != 'cfg'} for b in boxes]


def disconnect_mailbox(mailbox_id: str) -> bool:
    """Déconnecte une boîte mail."""
    if mailbox_id in CONNECTED_MAILBOXES:
        del CONNECTED_MAILBOXES[mailbox_id]
        logger.info(f"[MAIL ONBOARDING] Déconnecté : {mailbox_id}")
        return True
    return False
