"""
tools/escalation/escalation_engine.py
=======================================
Moteur de remontée hiérarchique universel Bizzi.

Fonctionne pour n'importe quel domaine :
  - Parti politique  : signalement → section → fédération → national
  - Entreprise IT    : ticket → équipe → direction → DG
  - Réseau franchise : remontée → région → national
  - Hôpital          : incident → service → direction → ARS

Tout est configuré dans le yaml du client.
"""

import logging, httpx, json, re
from datetime import datetime
from uuid import uuid4
from typing import Optional

logger = logging.getLogger("tools.escalation")

SIGNALS_DB:  list = []
ISSUES_DB:   list = []
PROJECTS_DB: list = []

OLLAMA = "http://localhost:11434"
MODEL  = "mistral:7b"


class EscalationEngine:

    def __init__(self, domain_config):
        self.domain = domain_config
        self.tenant = domain_config.name
        esc = getattr(domain_config, 'escalation_config', {}) or {}
        self.signal_label  = esc.get('signal_label',  'Signalement')
        self.issue_label   = esc.get('issue_label',   'Problématique')
        self.project_label = esc.get('project_label', 'Projet')
        self.categories    = esc.get('categories', [])
        self.levels        = esc.get('levels', [])

    # ── Catégorisation ────────────────────────────────────────

    async def categorize(self, content: str) -> dict:
        cats = [c['label'] for c in self.categories] if self.categories \
               else ["Sécurité","Logement","Transport","Environnement","Services publics","Social","Autre"]

        prompt = f"""Analyse ce signalement. Retourne UNIQUEMENT ce JSON :
{{"category":"{cats[0]}","subcategory":"sous-catégorie précise","urgency":"haute|normale|basse","summary":"1 phrase"}}
Catégories possibles : {', '.join(cats)}
Signalement : {content}"""

        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post(f"{OLLAMA}/api/generate",
                    json={"model":MODEL,"prompt":prompt,"stream":False,
                          "options":{"temperature":0.2,"num_predict":150}})
                if r.status_code == 200:
                    raw = r.json().get("response","")
                    m = re.search(r'\{.*?\}', raw, re.DOTALL)
                    if m: return json.loads(m.group())
        except Exception as e:
            logger.error(f"[ESCALATION] Categorize: {e}")

        return {"category":"Autre","subcategory":"","urgency":"normale","summary":content[:80]}

    # ── Réponse niveau 1 ──────────────────────────────────────

    async def generate_response(self, content: str, category: str,
                                 urgency: str, author: str) -> str:
        delays = {"haute":"24h","normale":"48h","basse":"5 jours"}
        prompt = f"""Tu es l'agent d'accueil de {self.tenant}.
Réponds à ce {self.signal_label.lower()} de {author} (catégorie: {category}, urgence: {urgency}).
Contenu : {content}
Réponse empathique, 3 phrases max, délai {delays.get(urgency,'48h')}, signe au nom de {self.tenant}."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(f"{OLLAMA}/api/generate",
                    json={"model":MODEL,"prompt":prompt,"stream":False,
                          "options":{"temperature":0.5,"num_predict":200}})
                if r.status_code == 200:
                    return r.json().get("response","").strip()
        except Exception as e:
            logger.error(f"[ESCALATION] Response: {e}")
        return (f"Bonjour {author}, votre {self.signal_label.lower()} ({category}) "
                f"est bien enregistré. Réponse sous {delays.get(urgency,'48h')}. "
                f"Cordialement, {self.tenant}")

    # ── Vérification seuils ───────────────────────────────────

    async def check_thresholds(self, signal_id: str, category: str,
                                location: str, scopes: dict):
        for lvl in self.levels:
            n = lvl.get('level', 1)
            if n <= 1: continue
            threshold   = lvl.get('threshold', 3)
            scope       = lvl.get('scope', 'commune')
            scope_value = scopes.get(scope, location)

            similar = [s for s in SIGNALS_DB
                       if s['tenant']   == self.tenant
                       and s['category'] == category
                       and s.get('scopes',{}).get(scope) == scope_value
                       and s['status']   == 'open']

            if len(similar) >= threshold:
                existing = next((i for i in ISSUES_DB
                    if i['tenant']      == self.tenant
                    and i['category']   == category
                    and i['scope']      == scope
                    and i['scope_value']== scope_value
                    and i['status']     == 'open'), None)
                if not existing:
                    await self._create_issue(similar, category, scope, scope_value, n, lvl)

    async def _create_issue(self, signals, category, scope, scope_value, level, lvl_cfg):
        issue_id = f"ISS-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:6].upper()}"
        contents = [s['content'] for s in signals[:5]]

        prompt = f"""Résumé de {self.issue_label.lower()} pour {self.tenant}.
