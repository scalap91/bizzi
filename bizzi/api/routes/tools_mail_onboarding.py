"""api/routes/tools_mail_onboarding.py — Onboarding mail simplifié"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from tools.email.mail_onboarding import (
    detect_provider, get_provider_settings_for_email,
    send_validation_email, validate_and_connect,
    generate_daily_report, list_mailboxes, disconnect_mailbox,
    PENDING_VALIDATIONS, CONNECTED_MAILBOXES, DAILY_REPORTS,
)

router = APIRouter()


class StartOnboarding(BaseModel):
    email:      str
    agent_name: str
    org_name:   str
    base_url:   Optional[str] = "https://api.bizzi.fr"


class ValidateConnection(BaseModel):
    token:       str
    password:    str
    report_time: Optional[str] = "08:00"
    report_mode: Optional[str] = "email"  # email | summary_only | none


# ── Étape 1 — Détecter le provider ───────────────────────────

@router.get("/detect")
async def detect_mail_provider(email: str):
    """Détecte automatiquement le provider depuis l'adresse email."""
    settings = get_provider_settings_for_email(email)
    return {
        "email":     email,
        "provider":  settings["provider"],
        "label":     settings["label"],
        "imap_host": settings["imap_host"],
        "imap_port": settings["imap_port"],
        "smtp_host": settings["smtp_host"],
        "smtp_port": settings["smtp_port"],
        "needs_app_password": settings["provider"] in ["google","microsoft","apple","yahoo"],
    }


# ── Étape 2 — Envoyer l'email de validation ──────────────────

@router.post("/start")
async def start_mail_onboarding(data: StartOnboarding):
    """
    Lance l'onboarding mail.
    Envoie un email de validation au client.
    """
    result = send_validation_email(
        client_email = data.email,
        agent_name   = data.agent_name,
        org_name     = data.org_name,
        base_url     = data.base_url,
    )
    return result


# ── Étape 3 — Page de validation (lien dans l'email) ─────────

@router.get("/validate/{token}", response_class=HTMLResponse)
async def validation_page(token: str):
    """Page HTML de validation — le client arrive ici en cliquant le lien."""
    if token not in PENDING_VALIDATIONS:
        return HTMLResponse(content=_error_page("Lien invalide ou expiré."), status_code=400)

    pending  = PENDING_VALIDATIONS[token]
    settings = get_provider_settings_for_email(pending['email'])
    needs_app = settings["provider"] in ["google","microsoft","apple","yahoo"]

    app_password_guide = {
        "google":    ("App Password Gmail",    "Compte Google → Sécurité → Validation en 2 étapes → Mots de passe des applications → Créer"),
        "microsoft": ("App Password Microsoft","compte.microsoft.com → Sécurité → Options avancées → Créer mot de passe app"),
        "apple":     ("Mot de passe spécifique","appleid.apple.com → Sécurité → Mots de passe spécifiques → Créer"),
        "yahoo":     ("Mot de passe app Yahoo", "Paramètres Yahoo → Sécurité → Générer mot de passe app"),
    }.get(settings["provider"], ("Mot de passe email", "Utilisez votre mot de passe habituel."))

    return HTMLResponse(content=_validation_page_html(
        token       = token,
        email       = pending["email"],
        agent_name  = pending["agent_name"],
        org_name    = pending["org_name"],
        provider    = settings["label"],
        imap_host   = settings["imap_host"],
        needs_app   = needs_app,
        guide_title = app_password_guide[0],
        guide_text  = app_password_guide[1],
    ))


# ── Étape 4 — Soumettre le mot de passe et connecter ─────────

