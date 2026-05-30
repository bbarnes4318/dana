"""Tests for production-grade RAG retrieval, embeddings, and vector stores."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest
from unittest.mock import MagicMock

from rag.document import Document
from rag.embeddings import (
    EmbeddingProvider,
    DeterministicFallbackProvider,
    get_embedding_provider
)
from rag.vector_store import (
    JsonlVectorStore,
    PostgresVectorStore,
    SearchResult,
    get_vector_store
)
from rag.retriever import Retriever
from rag.context_builder import ContextBuilder


@pytest.fixture
def temp_jsonl():
    """Create a temporary path for JsonlVectorStore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "vector_index.jsonl")


def test_embedding_provider_deterministic_fallback():
    """Verify MD5 deterministic fallback properties."""
    provider = DeterministicFallbackProvider(dimensions=100)
    text1 = "Hello, this is a final expense RAG test."
    text2 = "Hello, this is a final expense RAG test."
    text3 = "Different text query."

    vec1 = provider.embed_text(text1)
    vec2 = provider.embed_text(text2)
    vec3 = provider.embed_text(text3)

    # Same text returns same vector
    assert vec1 == vec2
    assert len(vec1) == 100

    # Different text returns different vector
    assert vec1 != vec3

    # Empty text returns zero vector
    assert provider.embed_text("") == [0.0] * 100
    assert provider.embed_text("   ") == [0.0] * 100

    # Very long text is truncated safely
    long_text = "abc " * 5000
    assert len(provider.embed_text(long_text)) == 100

    # Batch embedding handles empty list
    assert provider.embed_batch([]) == []


def test_embedding_provider_factory_defaults_to_deterministic():
    """Verify that factory defaults to deterministic provider when env is unset."""
    # Temporarily clean env
    old_val = os.environ.pop("DANA_EMBEDDING_PROVIDER", None)
    try:
        provider = get_embedding_provider()
        assert provider.name == "deterministic"
    finally:
        if old_val is not None:
            os.environ["DANA_EMBEDDING_PROVIDER"] = old_val


def test_vector_store_upsert_and_search_jsonl(temp_jsonl):
    """Verify JsonlVectorStore upsert (add with duplicate id) and simple search."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc_id = "doc-1"
    doc1 = Document(
        id=doc_id,
        content="First version content.",
        source="kb/test.md",
        embedding=provider.embed_text("First version content."),
        approved=True
    )
    store.add(doc1)
    assert store.count() == 1

    # Upsert with same ID
    doc1_updated = Document(
        id=doc_id,
        content="Second version content.",
        source="kb/test.md",
        embedding=provider.embed_text("Second version content."),
        approved=True
    )
    store.add(doc1_updated)
    assert store.count() == 1

    results = store.search(query_embedding=provider.embed_text("Second version"), top_k=5)
    assert len(results) == 1
    assert results[0].document.content == "Second version content."


def test_approved_only_filtering(temp_jsonl):
    """Verify approved_only=True/False filters unapproved documents correctly."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc1 = Document(
        id="approved-doc",
        content="This is approved final expense guide.",
        source="kb/guide.md",
        embedding=provider.embed_text("This is approved final expense guide."),
        approved=True
    )
    doc2 = Document(
        id="unapproved-doc",
        content="This is unapproved draft insurance guide.",
        source="kb/draft.md",
        embedding=provider.embed_text("This is unapproved draft insurance guide."),
        approved=False
    )
    store.add(doc1)
    store.add(doc2)

    # approved_only = True (Default)
    results = store.search(query_embedding=provider.embed_text("insurance guide"), approved_only=True)
    assert len(results) == 1
    assert results[0].document.id == "approved-doc"

    # approved_only = False
    results_all = store.search(query_embedding=provider.embed_text("insurance guide"), approved_only=False)
    assert len(results_all) == 2


