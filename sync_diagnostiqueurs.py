#!/usr/bin/env python3
import psycopg2, csv, urllib.request, os, smtplib
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

DB = os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi")
CSV_URL = "https://www.data.gouv.fr/api/1/datasets/r/7987214d-949e-4245-b005-5cc4e7a5df36"
CSV_FILE = "/tmp/diag_update.csv"
BREVO_API = os.environ.get("BREVO_API_KEY", "")
TODAY = date.today()

def get_conn():
    return psycopg2.connect(DB)

def telecharger_csv():
    print(f"[{datetime.now()}] Telechargement CSV...")
    urllib.request.urlretrieve(CSV_URL, CSV_FILE)
    print(f"[{datetime.now()}] OK")

def importer_csv():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("TRUNCATE diagnostiqueurs_officiels RESTART IDENTITY")
    count = 0
    with open(CSV_FILE, encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            if len(row) < 15: continue
            try:
                vals = [r.strip().strip(chr(34)) or None for r in row[:15]]
                cur.execute("""INSERT INTO diagnostiqueurs_officiels
                    (nom,prenom,societe,adresse,cp,ville,tel1,tel2,email,
                    organisme,org_cofrac,type_certificat,num_certificat,date_debut,date_fin)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", vals)
                count += 1
            except: pass
    conn.commit()
    cur.close()
    conn.close()
    os.remove(CSV_FILE)
    print(f"[{datetime.now()}] {count} diagnostiqueurs importes")


def envoyer_brevo(to_email, sujet, corps):
    import json as j
    data = j.dumps({'sender':{'name':'Le Diagnostiqueur','email':'contact@le-diagnostiqueur.com'},'to':[{'email':to_email}],'subject':sujet,'textContent':corps}).encode()
    req = urllib.request.Request('https://api.brevo.com/v3/smtp/email',data=data,headers={'api-key':BREVO_API,'Content-Type':'application/json'})
    try:
        urllib.request.urlopen(req,timeout=10)
        print(f'  -> Email envoye a {to_email}')
    except Exception as e:
        print(f'  -> Erreur {to_email}: {e}')

def envoyer_alertes_recertification():
    conn = get_conn()
    cur = conn.cursor()
    
    j90 = TODAY + timedelta(days=90)
    j30 = TODAY + timedelta(days=30)
    j7  = TODAY + timedelta(days=7)
    
    cur.execute("""
        SELECT m.email, m.prenom, m.nom,
               d.type_certificat, d.organisme, d.date_fin, d.num_certificat
        FROM diagnostiqueurs_membres m
        JOIN diagnostiqueurs_officiels d 
            ON LOWER(d.nom) = LOWER(m.nom) 
            AND LOWER(d.prenom) = LOWER(m.prenom)
        WHERE m.is_active = true
        AND m.alerte_recertification = true
        AND d.date_fin IS NOT NULL
        AND d.date_fin::date IN (%s, %s, %s)
    """, (j90, j30, j7))
    
    alertes = cur.fetchall()
    print(f"[{datetime.now()}] {len(alertes)} alertes recertification a envoyer")
    
    for email, prenom, nom, cert, org, date_fin, num_cert in alertes:
        jours = (date_fin - TODAY).days
        if jours == 90:
            urgence = "dans 3 mois"
            niveau = "information"
            emoji = "📋"
        elif jours == 30:
            urgence = "dans 1 mois"
            niveau = "attention"
            emoji = "⚠️"
        else:
            urgence = "dans 7 jours"
            niveau = "URGENT"
            emoji = "🚨"
        
        sujet = f"{emoji} Votre certification {cert} expire {urgence}"
        corps = f"""Bonjour {prenom},

Votre certification "{cert}" (N° {num_cert}) délivrée par {org} expire le {date_fin.strftime("%d/%m/%Y")}, soit {urgence}.

Pour maintenir votre activité de diagnostiqueur certifié, pensez à :
- Contacter votre organisme certificateur : {org}
- Préparer votre dossier de renouvellement
- Suivre une formation de surveillance si nécessaire

Besoin d'aide ? Retrouvez nos formations de surveillance sur le-diagnostiqueur.com

Cordialement,
L'équipe Le Diagnostiqueur
contact@le-diagnostiqueur.com
"""
        print(f"  -> Alerte {niveau} : {prenom} {nom} <{email}> — {cert} expire {urgence}")
        envoyer_brevo(email, sujet, corps)
    
    cur.close()
    conn.close()

def detecter_nouvelles_certifications():
    conn = get_conn()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT m.email, m.prenom, m.nom,
               d.type_certificat, d.organisme, d.date_fin, d.num_certificat
        FROM diagnostiqueurs_membres m
        JOIN diagnostiqueurs_officiels d 
            ON LOWER(d.nom) = LOWER(m.nom) 
            AND LOWER(d.prenom) = LOWER(m.prenom)
        WHERE m.is_active = true
        AND d.date_debut::date = %s
    """, (TODAY,))
    
    nouvelles = cur.fetchall()
    print(f"[{datetime.now()}] {len(nouvelles)} nouvelles certifications detectees aujourd'hui")
    
    for email, prenom, nom, cert, org, date_fin, num_cert in nouvelles:
        sujet = f"✅ Votre certification {cert} a bien été enregistrée"
        corps = f"""Bonjour {prenom},

Bonne nouvelle ! Votre certification "{cert}" (N° {num_cert}) vient d'être enregistrée dans l'annuaire officiel des diagnostiqueurs certifiés.

Détails :
- Certification : {cert}
- Organisme : {org}
- Numéro : {num_cert}
- Valide jusqu'au : {date_fin.strftime("%d/%m/%Y") if date_fin else "Non renseigné"}

Votre profil sur Le Diagnostiqueur a été automatiquement mis à jour.

Cordialement,
L'équipe Le Diagnostiqueur
"""
        print(f"  -> Nouvelle certification : {prenom} {nom} — {cert}")
        envoyer_brevo(email, sujet, corps)
    
    cur.close()
    conn.close()

def stats_quotidiennes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN date_fin::date < %s THEN 1 END) as expires,
            COUNT(CASE WHEN date_fin::date BETWEEN %s AND %s THEN 1 END) as expire_90j,
            COUNT(CASE WHEN date_fin::date BETWEEN %s AND %s THEN 1 END) as expire_30j
        FROM diagnostiqueurs_officiels
        WHERE date_fin IS NOT NULL
    """, (TODAY, TODAY, TODAY+timedelta(days=90), TODAY, TODAY+timedelta(days=30)))
    
    row = cur.fetchone()
    print(f"[{datetime.now()}] Stats : {row[0]} certifications | {row[1]} expirees | {row[2]} expirent <90j | {row[3]} expirent <30j")
    cur.close()
    conn.close()

telecharger_csv()
importer_csv()
stats_quotidiennes()
envoyer_alertes_recertification()
detecter_nouvelles_certifications()
print(f"[{datetime.now()}] Sync complete")
