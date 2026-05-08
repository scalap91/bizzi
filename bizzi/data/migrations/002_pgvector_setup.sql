-- bizzi.data — Setup pgvector (Phase 0, optionnel)
-- ════════════════════════════════════════════════════════════════
-- À exécuter (avec validation Pascal) :
--
--   sudo apt-get install -y postgresql-XX-pgvector   # XX = version pg
--   sudo systemctl restart postgresql
--   psql -U bizzi_admin -d bizzi -f /opt/bizzi/bizzi/data/migrations/002_pgvector_setup.sql
--
-- Sans ce setup, bizzi.data.memory_vector bascule en fallback ILIKE
-- (toujours fonctionnel, mais perd la similarité sémantique).
--
-- Les tables `memory_<tenant_id>` sont créées à la demande par
-- memory_vector._ensure_table() lors du premier store/search.

CREATE EXTENSION IF NOT EXISTS vector;

-- Vérification
SELECT extname, extversion
FROM pg_extension
WHERE extname = 'vector';