def test_metadata_filtering_by_stage(temp_jsonl):
    """Verify call_stage matching boosts the matching document."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc_opening = Document(
        id="opening-doc",
        content="Hello, my name is Alex.",
        source="kb/scripts.md",
        call_stage="opening",
        embedding=provider.embed_text("Hello, my name is Alex."),
        approved=True
    )
    doc_pricing = Document(
        id="pricing-doc",
        content="We offer affordable pricing options.",
        source="kb/objections.md",
        call_stage="price_question",
        embedding=provider.embed_text("We offer affordable pricing options."),
        approved=True
    )
    store.add(doc_opening)
    store.add(doc_pricing)

    # Match call_stage: price_question
    results = store.search(
        query_embedding=provider.embed_text("options"),
        query_text="options",
        call_stage="price_question",
        approved_only=True
    )
    # The pricing document should be boosted and rank first
    assert results[0].document.id == "pricing-doc"
    assert "matches query call_stage" in "".join(results[0].reasons)


def test_topic_boosting(temp_jsonl):
    """Verify matching topic boosts the document score."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc_general = Document(
        id="general-doc",
        content="Affordable coverage is available for everyone.",
        source="kb/general.md",
        topic="coverage",
        embedding=provider.embed_text("Affordable coverage is available for everyone."),
        approved=True
    )
    doc_objection = Document(
        id="objection-doc",
        content="Objection answer: our plans fit any budget.",
        source="kb/objections.md",
        topic="objections",
        embedding=provider.embed_text("Objection answer: our plans fit any budget."),
        approved=True
    )
    store.add(doc_general)
    store.add(doc_objection)

    # Match topic: objections
    results = store.search(
        query_embedding=provider.embed_text("budget"),
        query_text="budget",
        topic="objections",
        approved_only=True
    )
    assert results[0].document.id == "objection-doc"


def test_compliance_priority_boost(temp_jsonl):
    """Verify compliance document is boosted on compliance/objection query."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc_general = Document(
        id="general-doc",
        content="We can discuss options details.",
        source="kb/general.md",
        embedding=provider.embed_text("We can discuss options details."),
        approved=True
    )
    doc_compliance = Document(
        id="compliance-doc",
        content="Dana must never claim she is licensed.",
        source="kb/compliance.md",
        compliance_priority=True,
        embedding=provider.embed_text("Dana must never claim she is licensed."),
        approved=True
    )
    store.add(doc_general)
    store.add(doc_compliance)

    # Query containing "licensed" should trigger compliance boost
    results = store.search(
        query_embedding=provider.embed_text("Are you licensed?"),
        query_text="Are you licensed?",
        approved_only=True
    )
    assert results[0].document.id == "compliance-doc"


def test_quality_score_boost(temp_jsonl):
    """Verify quality_score boosts document ranking when content matches similarly."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc_low = Document(
        id="low-quality-doc",
        content="Insurance options are good.",
        source="kb/test.md",
        quality_score=0.2,
        embedding=provider.embed_text("Insurance options are good."),
        approved=True
    )
    doc_high = Document(
        id="high-quality-doc",
        content="Insurance options are good.",
        source="kb/test.md",
        quality_score=0.9,
        embedding=provider.embed_text("Insurance options are good."),
        approved=True
    )
    store.add(doc_low)
    store.add(doc_high)

    results = store.search(
        query_embedding=provider.embed_text("Insurance options"),
        query_text="Insurance options",
        approved_only=True
    )
    assert results[0].document.id == "high-quality-doc"


def test_keyword_plus_semantic_hybrid_scoring(temp_jsonl):
    """Verify exact phrase keyword overlap boosts documents."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc1 = Document(
        id="doc1",
        content="Some different words that don't match the query phrase exactly.",
        source="kb/test.md",
        embedding=provider.embed_text("Some different words that don't match the query phrase exactly."),
        approved=True
    )
    doc2 = Document(
        id="doc2",
        content="The exact phrase match should be here.",
        source="kb/test.md",
        embedding=provider.embed_text("The exact phrase match should be here."),
        approved=True
    )
    store.add(doc1)
    store.add(doc2)

    results = store.search(
        query_embedding=provider.embed_text("exact phrase match"),
        query_text="exact phrase match",
        approved_only=True
    )
    assert results[0].document.id == "doc2"
    assert "Exact phrase match boost applied" in "".join(results[0].reasons)


def test_context_builder_preserves_existing_api(temp_jsonl):
    """Verify ContextBuilder.build_context works correctly under standard arguments."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)
    retriever = Retriever(embedding_provider=provider, vector_store=store)
    builder = ContextBuilder(retriever=retriever)

    doc = Document(
        id="approved-doc",
        content="American Beneficiary provides burial coverage.",
        source="kb/general.md",
        embedding=provider.embed_text("American Beneficiary provides burial coverage."),
        approved=True
    )
    store.add(doc)

    context = builder.build_context(
        query="burial coverage",
        call_stage="opening",
        lead_profile={"name": "John Doe"},
        objection_type=None
    )

    assert "KNOWLEDGE CONTEXT" in context
    assert "American Beneficiary" in context


