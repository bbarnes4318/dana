-- Migration: 006_pgvector_rag_documents.sql
-- Upgrades PostgreSQL schema for pgvector RAG documents storage.

CREATE EXTENSION IF NOT EXISTS vector;

-- We use a default dimension size of 384, which matches the deterministic fallback 
-- and standard sentence-transformers model (all-MiniLM-L6-v2). If switching to OpenAI embeddings 
-- (1536 dimensions), this column type should be altered or a separate table used.
CREATE TABLE IF NOT EXISTS rag_documents (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(384),
    metadata JSONB NOT NULL DEFAULT '{}',
    source TEXT,
    source_id TEXT,
    source_type TEXT,
    topic TEXT,
    call_stage TEXT,
    doc_type TEXT,
    approved BOOLEAN DEFAULT FALSE,
    quality_score DOUBLE PRECISION,
    compliance_priority BOOLEAN DEFAULT FALSE,
    version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_rag_documents_approved ON rag_documents(approved);
CREATE INDEX IF NOT EXISTS idx_rag_documents_source ON rag_documents(source);
CREATE INDEX IF NOT EXISTS idx_rag_documents_source_type ON rag_documents(source_type);
CREATE INDEX IF NOT EXISTS idx_rag_documents_topic ON rag_documents(topic);
CREATE INDEX IF NOT EXISTS idx_rag_documents_call_stage ON rag_documents(call_stage);
CREATE INDEX IF NOT EXISTS idx_rag_documents_doc_type ON rag_documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_rag_documents_compliance_priority ON rag_documents(compliance_priority);
CREATE INDEX IF NOT EXISTS idx_rag_documents_quality_score ON rag_documents(quality_score);
CREATE INDEX IF NOT EXISTS idx_rag_documents_metadata ON rag_documents USING gin(metadata);

-- HNSW or IVFFlat vector index if supported (HNSW is supported on pgvector 0.5.0+)
CREATE INDEX IF NOT EXISTS idx_rag_documents_embedding_cosine ON rag_documents USING hnsw (embedding vector_cosine_ops);
