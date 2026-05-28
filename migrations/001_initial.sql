-- Migration: 001_initial.sql
-- Create initial schemas for all required tables

-- 1. Schema Migrations table
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Campaigns table
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    campaign_id TEXT UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    config JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Leads table
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    lead_id TEXT,
    phone_e164 TEXT,
    campaign_id TEXT,
    consent_artifact_id TEXT,
    source_vendor TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT,
    payload JSONB
);

-- 4. Calls table
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    call_id TEXT UNIQUE,
    lead_id TEXT,
    campaign_id TEXT,
    phone_e164 TEXT,
    caller_id TEXT,
    started_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds NUMERIC,
    outcome TEXT,
    recording_url TEXT,
    transcript JSONB,
    qualification JSONB,
    compliance_flags JSONB,
    latency_summary JSONB,
    qa_score NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Call Turns table
CREATE TABLE IF NOT EXISTS call_turns (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    turn_number INTEGER,
    speaker TEXT,
    text TEXT,
    stage TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Call Events table
CREATE TABLE IF NOT EXISTS call_events (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    event_type TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. Tool Events table
CREATE TABLE IF NOT EXISTS tool_events (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    tool_name TEXT,
    params JSONB,
    result JSONB,
    success BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. Transfers table
CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    lead_id TEXT,
    transfer_mode TEXT,
    agent_id TEXT,
    target_phone TEXT,
    success BOOLEAN,
    failure_reason TEXT,
    provider_call_id TEXT,
    summary JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. Callbacks table
CREATE TABLE IF NOT EXISTS callbacks (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    lead_id TEXT,
    phone_e164 TEXT,
    callback_time_local TEXT,
    callback_timezone TEXT,
    status TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 10. DNC Requests table
CREATE TABLE IF NOT EXISTS dnc_requests (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    lead_id TEXT,
    phone_e164 TEXT,
    campaign_id TEXT,
    reason TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 11. Consent Records table
CREATE TABLE IF NOT EXISTS consent_records (
    id TEXT PRIMARY KEY,
    consent_artifact_id TEXT,
    lead_id TEXT,
    phone_e164 TEXT,
    source_vendor TEXT,
    consent_text TEXT,
    consent_timestamp TIMESTAMPTZ,
    landing_page_url TEXT,
    ip_address TEXT,
    user_agent TEXT,
    tcpa_consent_version TEXT,
    campaign_id TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 12. QA Reports table
CREATE TABLE IF NOT EXISTS qa_reports (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    overall_score NUMERIC,
    grade TEXT,
    scores JSONB,
    issues JSONB,
    recommendations JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 13. Latency Metrics table
CREATE TABLE IF NOT EXISTS latency_metrics (
    id TEXT PRIMARY KEY,
    call_id TEXT,
    metric_name TEXT,
    metric_value_ms NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 14. Agent Availability table
CREATE TABLE IF NOT EXISTS agent_availability (
    id TEXT PRIMARY KEY,
    agent_id TEXT UNIQUE,
    name TEXT,
    phone_number TEXT,
    licensed_states JSONB,
    status TEXT,
    priority INTEGER,
    max_concurrent_calls INTEGER,
    current_call_count INTEGER,
    last_call_at TIMESTAMPTZ,
    browser_join_enabled BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 15. Training Notes table
CREATE TABLE IF NOT EXISTS training_notes (
    id TEXT PRIMARY KEY,
    source TEXT,
    topic TEXT,
    sales_lesson TEXT,
    good_example TEXT,
    bad_example TEXT,
    call_stage TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- Standard Indexes for fast lookups
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_campaigns_campaign_id ON campaigns(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_created_at ON campaigns(created_at);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

CREATE INDEX IF NOT EXISTS idx_leads_phone_e164 ON leads(phone_e164);
CREATE INDEX IF NOT EXISTS idx_leads_lead_id ON leads(lead_id);
CREATE INDEX IF NOT EXISTS idx_leads_campaign_id ON leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

CREATE INDEX IF NOT EXISTS idx_calls_call_id ON calls(call_id);
CREATE INDEX IF NOT EXISTS idx_calls_lead_id ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_campaign_id ON calls(campaign_id);
CREATE INDEX IF NOT EXISTS idx_calls_phone_e164 ON calls(phone_e164);
CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at);
CREATE INDEX IF NOT EXISTS idx_calls_outcome ON calls(outcome);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(outcome); -- outcome acts as call status

CREATE INDEX IF NOT EXISTS idx_call_turns_call_id ON call_turns(call_id);
CREATE INDEX IF NOT EXISTS idx_call_turns_created_at ON call_turns(created_at);

CREATE INDEX IF NOT EXISTS idx_call_events_call_id ON call_events(call_id);
CREATE INDEX IF NOT EXISTS idx_call_events_created_at ON call_events(created_at);

CREATE INDEX IF NOT EXISTS idx_tool_events_call_id ON tool_events(call_id);
CREATE INDEX IF NOT EXISTS idx_tool_events_created_at ON tool_events(created_at);

CREATE INDEX IF NOT EXISTS idx_transfers_call_id ON transfers(call_id);
CREATE INDEX IF NOT EXISTS idx_transfers_lead_id ON transfers(lead_id);
CREATE INDEX IF NOT EXISTS idx_transfers_created_at ON transfers(created_at);

CREATE INDEX IF NOT EXISTS idx_callbacks_call_id ON callbacks(call_id);
CREATE INDEX IF NOT EXISTS idx_callbacks_lead_id ON callbacks(lead_id);
CREATE INDEX IF NOT EXISTS idx_callbacks_phone_e164 ON callbacks(phone_e164);
CREATE INDEX IF NOT EXISTS idx_callbacks_created_at ON callbacks(created_at);
CREATE INDEX IF NOT EXISTS idx_callbacks_status ON callbacks(status);

CREATE INDEX IF NOT EXISTS idx_dnc_requests_call_id ON dnc_requests(call_id);
CREATE INDEX IF NOT EXISTS idx_dnc_requests_lead_id ON dnc_requests(lead_id);
CREATE INDEX IF NOT EXISTS idx_dnc_requests_phone_e164 ON dnc_requests(phone_e164);
CREATE INDEX IF NOT EXISTS idx_dnc_requests_campaign_id ON dnc_requests(campaign_id);
CREATE INDEX IF NOT EXISTS idx_dnc_requests_requested_at ON dnc_requests(requested_at);

CREATE INDEX IF NOT EXISTS idx_consent_records_lead_id ON consent_records(lead_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_phone_e164 ON consent_records(phone_e164);
CREATE INDEX IF NOT EXISTS idx_consent_records_campaign_id ON consent_records(campaign_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_consent_artifact_id ON consent_records(consent_artifact_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_created_at ON consent_records(created_at);

CREATE INDEX IF NOT EXISTS idx_qa_reports_call_id ON qa_reports(call_id);
CREATE INDEX IF NOT EXISTS idx_qa_reports_created_at ON qa_reports(created_at);

CREATE INDEX IF NOT EXISTS idx_latency_metrics_call_id ON latency_metrics(call_id);
CREATE INDEX IF NOT EXISTS idx_latency_metrics_created_at ON latency_metrics(created_at);

CREATE INDEX IF NOT EXISTS idx_agent_availability_agent_id ON agent_availability(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_availability_created_at ON agent_availability(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_availability_status ON agent_availability(status);

CREATE INDEX IF NOT EXISTS idx_training_notes_created_at ON training_notes(created_at);

-- -----------------------------------------------------------------------------
-- JSONB GIN Indexes for high-performance JSON queries
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_leads_payload_gin ON leads USING gin (payload);
CREATE INDEX IF NOT EXISTS idx_calls_qualification_gin ON calls USING gin (qualification);
CREATE INDEX IF NOT EXISTS idx_calls_compliance_flags_gin ON calls USING gin (compliance_flags);
CREATE INDEX IF NOT EXISTS idx_qa_reports_scores_gin ON qa_reports USING gin (scores);
CREATE INDEX IF NOT EXISTS idx_qa_reports_issues_gin ON qa_reports USING gin (issues);
