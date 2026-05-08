# bizzi.org_hierarchy — Phases 0 + 1

Module GÉNÉRIQUE multi-tenant de dimension hiérarchique organisationnelle.
Pas politique-only : section/fédération (parti), agence/région (banque),
restaurant/pays (franchise), service/hôpital (santé), etc.

## État Phase 0 (livré)

| Composant | État | Notes |
|---|---|---|
| Scaffold module | ✅ | `_db.py`, `models.py`, `storage.py`, `permissions.py`, `audit.py`, `yaml_loader.py`, `routes.py`, `__init__.py` |
| DDL `migrations/001_org_hierarchy.sql` | ✅ écrite | **⚠️ À VALIDER PASCAL** avant exécution |
| YAML sample lesdemocrates | ✅ patch dans `samples/` | YAML core root-owned, **patch à appliquer manuellement** |
| Loader YAML idempotent | ✅ | upsert par `(tenant_id, external_id)`, deux passes pour parents |
| Helper `get_visible_units(scope)` | ✅ | exposé pour bizzi-audience |
| JWT HS256 minimal | ✅ stdlib | `BIZZI_JWT_SECRET` env, pas de PyJWT requis |
| Endpoints `/api/org/*` | ✅ | units, units/tree, units/{id}, children, descendants, path, geo/resolve, sync-from-yaml |
| Endpoint embed `/embed/org/territories/{tenant_id}` | ✅ | HTML autonome white-label (Phase 0 = arbre géolocalisé, Phase 1 = Leaflet) |
| Fallback region_detector | ✅ | `storage.resolve_city_with_fallback()` — geo_mapping principal, FR hardcoded en fallback (plan additif Pascal 2026-05-08) |
| Wiring `api/main.py` | ⏳ patch prêt | `samples/api_main_patch_apply.sh` (sudo bash …) — **validation Pascal + restart bizzi-api** |
| Tests (3 modules) | ✅ 17/17 | scripts autonomes pattern `python -m bizzi.org_hierarchy.tests.<mod>` |
| `bizzi-loader.js` (mount points) | ✅ squelette | `org/territories`, `org/units-tree`, `org/unit-detail` |
| Audit log writer | ✅ table + writer | utilisation effective en Phase 1 (embed iframe) |

## Phase 1 (livré)

| Composant | État | Notes |
|---|---|---|
| Mapping région canonique | ✅ | `REGION_ALIASES` (14 régions FR), strip diacritics, `_find_unit_by_region` tolère "Ile-de-France"→"idf" |
| JWT enforcement strict `/embed/*` | ✅ | `BIZZI_EMBED_REQUIRE_JWT=true` (défaut), 401/403 explicites, `tenant_id` mismatch détecté |
| Audit log effectif | ✅ | Chaque requête embed → row dans `org_audit_log` |
| Rollup `org_aggregations` | ✅ | `rollup.run_rollup(tenant_id, period)` — feuilles depuis `audience_reports`, parents par cascade |
| Endpoint `GET /units/{id}/aggregations` + `POST /rollup/run` | ✅ | Cron-able |
| Broadcasts CRUD | ✅ | POST `/broadcast` (can_broadcast required), GET `/broadcasts/received?unit_id=`, GET `/broadcasts/{id}`, POST `/broadcasts/{id}/publish` |
| `target_filter` riche | ✅ | `{all}`, `{level,region_id}` (région héritée via parent walk), `{unit_external_ids}`, `{descendant_of}` |
| Audit export `/audit/export` | ✅ | responsable_territorial+, filtres tenant/user/dates |
| Audit purge `/audit/purge` | ✅ | admin only, retention min 7j |

## Phase 1.5 (à venir)

- `trend_pct` dans `org_aggregations` (vs période précédente)
- Embed dashboards `/embed/audience/{section,federation,national}/{id}` complets (Leaflet/MapLibre)
- WebSocket `/embed/audience/stream` scopé via JWT
- Auto-program-generation triggers (catégorie > X% → propose `axe_programme`)
- Cron périodique : rollup horaire + purge audit hebdo

