-- bizzi.social — Migration 001 : table social_posts
-- À exécuter manuellement (NE PAS appliquer sans validation Pascal) :
--   psql -U bizzi_admin -d bizzi -f /opt/bizzi/bizzi/social/migrations/001_social_posts.sql

CREATE TABLE IF NOT EXISTS social_posts (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INT          NOT NULL,
    agent_id           INT,
    networks           TEXT[]       NOT NULL,
    video_url          TEXT,
    thumbnail_url      TEXT,
    caption            TEXT,
    hashtags           TEXT[]       DEFAULT ARRAY[]::TEXT[],
    template_id        TEXT,
    context            JSONB        DEFAULT '{}'::jsonb,

    -- Statut workflow shadow-mode :
    -- pending  → approved → posting → posted | failed
    --                     ↘ rejected
    status             TEXT         NOT NULL DEFAULT 'pending',
    shadow             BOOLEAN      NOT NULL DEFAULT TRUE,

    scheduled_at       TIMESTAMPTZ,
    posted_at          TIMESTAMPTZ,

    -- Multi-réseau : ID + URL retournés par chaque provider
    provider_post_ids  JSONB        DEFAULT '{}'::jsonb,  -- {"tiktok": "abc", "instagram": "xyz"}
    post_urls          JSONB        DEFAULT '{}'::jsonb,

    -- Métriques agrégées (rafraîchies par scheduler.fetch_metrics)
    views              BIGINT       DEFAULT 0,
    likes              BIGINT       DEFAULT 0,
    comments           BIGINT       DEFAULT 0,
    shares             BIGINT       DEFAULT 0,
    metrics            JSONB        DEFAULT '{}'::jsonb,  -- détail par réseau

    error              TEXT,
    created_by         TEXT,
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,

    created_at         TIMESTAMPTZ  DEFAULT now(),
    updated_at         TIMESTAMPTZ  DEFAULT now(),

    CONSTRAINT social_posts_status_chk
        CHECK (status IN ('pending','approved','posting','posted','failed','rejected'))
);

CREATE INDEX IF NOT EXISTS idx_social_posts_tenant       ON social_posts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_agent        ON social_posts(agent_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_status       ON social_posts(status);
CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled    ON social_posts(scheduled_at)
    WHERE status = 'approved';
CREATE INDEX IF NOT EXISTS idx_social_posts_pending      ON social_posts(tenant_id, created_at DESC)
    WHERE status = 'pending';

COMMENT ON TABLE  social_posts IS 'Queue + journal des publications réseaux sociaux Bizzi (shadow-mode workflow)';
COMMENT ON COLUMN social_posts.shadow            IS 'Si TRUE : ne sera jamais posté tant que approved_by=NULL';
COMMENT ON COLUMN social_posts.provider_post_ids IS 'Map network → ID retourné par le provider';
COMMENT ON COLUMN social_posts.context           IS 'Variables passées au template (origin, destination, title, …)';
