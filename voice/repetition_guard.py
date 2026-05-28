"""Repetition Guard for Dana.

Enforces rules to prevent the agent from repeating exact sentences, openers,
objection handlers, and short acknowledgments.
"""

from __future__ import annotations

import re
from typing import List, Set

STANDARD_ACKNOWLEDGMENTS = {"okay", "gotit", "gotcha", "makessense", "thatmakessense", "great", "perfect", "awesome", "right"}


class RepetitionGuard:
    """Tracks conversation history within a single call to block repetitive phrases."""

    def __init__(self) -> None:
        self.spoken_sentences: Set[str] = set()
        self.spoken_openers: Set[str] = set()
        self.spoken_objections: Set[str] = set()
        self.acknowledgment_counts: dict[str, int] = {}

    def filter_response(self, text: str, is_objection: bool = False) -> str:
        """Filters sentences out of a response if they violate repetition rules."""
        if not text:
            return text

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        filtered_sentences = []

        for idx, s in enumerate(sentences):
            clean_s = s.strip()
            if not clean_s:
                continue

            # Normalize sentence for indexing
            norm_s = re.sub(r'[^a-z0-9]', '', clean_s.lower())

            # Rule 1: Guard against exact same sentence
            if norm_s in self.spoken_sentences:
                continue

            # Rule 2: Guard against duplicate openers (first sentence of the response)
            if idx == 0:
                if norm_s in self.spoken_openers:
                    # Strip short openers
                    if len(clean_s.split()) <= 3:
                        continue
                self.spoken_openers.add(norm_s)

            # Rule 3: Limit short acknowledgments to 2 per call
            if norm_s in STANDARD_ACKNOWLEDGMENTS:
                count = self.acknowledgment_counts.get(norm_s, 0)
                if count >= 2:
                    continue
                self.acknowledgment_counts[norm_s] = count + 1

            # Rule 4: Prevent duplicate objection responses
            if is_objection:
                if norm_s in self.spoken_objections:
                    continue
                self.spoken_objections.add(norm_s)

            self.spoken_sentences.add(norm_s)
            filtered_sentences.append(clean_s)

        return " ".join(filtered_sentences)
