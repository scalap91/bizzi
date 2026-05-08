import os
from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
from pydantic import BaseModel
from typing import Optional
import hashlib, secrets, urllib.request, json

router = APIRouter()
_db = create_engine(os.environ.get("DATABASE_URL", "postgresql://bizzi_admin:CHANGE_ME@localhost/bizzi"))
BREVO_API = os.environ.get("BREVO_API_KEY", "")

class InscriptionData(BaseModel):
    nom: str
    prenom: str
    email: str
    password: str
    telephone: Optional[str] = None
    site_web: Optional[str] = None
    societe: Optional[str] = None
    alerte_decrets: bool = True
    alerte_recertification: bool = True
    alerte_jurisprudence: bool = False
    newsletter: bool = True
    frequence_newsletter: str = "hebdo"

@router.get("/verifier")
async def verifier(nom: str, prenom: str, num_cert: Optional[str] = None):
    with _db.connect() as conn:
        rows = conn.execute(text(
            "SELECT nom,prenom,societe,ville,cp,organisme,type_certificat,num_certificat,date_fin "
            "FROM diagnostiqueurs_officiels WHERE LOWER(nom)=LOWER(:n) AND LOWER(prenom)=LOWER(:p) "
            "ORDER BY date_fin DESC"
        ), {"n": nom, "p": prenom}).fetchall()
        if not rows:
            return {"found": False}
        certs = list(set([r[6] for r in rows if r[6]]))
        cert_details = [{"type": r[6], "num": r[7], "fin": str(r[8]) if r[8] else None} for r in rows if r[6]]
        r = rows[0]
        return {
            "found": True, "nom": r[0], "prenom": r[1], "societe": r[2] or "",
            "organisme": r[5] or "", "certifications": certs, "cert_details": cert_details
        }

@router.post("/inscrire")
async def inscrire(data: InscriptionData):
    with _db.connect() as conn:
        ex = conn.execute(text("SELECT id FROM diagnostiqueurs_membres WHERE email=:e"), {"e": data.email}).fetchone()
        if ex:
            raise HTTPException(status_code=400, detail="Email deja inscrit.")
        ph = hashlib.sha256(data.password.encode()).hexdigest()
        wt = "wd_" + secrets.token_hex(16)
        conn.execute(text(
            "INSERT INTO diagnostiqueurs_membres (nom,prenom,email,password_hash,telephone,site_web,societe,"
            "alerte_decrets,alerte_recertification,alerte_jurisprudence,newsletter,frequence_newsletter,"
            "widget_token,is_active,is_verified,date_inscription) "
            "VALUES (:nom,:prenom,:email,:ph,:tel,:site,:soc,:ad,:ar,:aj,:nl,:fn,:wt,true,false,NOW())"
        ), {"nom":data.nom,"prenom":data.prenom,"email":data.email,"ph":ph,"tel":data.telephone,
            "site":data.site_web,"soc":data.societe,"ad":data.alerte_decrets,"ar":data.alerte_recertification,
            "aj":data.alerte_jurisprudence,"nl":data.newsletter,"fn":data.frequence_newsletter,"wt":wt})
        conn.commit()
    try:
        corps = f"Bonjour {data.prenom},\n\nVotre compte Le Diagnostiqueur est actif.\n\nhttps://le-diagnostiqueur.com/espace-membre.html\n\nCordialement,\nLe Diagnostiqueur"
        d = json.dumps({"sender":{"name":"Le Diagnostiqueur","email":"contact@le-diagnostiqueur.com"},"to":[{"email":data.email}],"subject":"Bienvenue sur Le Diagnostiqueur !","textContent":corps}).encode()
        req = urllib.request.Request("https://api.brevo.com/v3/smtp/email",data=d,headers={"api-key":BREVO_API,"Content-Type":"application/json"})
        urllib.request.urlopen(req,timeout=10)
    except Exception as e:
        print(f"Email erreur: {e}")
    return {"success": True, "widget_token": wt}

