-- Migration 008: Add dids table for Provider-Specific DID Pool and Caller ID Reputation Manager
CREATE TABLE IF NOT EXISTS dids (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    phone_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'manual',
    verified_for_provider BOOLEAN NOT NULL DEFAULT FALSE,
    stir_shaken_attestation TEXT,
    daily_cap INTEGER NOT NULL DEFAULT 100,
    hourly_cap INTEGER NOT NULL DEFAULT 20,
    calls_today INTEGER NOT NULL DEFAULT 0,
    calls_this_hour INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    spam_label_status TEXT NOT NULL DEFAULT 'clean',
    complaint_count INTEGER NOT NULL DEFAULT 0,
    dnc_count INTEGER NOT NULL DEFAULT 0,
    answer_rate DOUBLE PRECISION,
    transfer_rate DOUBLE PRECISION,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for faster query and selection in pool rotations
CREATE INDEX IF NOT EXISTS idx_dids_provider_status ON dids (provider, status);
