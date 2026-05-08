-- bizzi.comms — Migration 001 : table sms_logs
-- À exécuter manuellement (NE PAS appliquer sans validation Pascal) :
--   psql -U bizzi_admin -d bizzi -f /opt/bizzi/bizzi/comms/migrations/001_sms_logs.sql

CREATE TABLE IF NOT EXISTS sms_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INT          NOT NULL,
    agent_id            INT,

    -- Destination & contenu
    to_phone            TEXT         NOT NULL,
    sender_id           TEXT,                                -- alphanum ou MSISDN selon provider
    body                TEXT         NOT NULL,
    template_id         TEXT,
    template_context    JSONB        DEFAULT '{}'::jsonb,

    -- Provider
    provider            TEXT         NOT NULL,               -- 'twilio' | 'brevo' | 'ovh'
    provider_message_id TEXT,

    -- Workflow shadow-mode (pattern social_posts)
    -- pending → approved → queued → sent → delivered | failed
    --                                    ↘ rejected
    status              TEXT         NOT NULL DEFAULT 'pending',
    shadow              BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Coût & segments
    cost_eur            NUMERIC(10,4) DEFAULT 0,
    segments            INT          DEFAULT 1,

    error               TEXT,

    scheduled_at        TIMESTAMPTZ,
    sent_at             TIMESTAMPTZ,
    delivered_at        TIMESTAMPTZ,

    metadata            JSONB        DEFAULT '{}'::jsonb,

    created_by          TEXT,
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,

    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now(),

    CONSTRAINT sms_logs_status_chk
        CHECK (status IN ('pending','approved','queued','sent','delivered','failed','rejected'))
);

CREATE INDEX IF NOT EXISTS idx_sms_logs_tenant       ON sms_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sms_logs_agent        ON sms_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_sms_logs_status       ON sms_logs(status);
CREATE INDEX IF NOT EXISTS idx_sms_logs_to_phone     ON sms_logs(tenant_id, to_phone, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_logs_pending      ON sms_logs(tenant_id, created_at DESC)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_sms_logs_provider_id  ON sms_logs(provider, provider_message_id)
    WHERE provider_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sms_logs_scheduled    ON sms_logs(scheduled_at)
    WHERE status = 'approved';

COMMENT ON TABLE  sms_logs IS 'Queue + journal des SMS sortants Bizzi (shadow-mode workflow)';
COMMENT ON COLUMN sms_logs.shadow            IS 'Si TRUE : ne sera jamais envoyé tant que approved_by=NULL';
COMMENT ON COLUMN sms_logs.template_context  IS 'Variables passées au template au moment du rendu';
COMMENT ON COLUMN sms_logs.metadata          IS 'use_case, raw provider response, dlr details, etc.';
