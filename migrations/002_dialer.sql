-- Migration: 002_dialer.sql
-- Add dialer-specific columns and caller_ids table

-- 1. Alter leads table to add dialer-specific columns
ALTER TABLE leads ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS retry_after TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS lock_holder_id TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS callback_time TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0;

-- 2. Alter calls table to add dialer-specific columns
ALTER TABLE calls ADD COLUMN IF NOT EXISTS amd_result TEXT;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS retry_after TIMESTAMPTZ;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS dry_run BOOLEAN DEFAULT FALSE;

-- 3. Create caller_ids table
CREATE TABLE IF NOT EXISTS caller_ids (
    caller_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    daily_call_count INTEGER NOT NULL DEFAULT 0,
    answer_rate NUMERIC NOT NULL DEFAULT 0.0,
    dnc_rate NUMERIC NOT NULL DEFAULT 0.0,
    complaint_rate NUMERIC NOT NULL DEFAULT 0.0,
    stir_shaken_status TEXT,
    last_used_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    total_calls INTEGER NOT NULL DEFAULT 0,
    total_answers INTEGER NOT NULL DEFAULT 0,
    total_dncs INTEGER NOT NULL DEFAULT 0,
    total_complaints INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (caller_id, campaign_id)
);

-- 4. Create indices for fast dialer lookups
CREATE INDEX IF NOT EXISTS idx_leads_eligible ON leads(campaign_id, status, retry_after, priority);
CREATE INDEX IF NOT EXISTS idx_leads_lock_holder ON leads(lock_holder_id);