@router.post("/login")
async def login(data: dict):
    email = data.get("email","")
    password = data.get("password","")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email et mot de passe requis")
    ph = hashlib.sha256(password.encode()).hexdigest()
    with _db.connect() as conn:
        row = conn.execute(text(
            "SELECT id,nom,prenom,email,societe,site_web,widget_token,alerte_decrets,alerte_recertification,newsletter "
            "FROM diagnostiqueurs_membres WHERE email=:e AND password_hash=:p AND is_active=true"
        ), {"e":email,"p":ph}).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
        return {"success":True,"membre":{"id":row[0],"nom":row[1],"prenom":row[2],"email":row[3],"societe":row[4],"site_web":row[5],"widget_token":row[6],"alerte_decrets":row[7],"alerte_recertification":row[8],"newsletter":row[9]}}

@router.get("/profil")
async def profil(email: str):
    with _db.connect() as conn:
        m = conn.execute(text(
            "SELECT id,nom,prenom,email,societe,site_web,widget_token,alerte_decrets,"
            "alerte_recertification,newsletter,frequence_newsletter,date_inscription "
            "FROM diagnostiqueurs_membres WHERE email=:e AND is_active=true"
        ), {"e":email}).fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Membre non trouve")
        certs = conn.execute(text(
            "SELECT type_certificat,organisme,num_certificat,date_debut,date_fin "
            "FROM diagnostiqueurs_officiels WHERE LOWER(nom)=LOWER(:n) AND LOWER(prenom)=LOWER(:p) "
            "ORDER BY date_fin DESC"
        ), {"n":m[1],"p":m[2]}).fetchall()
        return {
            "id":m[0],"nom":m[1],"prenom":m[2],"email":m[3],"societe":m[4],"site_web":m[5],
            "widget_token":m[6],"alerte_decrets":m[7],"alerte_recertification":m[8],
            "newsletter":m[9],"frequence_newsletter":m[10],"date_inscription":str(m[11]),
            "certifications":[{"type":r[0],"organisme":r[1],"num":r[2],"debut":str(r[3]),"fin":str(r[4])} for r in certs]
        }

class ModifierData(BaseModel):
    email: str
    telephone: Optional[str] = None
    site_web: Optional[str] = None
    societe: Optional[str] = None
    assureur: Optional[str] = None
    numero_police: Optional[str] = None
    alerte_decrets: Optional[bool] = None
    alerte_recertification: Optional[bool] = None
    alerte_jurisprudence: Optional[bool] = None
    newsletter: Optional[bool] = None
    frequence_newsletter: Optional[str] = None

@router.post("/modifier")
async def modifier(data: ModifierData):
    with _db.connect() as conn:
        ex = conn.execute(text("SELECT id FROM diagnostiqueurs_membres WHERE email=:e"),{"e":data.email}).fetchone()
        if not ex:
            raise HTTPException(status_code=404, detail="Membre non trouve")
        fields = []
        vals = {"e": data.email}
        if data.telephone is not None: fields.append("telephone=:tel"); vals["tel"]=data.telephone
        if data.site_web is not None: fields.append("site_web=:site"); vals["site"]=data.site_web
        if data.societe is not None: fields.append("societe=:soc"); vals["soc"]=data.societe
        if data.alerte_decrets is not None: fields.append("alerte_decrets=:ad"); vals["ad"]=data.alerte_decrets
        if data.alerte_recertification is not None: fields.append("alerte_recertification=:ar"); vals["ar"]=data.alerte_recertification
        if data.alerte_jurisprudence is not None: fields.append("alerte_jurisprudence=:aj"); vals["aj"]=data.alerte_jurisprudence
        if data.newsletter is not None: fields.append("newsletter=:nl"); vals["nl"]=data.newsletter
        if data.frequence_newsletter is not None: fields.append("frequence_newsletter=:fn"); vals["fn"]=data.frequence_newsletter
        if not fields:
            return {"success": True, "message": "Rien a modifier"}
        conn.execute(text(f"UPDATE diagnostiqueurs_membres SET {','.join(fields)},updated_at=NOW() WHERE email=:e"),vals)
        conn.commit()
    return {"success": True, "message": "Profil mis a jour"}
