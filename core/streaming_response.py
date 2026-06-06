import re
import os
from typing import List, Optional, Tuple

class StreamingResponseChunk:
    """A chunk of streamed text from the LLM."""
    def __init__(self, content: str, is_final: bool = False):
        self.content = content
        self.is_final = is_final

class SafeClauseBuffer:
    """
    Accumulates streamed tokens from the LLM. 
    Detects the first safe clause boundary, validates it against compliance,
    and yields it to the TTS node immediately.
    Buffers the remainder until completion and full validation.
    """
    def __init__(self, max_first_clause_len: int = 150):
        self.max_first_clause_len = max_first_clause_len
        self.buffer = ""
        self.first_clause_emitted = False
        self.is_unsafe = False
        self.first_clause = ""
        
        # Core compliance patterns that must be blocked early
        self.forbidden_patterns = [
            re.compile(r"\bqualif(?:y|ied|ication)\b", re.IGNORECASE),
            re.compile(r"\bapprov(?:ed|al)\b", re.IGNORECASE),
            re.compile(r"\bguarante(?:e|ed)\b", re.IGNORECASE),
            re.compile(r"\bgovernment\s+benefit\b", re.IGNORECASE),
            re.compile(r"\blicensed\s+agent\b", re.IGNORECASE),
            re.compile(r"\blicensed\b", re.IGNORECASE),
            re.compile(r"\$\s?\d+", re.IGNORECASE),
            re.compile(r"\bdollars\b", re.IGNORECASE),
        ]

    def is_text_safe(self, text: str) -> bool:
        """Determines if the text chunk is compliance-safe."""
        text_clean = re.sub(r"[^\w\s\$]", " ", text)
        for pattern in self.forbidden_patterns:
            if pattern.search(text_clean):
                return False
        return True

    def process_chunk(self, content: str) -> Optional[str]:
        """
        Feeds a chunk of text into the buffer.
        Returns the first safe clause if it was just completed and verified,
        otherwise returns None.
        """
        if self.is_unsafe:
            return None

        self.buffer += content

        # Check early compliance blocker on the entire accumulated buffer
        if not self.is_text_safe(self.buffer):
            self.is_unsafe = True
            return None

        if not self.first_clause_emitted and len(self.buffer) > self.max_first_clause_len:
            self.is_unsafe = True
            return None

        if self.first_clause_emitted:
            # First clause is already emitted, keep buffering remainder
            return None

        # Check for sentence/clause boundary (. ! ? \n)
        # Avoid splitting on common abbreviations
        abbrev_pattern = r"\b(?:Mr|Mrs|Dr|St|Co|Inc|vs)$"
        
        for i, char in enumerate(self.buffer):
            if char in ".!?\n":
                candidate = self.buffer[:i + 1]
                # If it looks like an abbreviation, don't split
                if char == "." and re.search(abbrev_pattern, candidate[:-1].strip(), re.IGNORECASE):
                    continue
                
                candidate_str = candidate.strip()
                if len(candidate_str) > self.max_first_clause_len:
                    # Exceeds max first clause length, force fallback path
                    self.is_unsafe = True
                    return None

                if self.is_text_safe(candidate_str):
                    self.first_clause = candidate_str
                    self.first_clause_emitted = True
                    self.buffer = self.buffer[i + 1:]
                    return self.first_clause
                else:
                    self.is_unsafe = True
                    return None

        return None

    def finalize(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Called when LLM stream finishes.
        Returns:
            Tuple[first_clause, remainder] if safe, or (None, None) if unsafe.
        """
        remaining = self.buffer.strip()
        
        # Check remainder compliance
        if self.is_unsafe or not self.is_text_safe(remaining):
            return None, None

        if not self.first_clause_emitted:
            # Never split: treat entire response as first clause
            if self.is_text_safe(remaining):
                return remaining, ""
            return None, None

        return self.first_clause, remaining

    def clear(self) -> None:
        """Resets the buffer (useful on user interruption/barge-in)."""
        self.buffer = ""
        self.first_clause_emitted = False
        self.is_unsafe = False
        self.first_clause = ""
