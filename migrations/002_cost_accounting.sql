-- Migration: 002_cost_accounting.sql
-- Create cost rate cards, provider decisions, turn latency spans, GPU allocations, outcome costs, and rollups

CREATE TABLE IF NOT EXISTS cost_rate_cards (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    component TEXT NOT NULL,
    model TEXT,
    unit_rate NUMERIC NOT NULL,
    usage_unit TEXT NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_to TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS provider_decisions (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    component TEXT NOT NULL,
    selected_provider TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    latency_ms NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS turn_latency_spans (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    component TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    latency_ms NUMERIC NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gpu_runtime_allocations (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    component TEXT NOT NULL,
    gpu_device_id TEXT,
    runtime_seconds NUMERIC NOT NULL,
    hourly_rate NUMERIC NOT NULL,
    allocated_cost NUMERIC NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS call_outcome_costs (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    telephony_cost NUMERIC NOT NULL DEFAULT 0.0,
    stt_cost NUMERIC NOT NULL DEFAULT 0.0,
    llm_cost NUMERIC NOT NULL DEFAULT 0.0,
    tts_cost NUMERIC NOT NULL DEFAULT 0.0,
    gpu_cost NUMERIC NOT NULL DEFAULT 0.0,
    total_cost NUMERIC NOT NULL DEFAULT 0.0,
    is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaign_cost_rollups (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    total_calls INTEGER NOT NULL DEFAULT 0,
    total_duration_seconds NUMERIC NOT NULL DEFAULT 0.0,
    total_telephony_cost NUMERIC NOT NULL DEFAULT 0.0,
    total_stt_cost NUMERIC NOT NULL DEFAULT 0.0,
    total_llm_cost NUMERIC NOT NULL DEFAULT 0.0,
    total_tts_cost NUMERIC NOT NULL DEFAULT 0.0,
    total_gpu_cost NUMERIC NOT NULL DEFAULT 0.0,
    total_cost NUMERIC NOT NULL DEFAULT 0.0,
    average_call_cost NUMERIC NOT NULL DEFAULT 0.0,
    rollup_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_campaign_cost_rollups UNIQUE (campaign_id, outcome)
);

CREATE INDEX IF NOT EXISTS idx_cost_rate_cards_prov_comp ON cost_rate_cards(provider, component);
CREATE INDEX IF NOT EXISTS idx_provider_decisions_call ON provider_decisions(call_id);
CREATE INDEX IF NOT EXISTS idx_turn_latency_spans_call ON turn_latency_spans(call_id);
CREATE INDEX IF NOT EXISTS idx_gpu_runtime_alloc_call ON gpu_runtime_allocations(call_id);
CREATE INDEX IF NOT EXISTS idx_call_outcome_costs_campaign ON call_outcome_costs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_cost_rollups_campaign ON campaign_cost_rollups(campaign_id);
