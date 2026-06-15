-- Migration 013: Add metadata and live logging fields to call_turns table and call_attempts table
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS call_attempt_id TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS campaign_id TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS lead_id TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS livekit_room_name TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS participant_id TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS compliance_warnings JSONB;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS latency_metrics JSONB;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS selected_did TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS caller_id_source TEXT;
ALTER TABLE call_turns ADD COLUMN IF NOT EXISTS interrupted BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_call_status TEXT;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_status_code INTEGER;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS sip_status TEXT;
