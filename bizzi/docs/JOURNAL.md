# JOURNAL Bizzi

## 2026-05-09 (nuit) — Phase 32 BACKEND FONDATION : multi-agents + mémoire perpétuelle + tracking commissions

**Contexte :** Backend mobile fondation livré : pierre angulaire du flywheel monétisation
Bizzi Mobile (cf `project_bizzi_flywheel.md`). Sans cette couche, le mobile est un POC sans
business model. Avec, chaque recommandation tenant tracée → commission auto-calculée.

**Réalisé :**
- DB postgres bizzi : 2 nouvelles tables `mobile_user_memory` (mémoire perpétuelle scopée
  user, optionnellement par agent_slug, fact_type, expires_at, metadata JSONB) +
  `mobile_recommendations` (recommandations + funnel click → conversion → commission_due
  → commission_paid). Tables `mobile_users` + `mobile_agents` déjà créées par sub-PWA hier.
  Tous owner `bizzi_admin`. Index sur (user_id, agent_slug, created_at DESC), (tenant, month),
  partial index sur unpaid commissions.
- 3 modules routes : `api/routes/mobile_agents.py` (templates pré-faits voyage/finance/sante/
  sport/apprentissage/actualites/politique, PATCH agent, ask_peer entre agents du même user),
  `api/routes/mobile_memory.py` (CRUD mémoire + search ILIKE, scoped user JWT strict),
  `api/routes/mobile_recommendations.py` (recommend, click, conversion 3-auth-paths
  user/admin/tenant-token, admin/stats par mois, admin/unpaid, admin/mark_paid, admin/grid).
- Grille commissions V1 hardcodée : airbizness 5€/15€/5%hôtel, onyx 0.5€/abonné,
  lediagnostiqueur 50€/RDV + 80€/diag, lesdemocrates 10€/adhésion + 5%/don. Default 2%.
  V2 prévue : table `mobile_commission_rules` configurable par YAML tenant.
- Wiring `api/main.py` : 3 nouveaux `include_router` sous prefix `/api/mobile/{agents,memory,
  recommendations}`. Backup pre-mobile-fondation conservé.
- Auth :
  - User : JWT mobile via `Authorization: Bearer` (réutilise `get_current_user` de auth.py)
  - Admin : `X-Admin-Token` header (env `MOBILE_ADMIN_TOKEN` ajouté au .env)
  - Tenant (pour notifier conversions) : `X-Tenant-Token` matché contre env `BIZZI_TENANT_*_TOKEN`
- Tests E2E (18 cas couverts, tous OK) : login → me → list_agents → create from_template
  politique → patch persona voyage → add memory (scoped agent + global) → search "business" →
  recommend airbizness flight_business CDG-NRT → click → conversion 4250€ → 15€ commission →
  reco lediagnostiqueur RDV → conversion → 50€ commission → admin/stats month → admin/unpaid
  → mark_paid → ask_peer voyage→finance → ISOLATION : user3 ne voit pas mémoire/reco user2,
  conversion cross-user refusée 403.
- Restart `bizzi-api` : RUNNING, openapi.json expose 11 nouvelles routes mobile.

