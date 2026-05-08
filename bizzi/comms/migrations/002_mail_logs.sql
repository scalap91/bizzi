-- bizzi.comms — Migration 002 : table mail_logs
-- À exécuter manuellement (NE PAS appliquer sans validation Pascal) :
--   psql -U bizzi_admin -d bizzi -f /opt/bizzi/bizzi/comms/migrations/002_mail_logs.sql

CREATE TABLE IF NOT EXISTS mail_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INT          NOT NULL,
    agent_id            INT,

    -- Destinataires
    to_addrs            TEXT[]       NOT NULL,
    cc_addrs            TEXT[]       DEFAULT ARRAY[]::TEXT[],
    bcc_addrs           TEXT[]       DEFAULT ARRAY[]::TEXT[],
    from_email          TEXT,
    from_name           TEXT,
    reply_to            TEXT,

    -- Contenu
    subject             TEXT         NOT NULL,
    html                TEXT,
    text                TEXT,
    template_id         TEXT,
    template_context    JSONB        DEFAULT '{}'::jsonb,
    -- attachments_meta : [{filename, content_type, size_bytes}] — on ne stocke pas le binaire
    attachments_meta    JSONB        DEFAULT '[]'::jsonb,
    has_attachments     BOOLEAN      DEFAULT FALSE,

    -- Provider
    provider            TEXT         NOT NULL,                -- 'brevo' | 'sendgrid' | 'mailgun' | 'ses'
    provider_message_id TEXT,

    -- Workflow shadow-mode (pattern social_posts / sms_logs)
    -- pending → approved → queued → sent → delivered | bounced | complained | failed
    --                                    ↘ rejected | unsubscribed
    status              TEXT         NOT NULL DEFAULT 'pending',
    shadow              BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Tracking
    track_opens         BOOLEAN      DEFAULT TRUE,
    track_clicks        BOOLEAN      DEFAULT TRUE,
    opens               INT          DEFAULT 0,
    clicks              INT          DEFAULT 0,
    last_open_at        TIMESTAMPTZ,
    last_click_at       TIMESTAMPTZ,

    -- Coût (Brevo : crédits ; SendGrid : NULL ; on stocke notre estimation)
    cost_eur            NUMERIC(10,4) DEFAULT 0,

    error               TEXT,

    scheduled_at        TIMESTAMPTZ,
    sent_at             TIMESTAMPTZ,
    delivered_at        TIMESTAMPTZ,
    bounced_at          TIMESTAMPTZ,

    metadata            JSONB        DEFAULT '{}'::jsonb,

    created_by          TEXT,
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,

    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now(),

    CONSTRAINT mail_logs_status_chk
        CHECK (status IN (
            'pending','approved','queued','sent','delivered',
            'bounced','complained','failed','rejected','unsubscribed'
        ))
);

CREATE INDEX IF NOT EXISTS idx_mail_logs_tenant       ON mail_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_mail_logs_agent        ON mail_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_mail_logs_status       ON mail_logs(status);
CREATE INDEX IF NOT EXISTS idx_mail_logs_pending      ON mail_logs(tenant_id, created_at DESC)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_mail_logs_provider_id  ON mail_logs(provider, provider_message_id)
    WHERE provider_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mail_logs_scheduled    ON mail_logs(scheduled_at)
    WHERE status = 'approved';
-- Recherche par destinataire : on indexe le 1er to_addr (cas usage le plus courant)
CREATE INDEX IF NOT EXISTS idx_mail_logs_to1          ON mail_logs(tenant_id, (to_addrs[1]), created_at DESC);

COMMENT ON TABLE  mail_logs IS 'Queue + journal des emails sortants Bizzi (shadow-mode + tracking opens/clicks)';
COMMENT ON COLUMN mail_logs.shadow            IS 'Si TRUE : ne sera jamais envoyé tant que approved_by=NULL';
COMMENT ON COLUMN mail_logs.attachments_meta  IS 'Méta des PJ ; le binaire n''est pas stocké en DB';
COMMENT ON COLUMN mail_logs.metadata          IS 'use_case, headers custom, raw provider response, dlr details, etc.';
