-- Sovereign Voice Stack - Telephony Campaign schemas
-- Telnyx + LiveKit Outbound Control Layer

CREATE TABLE IF NOT EXISTS telephony_provider_configs (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'telnyx_livekit',
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    telnyx_connection_id TEXT,
    telnyx_sip_trunk_name TEXT,
    telnyx_phone_numbers JSONB,
    livekit_url TEXT,
    livekit_sip_outbound_trunk_id TEXT,
    livekit_sip_inbound_trunk_id TEXT,
    livekit_dispatch_rule_id TEXT,
    room_name_template TEXT NOT NULL DEFAULT 'dana-{campaign_id}-{lead_id}-{attempt_id}',
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbound_campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    campaign_type TEXT NOT NULL DEFAULT 'final_expense_outbound',
    provider_config_id TEXT REFERENCES telephony_provider_configs(id),
    prompt_name TEXT NOT NULL DEFAULT 'final_expense_alex',
    max_concurrent_calls INTEGER NOT NULL DEFAULT 1,
    daily_call_cap INTEGER NOT NULL DEFAULT 100,
    calls_started_today INTEGER NOT NULL DEFAULT 0,
    timezone TEXT NOT NULL DEFAULT 'America/New_York',
    calling_window_start TEXT NOT NULL DEFAULT '09:30',
    calling_window_end TEXT NOT NULL DEFAULT '18:00',
    allowed_days JSONB,
    retry_policy JSONB,
    transfer_phone_number TEXT,
    caller_id TEXT,
    compliance_mode TEXT NOT NULL DEFAULT 'strict',
    dnc_scrub_required BOOLEAN NOT NULL DEFAULT TRUE,
    require_live_mode BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TEXT,
    paused_at TEXT,
    stopped_at TEXT
);

CREATE TABLE IF NOT EXISTS campaign_leads (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES outbound_campaigns(id) ON DELETE CASCADE,
    first_name TEXT,
    last_name TEXT,
    phone_number TEXT NOT NULL,
    state TEXT,
    timezone TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    outcome TEXT,
    suppression_reason TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS call_attempts (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES outbound_campaigns(id) ON DELETE CASCADE,
    lead_id TEXT NOT NULL REFERENCES campaign_leads(id) ON DELETE CASCADE,
    provider_config_id TEXT REFERENCES telephony_provider_configs(id),
    status TEXT NOT NULL DEFAULT 'queued',
    phone_number_redacted TEXT,
    phone_number_hash TEXT,
    livekit_room_name TEXT,
    livekit_participant_id TEXT,
    livekit_sip_call_id TEXT,
    provider_call_id TEXT,
    started_at TEXT,
    answered_at TEXT,
    ended_at TEXT,
    duration_seconds INTEGER,
    outcome TEXT,
    failure_reason TEXT,
    transfer_consent BOOLEAN NOT NULL DEFAULT FALSE,
    transfer_attempted BOOLEAN NOT NULL DEFAULT FALSE,
    transfer_successful BOOLEAN NOT NULL DEFAULT FALSE,
    post_call_export_path TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS live_call_sessions (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES outbound_campaigns(id) ON DELETE CASCADE,
    lead_id TEXT NOT NULL REFERENCES campaign_leads(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL REFERENCES call_attempts(id) ON DELETE CASCADE,
    call_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'starting',
    current_stage TEXT,
    latest_transcript TEXT,
    compliance_warnings JSONB,
    livekit_room_name TEXT,
    participant_identity TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TEXT,
    outcome TEXT,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS campaign_control_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES outbound_campaigns(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    operator TEXT,
    reason TEXT,
    previous_status TEXT,
    new_status TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast lookup
CREATE INDEX IF NOT EXISTS idx_campaign_leads_campaign_id ON campaign_leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_leads_phone_number ON campaign_leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_call_attempts_campaign_id ON call_attempts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_call_attempts_lead_id ON call_attempts(lead_id);
CREATE INDEX IF NOT EXISTS idx_live_call_sessions_attempt_id ON live_call_sessions(attempt_id);
CREATE INDEX IF NOT EXISTS idx_campaign_control_events_campaign_id ON campaign_control_events(campaign_id);
