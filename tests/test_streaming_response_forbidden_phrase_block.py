import pytest
from core.streaming_response import SafeClauseBuffer

def test_safe_clause_buffer_forbidden_phrase_block():
    """Verify that forbidden compliance phrases block early emission immediately."""
    forbidden_words = ["qualify", "approved", "guaranteed", "government benefit", "licensed agent", "$100", "50 dollars"]
    
    for word in forbidden_words:
        buf = SafeClauseBuffer(max_first_clause_len=150)
        
        # Pushing a chunk containing forbidden word should make it unsafe
        assert buf.process_chunk(f"You are {word}.") is None
        assert buf.is_unsafe is True
        
        # Finalize should return (None, None)
        first_f, remainder_f = buf.finalize()
        assert first_f is None
        assert remainder_f is None

def test_safe_clause_buffer_forbidden_phrase_after_first_clause():
    """If the first clause is safe but the remainder is unsafe, we detect the safety violation in remainder."""
    buf = SafeClauseBuffer(max_first_clause_len=150)
    
    # First sentence is safe
    assert buf.process_chunk("Hello. ") == "Hello."
    assert buf.first_clause_emitted is True
    
    # Second sentence contains forbidden word
    assert buf.process_chunk("You are approved for options.") is None
    
    # Finalize should identify the unsafe remainder and return (None, None)
    first_f, remainder_f = buf.finalize()
    assert first_f is None
    assert remainder_f is None
