-- Migration 003: Integrations Webhook Events Table
-- Idempotent setup for the persistent integration outbox

CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_id TEXT UNIQUE NOT NULL,
    destination TEXT,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    response_status_code INTEGER,
    response_body_preview TEXT,
    sent_at TIMESTAMPTZ,
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status_next_attempt_at ON webhook_events (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_event_id ON webhook_events (event_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_created_at ON webhook_events (created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_event_type ON webhook_events (event_type);
CREATE INDEX IF NOT EXISTS idx_webhook_events_payload_gin ON webhook_events USING gin (payload);
