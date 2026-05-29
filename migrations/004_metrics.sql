-- Migration: 004_metrics.sql
-- Create call costs and daily outcome rollups with constraints and indexes

CREATE TABLE IF NOT EXISTS call_costs (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    campaign_id TEXT,
    component TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'unknown',
    model TEXT NOT NULL DEFAULT 'unknown',
    usage_unit TEXT,
    usage_quantity NUMERIC,
    unit_rate NUMERIC,
    estimated_cost NUMERIC,
    currency TEXT,
    rate_source TEXT,
    estimated BOOLEAN,
    dry_run BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_call_costs UNIQUE (call_id, component, provider, model)
);

CREATE TABLE IF NOT EXISTS outcome_metrics (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    metric_date DATE NOT NULL,
    total_dialed INTEGER DEFAULT 0,
    answered INTEGER DEFAULT 0,
    human_answered INTEGER DEFAULT 0,
    voicemail INTEGER DEFAULT 0,
    no_answer INTEGER DEFAULT 0,
    busy INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    open_to_review INTEGER DEFAULT 0,
    qualified INTEGER DEFAULT 0,
    transferred INTEGER DEFAULT 0,
    callback INTEGER DEFAULT 0,
    dnc INTEGER DEFAULT 0,
    disqualified INTEGER DEFAULT 0,
    cost NUMERIC DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_outcome_metrics UNIQUE (campaign_id, metric_date)
);

CREATE INDEX IF NOT EXISTS idx_call_costs_call_id ON call_costs(call_id);
CREATE INDEX IF NOT EXISTS idx_call_costs_campaign_id ON call_costs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_call_costs_created_at ON call_costs(created_at);
CREATE INDEX IF NOT EXISTS idx_call_costs_component ON call_costs(component);
CREATE INDEX IF NOT EXISTS idx_outcome_metrics_campaign_date ON outcome_metrics(campaign_id, metric_date);