def test_context_builder_returns_empty_on_retrieval_failure():
    """Verify context builder fails open and returns empty string if retriever crashes."""
    mock_retriever = MagicMock()
    mock_retriever.retrieve.side_effect = Exception("Connection error")
    
    # Enable fail-open (Default)
    os.environ["DANA_RAG_FAIL_OPEN"] = "true"
    builder = ContextBuilder(retriever=mock_retriever)

    context = builder.build_context(query="crashing query")
    assert context == ""


def test_retrieval_does_not_return_unapproved_by_default(temp_jsonl):
    """Verify default retrieve filter excludes unapproved documents."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)
    retriever = Retriever(embedding_provider=provider, vector_store=store)

    doc_unapproved = Document(
        id="unapproved-doc",
        content="Unapproved guide content.",
        source="kb/general.md",
        embedding=provider.embed_text("Unapproved guide content."),
        approved=False
    )
    store.add(doc_unapproved)

    results = retriever.retrieve(query="guide content")
    assert len(results) == 0


def test_search_result_contains_score_breakdown_and_reasons(temp_jsonl):
    """Verify SearchResult output formatting."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    doc = Document(
        id="doc",
        content="Affordable final expense insurance plans.",
        source="kb/basics.md",
        embedding=provider.embed_text("Affordable final expense insurance plans."),
        approved=True,
        quality_score=0.8
    )
    store.add(doc)

    results = store.search(
        query_embedding=provider.embed_text("final expense plans"),
        query_text="final expense plans",
        approved_only=True
    )
    assert len(results) == 1
    res = results[0]
    assert isinstance(res, SearchResult)
    assert res.semantic_score >= 0.0
    assert res.keyword_score >= 0.0
    assert res.metadata_score >= 0.0
    assert res.final_score > 0.0
    assert len(res.reasons) > 0


def test_ingest_knowledge_preserves_old_behavior(temp_jsonl):
    """Verify indexing RAG Document chunks into vector store works."""
    store = JsonlVectorStore(path=temp_jsonl)
    provider = DeterministicFallbackProvider(dimensions=128)

    # Ingestion flow simulation: parse md and build_index
    doc = Document(
        id="chunk-1",
        content="Raw curated knowledge text.",
        source="kb/basics.md",
        embedding=provider.embed_text("Raw curated knowledge text."),
        approved=True
    )
    
    store.build_index([doc])
    assert store.count() == 1


def test_no_external_provider_required_for_tests():
    """Verify test runs do not require sentence-transformers or OpenAI keys."""
    # With no API keys, standard retrieval initialization must fall back to deterministic
    provider = get_embedding_provider()
    assert provider.name == "deterministic"


def test_openai_provider_dimensions_default_with_postgres_backend(monkeypatch):
    """Verify that OpenAIProvider defaults to 384 dimensions when Postgres is the configured backend."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    monkeypatch.setenv("DANA_RAG_BACKEND", "postgres")
    monkeypatch.setenv("DANA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("DANA_EMBEDDING_DIMENSIONS", raising=False)
    
    from rag.embeddings import OpenAIProvider
    provider = OpenAIProvider()
    assert provider.dimensions == 384


def test_openai_provider_dimensions_default_with_jsonl_backend(monkeypatch):
    """Verify that OpenAIProvider defaults to 1536 dimensions when Postgres backend is not set."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    monkeypatch.setenv("DANA_RAG_BACKEND", "jsonl")
    monkeypatch.setenv("DANA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("DANA_EMBEDDING_DIMENSIONS", raising=False)
    
    from rag.embeddings import OpenAIProvider
    provider = OpenAIProvider()
    assert provider.dimensions == 1536



@pytest.mark.postgres
def test_postgres_pgvector_integration():
    """Optional postgres integration test if DATABASE_URL is set and pgvector exists."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL is not configured.")

    try:
        store = PostgresVectorStore(database_url=db_url)
        # Force lazy connection trigger
        store._get_connection()
    except Exception as e:
        pytest.skip(f"Postgres connection or pgvector initialization failed: {e}")

    # Run simple insert and search
    provider = DeterministicFallbackProvider(dimensions=384)
    doc = Document(
        id="pg-test-doc",
        content="PostgreSQL pgvector test text.",
        source="kb/test.md",
        embedding=provider.embed_text("PostgreSQL pgvector test text."),
        approved=True,
        quality_score=0.9
    )
    store.add(doc)
    
    results = store.search(
        query_embedding=provider.embed_text("pgvector test"),
        query_text="pgvector test",
        approved_only=True
    )
    assert len(results) >= 1
    assert results[0].document.id == "pg-test-doc"