Catégorie : {category} · Zone : {scope_value} · Niveau : {lvl_cfg.get('name','')}
Signalements : {chr(10).join(f'- {c}' for c in contents)}
2-3 phrases : problématique centrale, ampleur, action recommandée."""
        summary = ""
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(f"{OLLAMA}/api/generate",
                    json={"model":MODEL,"prompt":prompt,"stream":False,
                          "options":{"temperature":0.4,"num_predict":200}})
                if r.status_code == 200:
                    summary = r.json().get("response","").strip()
        except: pass
        if not summary:
            summary = f"{len(signals)} {self.signal_label.lower()}s similaires sur {category} à {scope_value}."

        voice = lvl_cfg.get('publish_voice','')
        for k,v in {'ville':scope_value,'departement':scope_value,'region':scope_value,'category':category}.items():
            voice = voice.replace(f'{{{k}}}', v)

        issue = {
            "id": issue_id, "tenant": self.tenant,
            "category": category, "scope": scope, "scope_value": scope_value,
            "level": level, "level_label": lvl_cfg.get('name', f'Niveau {level}'),
            "signal_ids": [s['id'] for s in signals], "signal_count": len(signals),
            "summary": summary, "publish_text": voice,
            "notify": lvl_cfg.get('notify',''), "publish": lvl_cfg.get('publish', False),
            "status": "open", "published": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        ISSUES_DB.append(issue)
        logger.info(f"[ESCALATION] Issue {issue_id} · {category} · {scope_value} · niveau {level}")
        return issue

    def _build_scopes(self, location: str) -> dict:
        dept_map = {'évry':'91','evry':'91','essonne':'91','paris':'75',
                    'lyon':'69','marseille':'13','bordeaux':'33','toulouse':'31'}
        region_map = {'91':'Île-de-France','75':'Île-de-France','69':'Auvergne-Rhône-Alpes',
                      '13':'PACA','33':'Nouvelle-Aquitaine'}
        loc = location.lower()
        dept = next((v for k,v in dept_map.items() if k in loc), location)
        region = region_map.get(dept, location)
        return {"commune":location,"departement":dept,"region":region,
                "produit":location,"gamme":location,"service":location,"global":"global"}

    # ── Point d'entrée ────────────────────────────────────────

    async def process_signal(self, content: str, location: str,
                              author: str, contact: str) -> dict:
        cat    = await self.categorize(content)
        resp   = await self.generate_response(content, cat['category'], cat['urgency'], author)
        scopes = self._build_scopes(location)
        sig_id = f"SIG-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid4())[:6].upper()}"

        signal = {
            "id": sig_id, "tenant": self.tenant,
            "content": content, "location": location,
            "author": author, "contact": contact,
            "category": cat['category'], "subcategory": cat.get('subcategory',''),
            "urgency": cat['urgency'], "response": resp,
            "scopes": scopes, "status": "open",
            "created_at": datetime.utcnow().isoformat(),
        }
        SIGNALS_DB.append(signal)
        logger.info(f"[ESCALATION] Signal {sig_id} · {cat['category']} · {location}")
        await self.check_thresholds(sig_id, cat['category'], location, scopes)

        return {"signal_id":sig_id,"category":cat['category'],
                "subcategory":cat.get('subcategory',''),"urgency":cat['urgency'],
                "response":resp,"status":"processed",
                "created_at":signal['created_at']}

    def get_stats(self, scope: str = None, scope_value: str = None) -> dict:
        sigs = [s for s in SIGNALS_DB if s['tenant'] == self.tenant]
        iss  = [i for i in ISSUES_DB  if i['tenant'] == self.tenant]
        projs= [p for p in PROJECTS_DB if p['tenant'] == self.tenant]
        if scope and scope_value:
            sigs = [s for s in sigs if s.get('scopes',{}).get(scope) == scope_value]
            iss  = [i for i in iss  if i.get('scope') == scope and i.get('scope_value') == scope_value]
        by_cat = {}
        for s in sigs:
            c = s.get('category','Autre')
            by_cat[c] = by_cat.get(c,0) + 1
        return {"total_signals":len(sigs),"open_signals":len([s for s in sigs if s['status']=='open']),
                "total_issues":len(iss),"total_projects":len(projs),
                "by_category":by_cat,"urgent":len([s for s in sigs if s.get('urgency')=='haute'])}

    def get_signals(self, scope: str = None, scope_value: str = None,
                    category: str = None, status: str = None) -> list:
        sigs = [s for s in SIGNALS_DB if s['tenant'] == self.tenant]
        if scope and scope_value:
            sigs = [s for s in sigs if s.get('scopes',{}).get(scope) == scope_value]
        if category: sigs = [s for s in sigs if s.get('category') == category]
        if status:   sigs = [s for s in sigs if s.get('status')   == status]
        return sorted(sigs, key=lambda x: x['created_at'], reverse=True)

    def get_issues(self, level: int = None, scope: str = None,
                   scope_value: str = None) -> list:
        iss = [i for i in ISSUES_DB if i['tenant'] == self.tenant]
        if level:       iss = [i for i in iss if i.get('level')       == level]
        if scope:       iss = [i for i in iss if i.get('scope')       == scope]
        if scope_value: iss = [i for i in iss if i.get('scope_value') == scope_value]
        return sorted(iss, key=lambda x: x['created_at'], reverse=True)

    def validate_project(self, project_id: str, validated_by: str) -> dict:
        proj = next((p for p in PROJECTS_DB if p['id'] == project_id), None)
        if proj:
            proj['status']       = 'validated'
            proj['validated_by'] = validated_by
            proj['validated_at'] = datetime.utcnow().isoformat()
        return proj
