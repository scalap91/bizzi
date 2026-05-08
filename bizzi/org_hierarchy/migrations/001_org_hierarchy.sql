-- ════════════════════════════════════════════════════════════════════════════
-- bizzi.org_hierarchy — Phase 0 schema
-- ════════════════════════════════════════════════════════════════════════════
-- ⚠️  À VALIDER PAR PASCAL AVANT EXÉCUTION
--
-- Tables :
--   org_units        : nœuds de la hiérarchie (multi-tenant)
--   geo_mapping      : résolution ville → org_unit
--   org_aggregations : rollup local → global (alimenté par cron Phase 1)
--   org_broadcasts   : push global → local (Phase 1)
--   org_audit_log    : conformité requêtes embed (retention 90j)
--
-- ALTER :
--   audience_reports ADD org_unit_id (coordination avec bizzi-audience —
--   un seul ALTER, à valider conjointement)
--
-- Idempotent : utilise IF NOT EXISTS partout. Peut être ré-exécuté.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── org_units ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS org_units (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    parent_id       INT REFERENCES org_units(id) ON DELETE SET NULL,
    level           TEXT NOT NULL,
    level_order     INT NOT NULL,
    name            TEXT NOT NULL,
    external_id     TEXT,
    geo_meta        JSONB,
    contact_email   TEXT,
    responsible     TEXT,
    metadata        JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Unicité (tenant_id, external_id) pour upsert idempotent depuis YAML.
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_units_tenant_external
    ON org_units (tenant_id, external_id)
    WHERE external_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_org_units_tenant_level
    ON org_units (tenant_id, level);

CREATE INDEX IF NOT EXISTS ix_org_units_parent
    ON org_units (parent_id);

-- ─── geo_mapping ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS geo_mapping (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    city            TEXT NOT NULL,
    postal_code     TEXT,
    org_unit_id     INT NOT NULL REFERENCES org_units(id) ON DELETE CASCADE,
    UNIQUE (tenant_id, city)
);

CREATE INDEX IF NOT EXISTS ix_geo_mapping_tenant_city
    ON geo_mapping (tenant_id, LOWER(city));

CREATE INDEX IF NOT EXISTS ix_geo_mapping_unit
    ON geo_mapping (org_unit_id);

-- ─── audience_reports.org_unit_id (coordination bizzi-audience) ─────────────

ALTER TABLE audience_reports
    ADD COLUMN IF NOT EXISTS org_unit_id INT REFERENCES org_units(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_audience_reports_org_unit
    ON audience_reports (org_unit_id);

-- ─── org_aggregations (Phase 1 — rollup) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS org_aggregations (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    org_unit_id     INT NOT NULL REFERENCES org_units(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    period          TEXT NOT NULL,
    total_mentions  INT,
    trend_pct       FLOAT,
    top_keywords    TEXT[],
    emotion_dom     TEXT,
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, org_unit_id, category, period)
);

CREATE INDEX IF NOT EXISTS ix_org_aggregations_tenant_period
    ON org_aggregations (tenant_id, period);

-- ─── org_broadcasts (Phase 1 — push global → local) ─────────────────────────

CREATE TABLE IF NOT EXISTS org_broadcasts (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_unit_id  INT REFERENCES org_units(id) ON DELETE SET NULL,
    target_filter   JSONB,
    content_type    TEXT,
    title           TEXT,
    payload         JSONB,
    status          TEXT DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_org_broadcasts_tenant_status
    ON org_broadcasts (tenant_id, status);

-- ─── org_audit_log (conformité embed iframe) ────────────────────────────────

CREATE TABLE IF NOT EXISTS org_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role            TEXT,
    user_id         TEXT,
    org_unit_id     INT REFERENCES org_units(id) ON DELETE SET NULL,
    path            TEXT NOT NULL,
    method          TEXT NOT NULL,
    ip              INET,
    query           JSONB,
    status_code     INT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_org_audit_log_tenant_time
    ON org_audit_log (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_org_audit_log_user_time
    ON org_audit_log (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;

-- Note retention : purge des entrées > 90j à implémenter en Phase 1 via cron
-- (DELETE FROM org_audit_log WHERE created_at < NOW() - INTERVAL '90 days').

COMMIT;
