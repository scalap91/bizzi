# Bizzi

Moteur d'agents IA autonomes configurable par domaine.
Un seul moteur. N'importe quelle organisation.

---

## Démarrage rapide

### 1. Choisir un domaine

```bash
# Journal / Média
cp domains/media.yaml config/active.yaml

# Parti politique / Mouvement
cp domains/politics.yaml config/active.yaml

# Cabinet de diagnostic
cp domains/diagnostic.yaml config/active.yaml
```

### 2. Personnaliser

```yaml
# config/active.yaml
identity:
  name: "Mon Organisation"       # ← changer ici
  tagline: "Ma tagline."
  ...
```

### 3. Lancer

```python
from config.domain_loader import DomainLoader
from agents.base_agent import Agent
from moteur.pipeline import Pipeline

# Charger le domaine
domain = DomainLoader.load_domain('media')

# Créer les agents
agents = [
    Agent(slug="sophie", name="Sophie DURAND", agent_id="writer",   domain=domain, specialty="Économie"),
    Agent(slug="marc",   name="Marc FONTAINE",  agent_id="editor",   domain=domain),
    Agent(slug="lisa",   name="Lisa CHEN",      agent_id="community_manager", domain=domain),
]

# Lancer le pipeline
pipeline = Pipeline(domain=domain, agents=agents)
result   = await pipeline.run(topics=["Inflation alimentaire", "Budget 2026"])
```

### 4. Interface bureau

Ouvrir `bureau/bureau.html` dans le navigateur.
La salle de réunion est dans `bureau/meeting-room.html`.

---

## Structure

```
bizzi/
├── domains/
│   ├── media.yaml          ← Journal / Média
│   ├── politics.yaml       ← Parti / Mouvement politique
│   └── diagnostic.yaml     ← Cabinet de diagnostic
│
├── config/
│   └── domain_loader.py    ← Lit le .yaml et configure tout
│
├── agents/
│   └── base_agent.py       ← Agent générique (tous domaines)
│
├── moteur/
│   ├── pipeline.py         ← Pipeline universel
│   └── meeting_room.py     ← Salle de réunion générique
│
└── bureau/
    ├── bureau.html         ← Interface bureau agent
    └── meeting-room.html   ← Salle de réunion
```

---

## Créer un nouveau domaine

1. Copier `domains/media.yaml` → `domains/mon_domaine.yaml`
2. Modifier les agents, le pipeline, le vocabulaire
3. Utiliser : `DomainLoader.load_domain('mon_domaine')`

C'est tout.

---

## Intégration dans un site existant

### API REST
```python
GET  /api/agents          → liste des agents
POST /api/pipeline/run    → lancer le pipeline
GET  /api/content/latest  → contenu produit
```

### Widget JS
```html
<script src="https://bizzi.fr/widget.js"
        data-tenant="mon-org"
        data-token="xxx">
</script>
```

### WordPress
Installer le plugin `bizzi-wp` (à venir).

---

## Domaines préconfigurés

| Domaine    | Agents                                    | Pipeline                              | Output      |
|------------|-------------------------------------------|---------------------------------------|-------------|
| media      | directeur, rédac chef, journalistes, CM   | scrape → rédige → valide → publie     | articles    |
| politics   | président, porte-parole, analystes, CM    | veille → analyse → valide → diffuse   | communiqués |
| diagnostic | directeur, expert, diagnostiqueurs, comm  | collecte → analyse → valide → envoie  | rapports    |

---

## Déploiement

### VPS partagé (plusieurs clients)
```bash
bash deploy/shared.sh
```

### VPS dédié (client premium)
```bash
bash deploy/dedicated.sh
```
