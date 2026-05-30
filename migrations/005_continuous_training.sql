-- Migration: 005_continuous_training.sql
-- Create continuous training system schemas with constraints and indexes

CREATE TABLE IF NOT EXISTS training_sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    title TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS training_examples (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    call_id TEXT,
    stage TEXT NOT NULL,
    user_text TEXT NOT NULL,
    ideal_response TEXT NOT NULL,
    bad_response TEXT,
    labels JSONB,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    use_for JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_cases (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    prospect_utterance TEXT NOT NULL,
    expected_behavior TEXT NOT NULL,
    must_include JSONB,
    must_not_include JSONB,
    expected_tool TEXT,
    severity TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    sha TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    qa_thresholds JSONB,
    canary_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_review_items (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    payload JSONB,
    status TEXT NOT NULL,
    reviewer TEXT,
    review_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS deployment_experiments (
    id TEXT PRIMARY KEY,
    experiment_name TEXT NOT NULL,
    prompt_version_id TEXT,
    traffic_percent DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL,
    metrics JSONB,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS call_outcome_labels (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    campaign_id TEXT,
    outcome TEXT NOT NULL,
    sold BOOLEAN,
    issued BOOLEAN,
    transfer_quality_score DOUBLE PRECISION,
    agent_feedback TEXT,
    labels JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_training_sources_source_type ON training_sources(source_type);
CREATE INDEX IF NOT EXISTS idx_training_sources_status ON training_sources(status);
CREATE INDEX IF NOT EXISTS idx_training_examples_stage ON training_examples(stage);
CREATE INDEX IF NOT EXISTS idx_training_examples_source_id ON training_examples(source_id);
CREATE INDEX IF NOT EXISTS idx_training_examples_approved_at ON training_examples(approved_at);
CREATE INDEX IF NOT EXISTS idx_eval_cases_stage ON eval_cases(stage);
CREATE INDEX IF NOT EXISTS idx_eval_cases_severity ON eval_cases(severity);
CREATE INDEX IF NOT EXISTS idx_human_review_items_status ON human_review_items(status);
CREATE INDEX IF NOT EXISTS idx_human_review_items_item_type ON human_review_items(item_type);
CREATE INDEX IF NOT EXISTS idx_deployment_experiments_status ON deployment_experiments(status);
CREATE INDEX IF NOT EXISTS idx_call_outcome_labels_call_id ON call_outcome_labels(call_id);
CREATE INDEX IF NOT EXISTS idx_call_outcome_labels_campaign_id ON call_outcome_labels(campaign_id);
CREATE INDEX IF NOT EXISTS idx_call_outcome_labels_outcome ON call_outcome_labels(outcome);
