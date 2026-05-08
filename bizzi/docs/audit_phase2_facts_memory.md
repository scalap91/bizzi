# Audit tables existantes pour Phase 2 (facts memory)

Date : 2026-05-09
Contexte : foundation métacognition Bizzi. Phase 1 (confidence) et Phase 3 (ask_peer)
livrées. Phase 2 (facts memory) à décider.

## Tables auditees

| Table | Rows | Schema résumé | Réutilisable pour Phase 2 ? |
|-------|------|--------------|----------------------------|
| `agents` | 26 | id, tenant_id (FK), slug, name, role, system_prompt, etc. | Référentiel agents. **Non** pour facts. |
| `agent_memories` | 0 | id, tenant_id (FK), agent_id, scope (private/shared/team/global), memory_type (note/fact/source/contact/style/rule/event), key, title, content, tags JSONB, importance 0-100, related_production_id, expires_at | **OUI — REUTILISABLE telle quelle** |
| `agent_skills` | 0 | id, agent_id, skill_code, skill_label, level 1-5 | Compétences. **Non** pour facts. |
| `agent_tools` | 0 | id, agent_id, tool_code, enabled, config JSONB | Activation tools. **Non** pour facts. |
| `agent_missions` | 0 | id, tenant_id (FK), assigned_by/to_agent_id, brief, urgency, status, metadata | Briefs / missions. **Non** pour facts. |
| `agent_configs` | 0 | id, tenant_id (FK), agent_id, model, temperature, max_tokens, system_prompt, settings JSONB | Conf LLM par agent. **Non** pour facts. |

## Décision : REUTILISER `agent_memories`

`agent_memories` est conçue précisément pour ce cas :

- `tenant_id` (FK + ON DELETE CASCADE) → isolation stricte par tenant native
- `memory_type` accepte déjà la valeur `'fact'` (CHECK constraint OK)
- `scope` permet de distinguer privé / partagé / équipe / global
- `tags` JSONB + `importance` 0-100 + `expires_at` couvrent toutes les
  classes de besoins V1 (TTL, ranking, filtrage)
- Indexes GIN sur tags + (tenant_id, scope) déjà en place
- Trigger `updated_at` auto

### Adaptations à prévoir pour Phase 2

Aucune modification de schéma requise (tout est déjà là). Côté API :

1. Convention : `memory_type='fact'` pour les facts apprises automatiquement
   par les agents conversationnels (ex. "le client X préfère le matin").
2. `scope='private'` pour facts personnelles d'un agent, `scope='shared'`
   pour facts partagées au sein d'un tenant.
3. Côté `peer_bus.py`, prévoir un helper `record_fact(tenant, agent, content, importance, tags)`
   qui wrap `INSERT INTO agent_memories(tenant_id, agent_id, memory_type='fact', ...)`.
4. Index supplémentaire conseillé pour Phase 2 :
   `CREATE INDEX idx_agent_memories_type ON agent_memories(tenant_id, memory_type, importance DESC);`

### Note (à valider par Pascal)

Le champ `agent_id` dans `agent_memories` est un **integer FK vers `agents.id`**,
alors que dans le bus `agent_messages` créé en Phase 3, `from_agent`/`to_agent`
sont des **VARCHAR(80)** (slugs libres, peuvent être créés à la volée par les
configs YAML tenants). Pour la Phase 2, deux options :

- (A) Forcer chaque agent conversationnel à exister dans la table `agents`
  avant d'écrire une fact (cohérent, mais friction onboarding).
- (B) Ajouter un champ `agent_slug VARCHAR(80)` en complément de `agent_id`
  (nullable) dans `agent_memories`, pour matcher le pattern peer_bus.

Recommandation : **option B** (additive, pas de migration destructive,
permet de coexister avec le système existant des agents "officiels").

## Conclusion

Pas de nouvelle table à créer pour Phase 2. On consomme `agent_memories`.
Décision finale en attente de validation Pascal.
