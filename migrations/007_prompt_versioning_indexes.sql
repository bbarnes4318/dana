-- Migration: 007_prompt_versioning_indexes.sql
-- Create indexes for the prompt_versions table to optimize file_path and sha queries.

CREATE INDEX IF NOT EXISTS idx_prompt_versions_file_path ON prompt_versions(file_path);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_sha ON prompt_versions(sha);