## Coordination cross-modules

- **bizzi-audience** : utilise `from bizzi.org_hierarchy import get_visible_units`
  pour filtrer toutes ses queries `audience_reports.org_unit_id IN visible`.
  La colonne `audience_reports.org_unit_id` est ajoutée par notre migration
  (un seul ALTER, à valider conjointement avec sub-Claude audience).
- **tools.regions.region_detector** (FR hardcoded) : RESTE comme fallback,
  branché dans `storage.resolve_city_with_fallback()`. Si `geo_mapping`
  n'a pas la ville, on tente le détecteur région ; si une région est
  détectée et qu'un `org_unit` du tenant a `geo_meta.region_id` matchant,
  on retourne ce unit. Plan additif Pascal 2026-05-08, audit `/tmp/bizzi_audit_doublons_synth.md`.
- **frontend (futur bizzi-frontend-cc)** : consomme les endpoints `/api/org/*`
  via `bizzi-loader.js` côté tenant (pattern `data-bizzi-mount`).

## Règle critique UI (Pascal) — STRATÉGIE ADDITIVE

**Aucun container HTML créé sur les sites tenants. Aucun nouveau panel.**

Tous les containers existent déjà (ex: `panel-territoires` sur lesdemocrates.org
contient déjà le placeholder `<p>Carte interactive — Shortcode / iFrame à
intégrer</p>`, c'est l'emplacement explicite pour ce module). Le sub-Claude
tenant ajoute juste `data-bizzi-mount="..."` sur ses containers existants ;
`bizzi-loader.js` les hydrate **OU** une iframe pointe vers `/embed/org/...`.

Mes livrables côté tenant = **endpoints + loader + doc**. Aucune modif HTML
de mon côté.

Mount points org_hierarchy supportés Phase 0 :

```html
<!-- carte/arbre des fédérations + sections -->
<div id="panel-territoires" data-bizzi-mount="org/territories"></div>

<!-- liste plate -->
<div data-bizzi-mount="org/units-tree"></div>

<!-- fiche unit (id résolu via data-bizzi-id) -->
<aside data-bizzi-mount="org/unit-detail" data-bizzi-id="42"></aside>
```

## Checklist validation Pascal (avant Phase 1)

- [ ] **DB migration** : `psql bizzi < bizzi/org_hierarchy/migrations/001_org_hierarchy.sql`
  (crée 5 tables + 1 ALTER sur `audience_reports`)
- [ ] **YAML lesdemocrates** : appliquer patch `samples/lesdemocrates_org_hierarchy_patch.yaml`
  à la fin de `domains/lesdemocrates.yaml` (root-owned)
- [ ] **Wiring API** : `api/main.py` est root-owned. Deux options :
  - script idempotent : `sudo bash bizzi/org_hierarchy/samples/api_main_patch_apply.sh`
  - patch lisible : `samples/api_main_patch.diff`

  Effet net :
  ```python
  from org_hierarchy import routes as org_routes
  from org_hierarchy.embed import embed_router as org_embed_router
  app.include_router(org_routes.router, prefix="/api/org",   tags=["OrgHierarchy"])
  app.include_router(org_embed_router,  prefix="/embed/org", tags=["OrgEmbed"])
  ```
- [ ] **Restart bizzi-api** : `sudo systemctl restart bizzi-api`
- [ ] **Init data tenant** : `curl -X POST '.../api/org/sync-from-yaml?tenant_id=4&slug=lesdemocrates'`
- [ ] **Setter env** : `BIZZI_JWT_SECRET` (clé partagée avec backend tenant Fastify)

## Smoke tests

```bash
cd /opt/bizzi
for t in test_jwt test_permissions test_yaml_loader test_geo_fallback \
         test_broadcast test_region_aliases test_rollup; do
  ./bizzi/venv/bin/python -m bizzi.org_hierarchy.tests.$t
done
```

Résultat attendu : 7/7 modules `OK`, 34 checks individuels verts.