@router.post("/connect")
async def connect_mailbox(data: ValidateConnection):
    """Valide le token et connecte la boîte mail."""
    result = validate_and_connect(
        token       = data.token,
        password    = data.password,
        report_time = data.report_time,
        report_mode = data.report_mode,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ── Rapport quotidien ─────────────────────────────────────────

@router.post("/report/{mailbox_id}")
async def trigger_daily_report(mailbox_id: str):
    """Génère et envoie le rapport quotidien."""
    result = await generate_daily_report(mailbox_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/reports/{mailbox_id}")
async def get_reports(mailbox_id: str, limit: int = 10):
    """Historique des rapports d'une boîte."""
    reports = [r for r in DAILY_REPORTS if r["mailbox_id"] == mailbox_id]
    return {"mailbox_id": mailbox_id, "reports": reports[-limit:], "total": len(reports)}


# ── Gestion des boîtes ────────────────────────────────────────

@router.get("/mailboxes")
async def get_mailboxes(org: Optional[str] = None):
    """Liste les boîtes connectées."""
    return {"mailboxes": list_mailboxes(org), "count": len(list_mailboxes(org))}

@router.delete("/mailboxes/{mailbox_id}")
async def remove_mailbox(mailbox_id: str):
    """Déconnecte une boîte mail."""
    ok = disconnect_mailbox(mailbox_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Boîte introuvable")
    return {"status": "disconnected", "mailbox_id": mailbox_id}


# ══════════════════════════════════════════════════════════════
# HTML — Page de validation (design Bizzi)
# ══════════════════════════════════════════════════════════════

def _validation_page_html(token, email, agent_name, org_name,
                           provider, imap_host, needs_app,
                           guide_title, guide_text) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Connecter ma boîte mail — Bizzi</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800;900&family=Outfit:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{--bg:#04050c;--s1:#08090f;--card:#0e1020;--b1:#131625;--b2:#1c2035;--w:#eef0f8;--mid:#4a5070;--accent:#e02d2d;--grn:#00c896;--amb:#f59e0b;--D:'Syne',sans-serif;--B:'Outfit',sans-serif}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--B);background:var(--bg);color:var(--w);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;-webkit-font-smoothing:antialiased}}
.card{{background:var(--card);border:1px solid var(--b1);border-radius:16px;padding:40px;max-width:480px;width:100%}}
.logo{{font-family:var(--D);font-size:.9rem;font-weight:900;color:var(--accent);margin-bottom:28px;display:block}}
.logo::before{{content:'⬡ '}}
h1{{font-family:var(--D);font-size:1.5rem;font-weight:800;letter-spacing:-.03em;margin-bottom:8px}}
h1 span{{color:var(--accent)}}
.sub{{font-size:.82rem;color:var(--mid);line-height:1.65;margin-bottom:28px}}
.info-row{{display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--s1);border:1px solid var(--b1);border-radius:8px;margin-bottom:8px}}
.info-ico{{font-size:1rem;flex-shrink:0}}
.info-txt{{font-size:.75rem;color:var(--mid)}}
.info-val{{font-size:.78rem;font-weight:600;margin-left:auto}}
.guide{{padding:12px 14px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:8px;margin:16px 0}}
.guide-title{{font-size:.68rem;font-weight:700;color:var(--amb);margin-bottom:5px}}
.guide-text{{font-size:.7rem;color:rgba(255,255,255,.6);line-height:1.65}}
.field{{margin-bottom:16px}}
.field label{{display:block;font-size:.6rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--mid);margin-bottom:6px}}
.field input,.field select{{width:100%;background:var(--s1);border:1px solid var(--b2);border-radius:8px;padding:11px 14px;color:var(--w);font-size:.82rem;font-family:var(--B);outline:none;transition:border-color .15s}}
.field input:focus,.field select:focus{{border-color:var(--accent)}}
.hint{{font-size:.63rem;color:var(--mid);margin-top:5px}}
.btn{{width:100%;font-family:var(--D);font-size:.8rem;font-weight:700;padding:13px;border-radius:9px;background:var(--accent);color:#fff;border:none;cursor:pointer;transition:all .2s;margin-top:8px}}
.btn:hover{{background:#c42424}}
.btn:disabled{{opacity:.5;cursor:not-allowed}}
.result{{display:none;padding:14px;border-radius:8px;font-size:.78rem;margin-top:14px;line-height:1.6}}
.result.ok{{background:rgba(0,200,150,.08);border:1px solid rgba(0,200,150,.2);color:var(--grn)}}
.result.err{{background:rgba(224,45,45,.08);border:1px solid rgba(224,45,45,.2);color:var(--accent)}}
.divider{{height:1px;background:var(--b1);margin:20px 0}}
</style>
</head>
<body>
<div class="card">
  <span class="logo">Bizzi</span>
  <h1>Connecter <span>votre boîte</span></h1>
  <p class="sub">
    Entrez votre mot de passe pour que <strong>{agent_name}</strong> puisse
    lire et gérer les emails de <strong>{org_name}</strong>.
  </p>

  <div class="info-row"><span class="info-ico">📧</span><span class="info-txt">Adresse email</span><span class="info-val">{email}</span></div>
  <div class="info-row"><span class="info-ico">🏢</span><span class="info-txt">Provider détecté</span><span class="info-val">{provider}</span></div>
  <div class="info-row"><span class="info-ico">🔌</span><span class="info-txt">Serveur IMAP</span><span class="info-val">{imap_host}</span></div>

  {'<div class="guide"><div class="guide-title">⚠️ ' + guide_title + ' requis</div><div class="guide-text">' + guide_text + '</div></div>' if needs_app else ''}

  <div class="divider"></div>

  <form onsubmit="connect(event)">
    <div class="field">
      <label>{"App Password" if needs_app else "Mot de passe email"}</label>
      <input type="password" id="password" placeholder="{'App Password...' if needs_app else 'Votre mot de passe email...'}" required>
      <div class="hint">{'Ce mot de passe spécial est différent de votre mot de passe habituel.' if needs_app else 'Votre mot de passe email habituel.'}</div>
    </div>

    <div class="field">
      <label>Heure du rapport quotidien</label>
      <select id="report-time">
        <option value="07:00">7h00</option>
        <option value="08:00" selected>8h00</option>
        <option value="09:00">9h00</option>
        <option value="12:00">12h00</option>
        <option value="18:00">18h00</option>
        <option value="none">Pas de rapport automatique</option>
      </select>
      <div class="hint">Chaque jour à cette heure, {agent_name} vous enverra un résumé de vos emails.</div>
    </div>

    <div class="field">
      <label>Mode du rapport</label>
      <select id="report-mode">
        <option value="email" selected>📧 Email complet avec résumé IA</option>
        <option value="summary_only">📋 Résumé court uniquement</option>
        <option value="none">🔕 Aucun rapport — agent silencieux</option>
      </select>
    </div>

    <button class="btn" type="submit" id="submit-btn">🔌 Connecter ma boîte mail</button>
  </form>

  <div class="result" id="result"></div>
</div>

<script>
async function connect(e) {{
  e.preventDefault();
  const btn = document.getElementById('submit-btn');
  const res = document.getElementById('result');
  const pwd = document.getElementById('password').value;
  const rt  = document.getElementById('report-time').value;
  const rm  = document.getElementById('report-mode').value;

  btn.disabled = true;
  btn.textContent = '⟳ Connexion en cours...';
  res.style.display = 'none';

  try {{
    const r = await fetch('/api/tools/mail/onboarding/connect', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        token: '{token}',
        password: pwd,
        report_time: rt,
        report_mode: rm,
      }})
    }});
    const data = await r.json();

    if (r.ok) {{
      res.className = 'result ok';
      res.style.display = 'block';
      res.innerHTML = `✓ ${{data.message}}<br><br>
        IMAP (lecture) : ${{data.imap_ok ? '✓ Connecté' : '⚠ Limité'}}<br>
        SMTP (envoi) : ${{data.smtp_ok ? '✓ Connecté' : '⚠ Limité'}}<br>
        Rapport : ${{rt === 'none' ? 'Désactivé' : 'Chaque jour à ' + rt}}<br><br>
        Vous pouvez fermer cette page. {agent_name} commence à travailler.`;
      btn.textContent = '✓ Connecté !';
      btn.style.background = 'var(--grn)';
    }} else {{
      throw new Error(data.detail || 'Erreur de connexion');
    }}
  }} catch(err) {{
    res.className = 'result err';
    res.style.display = 'block';
    res.innerHTML = `✗ ${{err.message}}<br><br>Vérifiez votre mot de passe et réessayez.`;
    btn.disabled = false;
    btn.textContent = '🔌 Réessayer';
  }}
}}
</script>
</body>
</html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><title>Erreur — Bizzi</title>
<style>body{{font-family:sans-serif;background:#04050c;color:#eef0f8;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center}}
.card{{background:#0e1020;border:1px solid #131625;border-radius:16px;padding:40px;max-width:400px}}
h1{{color:#e02d2d;font-size:1.5rem;margin-bottom:12px}}p{{color:#4a5070;font-size:.85rem}}</style>
</head><body><div class="card"><h1>⚠ Lien invalide</h1><p>{message}</p>
<p style="margin-top:16px">Retournez dans le configurateur pour générer un nouveau lien.</p></div></body></html>"""
