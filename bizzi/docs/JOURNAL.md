# JOURNAL Bizzi

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