**Action requise Pascal :** la grille commissions V1 est hardcodée dans
`api/routes/mobile_recommendations.py` (`COMMISSION_GRID`). Quand un nouveau tenant rejoint
ou quand un tenant existant veut négocier sa commission, soit on édite ce dict, soit on
passe en V2 (table dédiée). Token admin pour Pascal stocké dans `.env` sous `MOBILE_ADMIN_TOKEN`
(à utiliser via header `X-Admin-Token` pour /api/mobile/recommendations/admin/*).

## 2026-05-09 (soir) — Phase 32 SWITCH URL : bizzi.fr/app/ → app.bizzi.fr/

**Contexte :** Migration PWA Bizzi Mobile du sous-dossier `https://bizzi.fr/app/`
vers une URL propre racine `https://app.bizzi.fr/`. DNS `app.bizzi.fr → 141.95.7.170`
posé et propagé (vérifié 1.1.1.1 + 8.8.8.8 + 9.9.9.9).

**Réalisé :**
- Vhost `/etc/nginx/sites-available/app.bizzi.fr` créé (root `/opt/bizzi/public/app/`,
  SPA fallback `/index.html`, SW + manifest no-cache, proxy `/api/` vers backend
  Bizzi 127.0.0.1:3000, security headers X-Frame-Options/X-Content-Type-Options/
  Referrer-Policy)
- Cert SSL Let's Encrypt obtenu pour `app.bizzi.fr` (valide jusqu'au 2026-08-07,
  auto-renew programmé) + redirect 80→443 auto par certbot
- `manifest.json` MAJ : `start_url` et `scope` passés de `/app/` à `/`,
  toutes les `icons[].src` `/app/icons/...` → `/icons/...`
- `sw.js` MAJ : `CACHE_VERSION` bumpé `v1-2026-05-09` → `v2-2026-05-09-rootscope`
  (force purge des anciens caches), tous les `PRECACHE_URLS` `/app/...` → `/...`,
  fetch handler simplifié (route tout GET non-/api/ vers cache stale-while-revalidate)
- `index.html` + `login.html` + `app.js` + `auth.js` MAJ : toutes refs `/app/...`
  (manifest, icons, css, scripts, redirects window.location, register SW scope)
  passées à racine `/`
- Vhost `bizzi.fr` modifié : `location /app/ { alias }` remplacé par
  `rewrite ^/app/(.*)$ https://app.bizzi.fr/$1 permanent;` (préserve sous-paths,
  ex. `/app/login.html` → `/login.html`) + `location = /app` et `location = /app/`
  → `301 https://app.bizzi.fr/`. Routes `/api/`, `/tg-bridge/`, `/` intactes
- Backups horodatés faits avant chaque edit : `manifest.json.bak-pre-switch-*`,
  `sw.js.bak-pre-switch-*`, `index.html.bak-...`, `login.html.bak-...`,
  `app.js.bak-...`, `auth.js.bak-...`, `bizzi.bak-pre-switch-...` (vhost)

**Tests E2E validés :**
- `https://app.bizzi.fr/` → 200 OK
- `https://app.bizzi.fr/manifest.json` → JSON propre (`scope: "/"`, `start_url: "/"`)
- `https://app.bizzi.fr/sw.js` → 200 + `Cache-Control: no-cache, no-store, must-revalidate`
- `https://bizzi.fr/app/` → 301 → `https://app.bizzi.fr/`
- `https://bizzi.fr/app/login.html` → 301 → `https://app.bizzi.fr/login.html`
- `https://bizzi.fr/app/icons/icon-192.png` → 301 → `https://app.bizzi.fr/icons/icon-192.png`
- `https://bizzi.fr/` → 200 (base intacte)
- Cert SSL : `subject=CN=app.bizzi.fr`, valid `2026-05-09 → 2026-08-07`
- Screenshot mobile 375x812 (`/tmp/app-bizzi-shot.jpg`) : login.html rendu propre,
  logo abeille + form email/password + bouton "Se connecter" jaune

**Note utilisateur (Pascal) :** Si la PWA était déjà installée depuis
`bizzi.fr/app/`, désinstaller d'abord (le SW v1 a un scope `/app/` figé qui ne
peut pas être hot-migré). Sur le tel : Chrome/Safari → app.bizzi.fr → Add to
Home Screen → nouvelle install propre avec scope `/`.

---

## 2026-05-09 (jour) — Phase 32 V0 LIVRÉE : PWA Bizzi Mobile installable

**Contexte :** Étape 1 de la roadmap Bizzi Mobile (Phase 32 du flywheel).
PWA = MVP installable via "Add to Home Screen" sur Android (Chrome/Edge) et iOS
(Safari "Sur l'écran d'accueil"). Servie sous `https://bizzi.fr/app/` en attendant
le DNS `app.bizzi.fr`.

**Réalisé :**
- Tenant `mobile-pascal.yaml` créé — assistant personnel de Pascal, persona
  chaleureux/direct/FR, modèle claude-haiku-4-5, rate-limit 500/jour, queries vides
  (agent conversationnel pur, pas de DB métier — la mémoire perpétuelle vient
  de chat_logs Phase 11 côté serveur)
- PWA déployée dans `/opt/bizzi/public/app/` (10 fichiers JS/CSS/HTML + 3 icônes PNG)
  - `index.html` : chat plein écran (header/messages/composer) + install banner
  - `login.html` : auth locale V0 (pascal/bizzi hardcodé en attendant /api/auth/login)
  - `manifest.json` : nom/icônes/theme #ffd23f/background #0a0a0f, display standalone
  - `sw.js` : SW versionné (v1-2026-05-09), stale-while-revalidate pour assets,
    network-first pour /api/, fallback offline JSON
  - `style.css` : mobile-first, charte noir+or Bizzi, fonts Syne/Outfit/DM Mono,
    breakpoint 600px pour desktop
  - `memory.js` : wrapper localStorage cap 50 messages, session_id généré + persisté
  - `auth.js` : login/logout + requireAuth gate
  - `voice.js` : Web Speech API fr-FR (interim+final results, dégradation gracieuse
    si non supporté)
  - `chat.js` : POST /api/tools/chat/message + persistance localStorage + bulle
    "Bizzi réfléchit…" + auto-scroll + meta cost/confidence
  - `app.js` : orchestre tout — auth gate, mic, logout, online/offline, SW register,
    install prompt (beforeinstallprompt + hint iOS Safari)
- Icônes générées via rsvg-convert depuis SVG abeille Bizzi → 192/512/180px
- Vhost nginx `/etc/nginx/sites-available/bizzi` MAJ : `/app/` (alias),
  `= /app/sw.js` (Cache-Control no-store), `= /app/manifest.json`,
  `= /app` (301 → /app/) — backup `bizzi.bak-pre-pwa-1778341039` posé
- bizzi-api restart pour charger le nouveau tenant

**Validations HTTP :**
- `https://bizzi.fr/app/` → 200 (index.html servi)
- `https://bizzi.fr/app/login.html` → 200
- `https://bizzi.fr/app/manifest.json` → 200 (4 icônes, validation JSON OK)
- `https://bizzi.fr/app/sw.js` → 200 + Cache-Control: no-cache,no-store,must-revalidate
- Icônes 192/512/apple-touch → 200 image/png

**Validations API E2E (tenant mobile-pascal) :**
- Tour 1 : "Mon prénom est Pascal et j'adore le cyclisme" → réponse cohérente
- Tour 2 (même session_id) : "Quel est mon prénom et mon hobby ?" → "Pascal"
  + "cyclisme" récupérés ✅ — mémoire serveur Phase 11 OK
- Coût ~$0.0006 par message (claude-haiku-4-5)
- Screenshot mobile (375x812, login screen) : `/tmp/bizzi-mobile-shot.jpg`

**Action requise pour Pascal pour switcher vers `app.bizzi.fr` :**
1. Ajouter enregistrement DNS A `app.bizzi.fr` → 141.95.7.170 chez le registrar
2. `sudo certbot certonly --nginx -d app.bizzi.fr` pour le cert SSL
3. Créer un nouveau vhost `/etc/nginx/sites-available/app.bizzi.fr` qui sert
   directement `/opt/bizzi/public/app/` à la racine `/` (au lieu de `/app/`)
4. Dans la PWA, MAJ `start_url` et `scope` du manifest.json + URLs `/app/*` du sw.js
5. Optionnel : 301 `https://bizzi.fr/app/` → `https://app.bizzi.fr/`

**Limites V0 (à noter) :**
- Auth hardcodée client-side (pascal/bizzi) — pas sécurisé pour prod multi-user.
  À remplacer par /api/auth/login avec JWT côté serveur quand on ouvrira au public.
- Pas d'orchestration multi-app dans la PWA (juste chat conversationnel) —
  cohérent avec le scope MVP. Pour orchestrer (envoyer SMS, lancer scrape, etc.)
  il faudra ajouter des tools côté tenant ou passer à une app native (TWA Android +
  Accessibility plus tard, étape 3 de la roadmap).
- Web Speech API : dépend du navigateur. Chrome Android et Safari iOS supportent fr-FR.
  Whisper côté serveur n'est pas branché — bouton micro masqué si non supporté.
- Service Worker pré-cache shell uniquement — pas de cache des messages API.
  Offline-friendly mais pas offline-fonctionnel pour le chat (cohérent : chat = réseau).
- Install prompt iOS Safari : pas d'API native, hint manuel "Partager → Sur l'écran
  d'accueil" affiché.

**Fichiers clés :**
- `/opt/bizzi/public/app/` (10 fichiers + icons/)
- `/opt/bizzi/bizzi/tenants/mobile-pascal.yaml`
- `/etc/nginx/sites-available/bizzi` (backup `.bak-pre-pwa-1778341039`)

**Coût test : ~$0.001 par message côté tenant mobile-pascal.**

---

## 2026-05-09 (nuit) — Phase 14a LIVRÉE : migration data Onyx vers DB postgres dédiée

**Contexte :** Onyx Infos = média indépendant, ses 2034 productions étaient
mélangées dans la DB `bizzi` avec celles des autres tenants (lesdemocrates,
lediagnostiqueur). Pour aligner les CGU client (data Onyx = propriété d'Onyx)
et préparer le pattern multi-tenant (DB séparée par tenant), migration des
data Onyx vers une DB postgres dédiée `onyx_content`.

**Pattern appliqué :** identique airbizness — DB owner dédié `onyx_admin`,
user reader READ-ONLY `bizzi_reader_onyx` consommé par Bizzi via tenant_db YAML.

**Réalisé :**
- pg_dump complet de bizzi (32 Mo, sécurité rollback)
- DB `onyx_content` créée + schéma 9 tables (productions, article_images,
  article_scores, article_sources, categories, regions, cities, agents, tenants)
- 2034 productions migrées (1946 publiés + 79 trashed + 5 rejected + 4 approved)
  — vérifié byte-perfect via md5(content_html) sur l'ensemble des publiés
- 12 categories, 14 regions, 11 agents, 1 tenant migrés
- `tenants/onyx.yaml` MAJ → DB `onyx_content` + `password_env: BIZZI_ONYX_DB_PASSWORD`
- `api/routes/content.py` MAJ → engine multi-tenant via tenant_db YAML
  (Onyx pointe sur onyx_content, fallback `_legacy_db` pour lookup token_hash)
- Bug existant corrigé : `tenant_id` non défini dans `article_meta` et `sitemap` → utilise `ONYX_TENANT_ID`
- `BIZZI_ONYX_DB_PASSWORD` ajouté dans `/etc/supervisor/conf.d/bizzi.conf`
- bizzi-api restart : 7/7 smoke tests passent (article-by-slug, article live,
  régions, équipe, sitemap.xml=1949 URLs, list=1841 articles édition nationale, home)
- 5 random slugs : md5(content) API == md5(content) DB → cohérence parfaite
- static_publisher (filet 14b) continue à tourner depuis bizzi (50 articles
  fallback HTML statique générés toutes les 5 min) — découplé volontairement
  de la migration pour résilience pendant la fenêtre de 1 semaine

**Filet de sécurité :** Phase 14b actif (static_publisher cron 5min + nginx
fallback `error_page 5xx`). Si l'API casse, les 50 derniers articles restent
lisibles via `/articles/<slug>.html`.

**Conservation :** Data Onyx restent dans `bizzi.productions WHERE tenant_id=1`
pendant 1 semaine pour rollback safety. À supprimer manuellement après vérif.

**Pattern à répliquer :** lesdemocrates (8 articles, tenant_id=4) et
lediagnostiqueur (1 article, tenant_id=2) dans les semaines à venir.

## À faire

1. **Après 1 semaine sans incident** (cible 2026-05-16) :
   `DELETE FROM bizzi.productions WHERE tenant_id=1`
   + idem `categories`, `regions`, `agents`, `tenants` si pas utilisés ailleurs
   (libère ~32 Mo de la DB bizzi). Avant DELETE, vérifier de nouveau les counts
   dans onyx_content + vérifier static_publisher continue à pointer où il faut
   (wrapper actuel pointe sur bizzi via DATABASE_URL — à MAJ).
2. **Pattern à répliquer** :
   - lesdemocrates : créer `lesdemocrates_content` DB + `bizzi_reader_lesdemocrates`
   - lediagnostiqueur : créer `lediagnostiqueur_content` DB + `bizzi_reader_lediagnostiqueur`
   Suivre la même séquence Phase A→F que cette migration (commit 1a3a4e8 + 31e31ea + ab04ffb comme template).
3. **Future** : déplacer le mapping `host → tenant_slug` du hard-code (Onyx
   only) vers une table de configuration ou variable d'env, pour automatiser
   la sélection du tenant en multi-tenant complet.
