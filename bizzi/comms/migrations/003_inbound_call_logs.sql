-- bizzi.comms — Migration 003 : table inbound_call_logs
-- À exécuter manuellement (NE PAS appliquer sans validation Pascal) :
--   psql -U bizzi_admin -d bizzi -f /opt/bizzi/bizzi/comms/migrations/003_inbound_call_logs.sql
--
-- Note : table dédiée pour les appels ENTRANTS gérés par bizzi.comms.inbound.
-- Cohabite avec la table `calls` de bizzi.phone (sortants) — schémas distincts
-- par design (inbound a un qualifier IA + actions de routing différentes).

CREATE TABLE IF NOT EXISTS inbound_call_logs (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INT          NOT NULL,
    agent_id            INT,

    -- Provider
    provider            TEXT         NOT NULL,                -- 'vapi' | 'twilio'
    provider_call_id    TEXT,

    -- Numéros (E.164)
    from_phone          TEXT,                                 -- l'appelant
    to_phone            TEXT,                                 -- numéro tenant appelé
    caller_name         TEXT,

    -- Statut workflow
    -- received → answered → in_progress → completed | failed | missed | voicemail
    status              TEXT         NOT NULL DEFAULT 'received',

    started_at          TIMESTAMPTZ,
    answered_at         TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    duration_seconds    INT,

    -- Contenu
    recording_url       TEXT,
    transcript          JSONB        DEFAULT '[]'::jsonb,
    summary             TEXT,

    -- Qualification (renseignée après l'appel par le qualifier IA)
    -- intent : rdv | renseignement | urgence | reclamation | autre
    -- suggested_action : transfer | rdv | sms_confirm | mail_summary | ticket
    intent              TEXT,
    urgency             INT          DEFAULT 0,
    suggested_action    TEXT,
    extracted           JSONB        DEFAULT '{}'::jsonb,
    confidence          NUMERIC(4,3),
    requires_human      BOOLEAN      DEFAULT FALSE,

    -- Actions effectuées en post-traitement (liste de {type, ref_id, status})
    actions             JSONB        DEFAULT '[]'::jsonb,

    -- Coût provider
    cost_eur            NUMERIC(10,4) DEFAULT 0,

    error               TEXT,
    metadata            JSONB        DEFAULT '{}'::jsonb,

    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now(),

    CONSTRAINT inbound_call_logs_status_chk CHECK (status IN (
        'received','answered','in_progress','completed','failed','missed','voicemail'
    )),
    CONSTRAINT inbound_call_logs_intent_chk CHECK (
        intent IS NULL OR intent IN ('rdv','renseignement','urgence','reclamation','autre')
    ),
    CONSTRAINT inbound_call_logs_action_chk CHECK (
        suggested_action IS NULL OR suggested_action IN
            ('transfer','rdv','sms_confirm','mail_summary','ticket')
    )
);

CREATE INDEX IF NOT EXISTS idx_inbound_calls_tenant       ON inbound_call_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_inbound_calls_status       ON inbound_call_logs(status);
CREATE INDEX IF NOT EXISTS idx_inbound_calls_provider_id  ON inbound_call_logs(provider, provider_call_id)
    WHERE provider_call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inbound_calls_from_phone   ON inbound_call_logs(tenant_id, from_phone, started_at DESC)
    WHERE from_phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inbound_calls_intent       ON inbound_call_logs(tenant_id, intent, started_at DESC)
    WHERE intent IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inbound_calls_requires_human ON inbound_call_logs(tenant_id, started_at DESC)
    WHERE requires_human = TRUE;

COMMENT ON TABLE  inbound_call_logs IS 'Journal des appels téléphoniques entrants Bizzi (IVR + agent IA + qualification + routing)';
COMMENT ON COLUMN inbound_call_logs.transcript IS 'Array [{role: user|assistant, text, timestamp}]';
COMMENT ON COLUMN inbound_call_logs.actions    IS 'Array [{type: sms_sent|mail_sent|ticket|transfer_failed, ref_id, status}]';
COMMENT ON COLUMN inbound_call_logs.extracted  IS 'Facts extraits par le qualifier IA (nom, demande, contact, etc.)';
