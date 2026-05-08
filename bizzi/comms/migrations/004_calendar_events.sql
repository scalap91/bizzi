-- bizzi.comms — Migration 004 : table calendar_events
-- À exécuter manuellement (NE PAS appliquer sans validation Pascal) :
--   psql -U bizzi_admin -d bizzi -f /opt/bizzi/bizzi/comms/migrations/004_calendar_events.sql

CREATE TABLE IF NOT EXISTS calendar_events (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INT          NOT NULL,
    agent_id             INT,

    -- Provider
    provider             TEXT         NOT NULL,                 -- 'google' | 'outlook' | 'doctolib' | 'local'
    provider_event_id    TEXT,
    provider_calendar_id TEXT,                                  -- ex: email Google, mailbox Outlook

    -- Contenu
    title                TEXT         NOT NULL,
    description          TEXT,
    location             TEXT,

    start_at             TIMESTAMPTZ  NOT NULL,
    end_at               TIMESTAMPTZ  NOT NULL,
    timezone             TEXT         DEFAULT 'Europe/Paris',

    organizer_email      TEXT,
    attendees            TEXT[]       DEFAULT ARRAY[]::TEXT[],

    -- Workflow shadow-mode
    -- pending → approved → created → confirmed | cancelled | failed
    --                              ↘ rejected
    status               TEXT         NOT NULL DEFAULT 'pending',
    shadow               BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Rappels (J-1, H-1, etc.)
    reminders_minutes    INT[]        DEFAULT ARRAY[]::INT[],   -- ex: {1440, 60}
    -- Liste des rappels envoyés : [{minutes_before:1440, channel:'sms', sent_at:'…', ref_id:123, ok:true}]
    reminders_sent       JSONB        DEFAULT '[]'::jsonb,

    -- Liens / IDs externes
    html_link            TEXT,
    ical_uid             TEXT,

    -- Templates
    template_id          TEXT,
    template_context     JSONB        DEFAULT '{}'::jsonb,

    error                TEXT,
    metadata             JSONB        DEFAULT '{}'::jsonb,

    created_by           TEXT,
    approved_by          TEXT,
    approved_at          TIMESTAMPTZ,
    cancelled_at         TIMESTAMPTZ,
    cancelled_by         TEXT,

    created_at           TIMESTAMPTZ  DEFAULT now(),
    updated_at           TIMESTAMPTZ  DEFAULT now(),

    CONSTRAINT calendar_events_status_chk CHECK (status IN (
        'pending','approved','created','confirmed','cancelled','failed','rejected'
    )),
    CONSTRAINT calendar_events_time_chk CHECK (end_at > start_at)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_tenant     ON calendar_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_calendar_events_status     ON calendar_events(status);
CREATE INDEX IF NOT EXISTS idx_calendar_events_start      ON calendar_events(tenant_id, start_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_provider_id ON calendar_events(provider, provider_event_id)
    WHERE provider_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_calendar_events_pending    ON calendar_events(tenant_id, created_at DESC)
    WHERE status = 'pending';
-- Index pour scan reminders : événements futurs status actif
CREATE INDEX IF NOT EXISTS idx_calendar_events_active_future ON calendar_events(tenant_id, start_at)
    WHERE status IN ('created','confirmed') AND start_at > now();

COMMENT ON TABLE  calendar_events IS 'Queue + journal des événements calendrier Bizzi (shadow-mode + rappels J-1)';
COMMENT ON COLUMN calendar_events.shadow            IS 'Si TRUE : ne sera jamais créé chez le provider tant que approved_by=NULL';
COMMENT ON COLUMN calendar_events.reminders_minutes IS 'Liste minutes avant start_at pour déclencher rappel';
COMMENT ON COLUMN calendar_events.reminders_sent    IS 'Trace [{minutes_before, channel, sent_at, ref_id, ok}]';
