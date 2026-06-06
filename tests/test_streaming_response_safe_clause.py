import pytest
from core.streaming_response import SafeClauseBuffer

def test_safe_clause_buffer_basic_splitting():
    """Verify that SafeClauseBuffer splits sentences correctly on punctuation."""
    buf = SafeClauseBuffer(max_first_clause_len=150)
    
    # Feeding a partial sentence should not emit anything
    assert buf.process_chunk("Hello ") is None
    assert buf.process_chunk("there") is None
    
    # Ending the sentence should emit the first clause
    first = buf.process_chunk(". How are you?")
    assert first == "Hello there."
    assert buf.first_clause_emitted is True
    
    # Subsequent chunks should be buffered, not emitted
    assert buf.process_chunk(" I am doing") is None
    assert buf.process_chunk(" great.") is None
    
    # Finalize should return the first clause and the remainder
    first_f, remainder_f = buf.finalize()
    assert first_f == "Hello there."
    assert remainder_f == "How are you? I am doing great."

def test_safe_clause_buffer_abbreviation_ignoring():
    """Verify that abbreviations (like Mr., Mrs., etc.) do not trigger false sentence boundaries."""
    buf = SafeClauseBuffer(max_first_clause_len=150)
    
    # "Mr." should be ignored as a sentence boundary
    assert buf.process_chunk("Hello Mr. ") is None
    assert buf.process_chunk("Smith. How is it going?") == "Hello Mr. Smith."
    
    first_f, remainder_f = buf.finalize()
    assert first_f == "Hello Mr. Smith."
    assert remainder_f == "How is it going?"

def test_safe_clause_buffer_max_length_fallback():
    """If the first clause exceeds max length, it should fallback to unsafe (which forces full validation path)."""
    buf = SafeClauseBuffer(max_first_clause_len=20)
    
    # Exceeding the length before any punctuation should flag buffer as unsafe
    assert buf.process_chunk("This is a very long sentence that has no end yet") is None
    assert buf.is_unsafe is True
    
    first_f, remainder_f = buf.finalize()
    assert first_f is None
    assert remainder_f is None
