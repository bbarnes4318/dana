-- Migration 012: Add room_name and sip_participant_id to calls table
ALTER TABLE calls ADD COLUMN IF NOT EXISTS room_name TEXT;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS sip_participant_id TEXT;
