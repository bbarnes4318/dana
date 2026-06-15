-- Migration: 009_training_notes_review.sql
-- Add fields to training_notes table for status tracking, compliance filtering, and RAG configuration.

ALTER TABLE training_notes ADD COLUMN IF NOT EXISTS objection_type TEXT;
ALTER TABLE training_notes ADD COLUMN IF NOT EXISTS compliance_risk TEXT;
ALTER TABLE training_notes ADD COLUMN IF NOT EXISTS use_in_live_call BOOLEAN DEFAULT FALSE;
ALTER TABLE training_notes ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending_review';

-- Create index for status-based lookups during review queue listing or re-indexing
CREATE INDEX IF NOT EXISTS idx_training_notes_status ON training_notes(status);
CREATE INDEX IF NOT EXISTS idx_training_notes_use_in_live_call ON training_notes(use_in_live_call);
