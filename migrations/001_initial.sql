-- =======================================================================
-- 001_initial.sql — Initial Postgres schema for Dana voice agent
--
-- Run once against the target database:
--   psql "$DATABASE_URL" -f migrations/001_initial.sql
-- =======================================================================

-- Enable pgvector extension for future RAG / embedding search.
CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------------------------------------------------
-- leads — point-in-time snapshots of lead qualification data
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id          TEXT PRIMARY KEY,
    call_id     TEXT        NOT NULL,
    lead_profile JSONB      NOT NULL DEFAULT '{}'::jsonb,
    stage       TEXT        NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leads_call_id   ON leads (call_id);
CREATE INDEX IF NOT EXISTS idx_leads_timestamp  ON leads (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_leads_stage      ON leads (stage);

-- -----------------------------------------------------------------------
-- call_turns — individual conversational turns
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_turns (
    id          TEXT PRIMARY KEY,
    call_id     TEXT    NOT NULL,
    turn_number INTEGER NOT NULL,
    speaker     TEXT    NOT NULL CHECK (speaker IN ('user', 'agent')),
    text        TEXT    NOT NULL,
    stage       TEXT    NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_call_turns_call_id   ON call_turns (call_id);
CREATE INDEX IF NOT EXISTS idx_call_turns_timestamp  ON call_turns (timestamp DESC);

-- -----------------------------------------------------------------------
-- tool_events — tool invocations during a call
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_events (
    id          TEXT PRIMARY KEY,
    call_id     TEXT    NOT NULL,
    tool_name   TEXT    NOT NULL,
    params      JSONB   NOT NULL DEFAULT '{}'::jsonb,
    result      JSONB,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tool_events_call_id   ON tool_events (call_id);
CREATE INDEX IF NOT EXISTS idx_tool_events_tool_name ON tool_events (tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_events_timestamp  ON tool_events (timestamp DESC);

-- -----------------------------------------------------------------------
-- qa_reports — quality-assurance scorecards per call
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qa_reports (
    id              TEXT PRIMARY KEY,
    call_id         TEXT    NOT NULL,
    scores          JSONB   NOT NULL DEFAULT '{}'::jsonb,
    issues          JSONB   NOT NULL DEFAULT '[]'::jsonb,
    recommendations JSONB   NOT NULL DEFAULT '[]'::jsonb,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qa_reports_call_id   ON qa_reports (call_id);
CREATE INDEX IF NOT EXISTS idx_qa_reports_timestamp  ON qa_reports (timestamp DESC);

-- -----------------------------------------------------------------------
-- training_notes — coaching / training observations
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_notes (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    topic        TEXT NOT NULL,
    sales_lesson TEXT NOT NULL,
    good_example TEXT,
    bad_example  TEXT,
    call_stage   TEXT,
    timestamp    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_training_notes_topic     ON training_notes (topic);
CREATE INDEX IF NOT EXISTS idx_training_notes_call_stage ON training_notes (call_stage);
CREATE INDEX IF NOT EXISTS idx_training_notes_timestamp   ON training_notes (timestamp DESC);
