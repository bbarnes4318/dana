"""Repetition Guard for Dana.

Enforces rules to prevent the agent from repeating exact sentences, openers,
objection handlers, and overused acknowledgments/phrases.
"""

from __future__ import annotations

import re
from typing import List, Set, Dict

STANDARD_ACKNOWLEDGMENTS = {
    "okay", "gotit", "gotcha", "makessense", "thatmakessense", 
    "great", "perfect", "awesome", "right", "understood", 
    "absolutely", "noproblem", "greatquestion"
}

OVERUSED_PHRASE_PATTERNS = {
    "perfect": re.compile(r"\bperfect\b", re.IGNORECASE),
    "gotcha": re.compile(r"\bgotcha\b", re.IGNORECASE),
    "understood": re.compile(r"\bunderstood\b", re.IGNORECASE),
    "absolutely": re.compile(r"\babsolutely\b", re.IGNORECASE),
    "no problem": re.compile(r"\bno\s+problem\b", re.IGNORECASE),
    "great question": re.compile(r"\bgreat\s+question\b", re.IGNORECASE),
}


class RepetitionGuard:
    """Tracks conversation history within a single call to block repetitive phrases."""

    def __init__(self) -> None:
        self.spoken_sentences: Set[str] = set()
        self.spoken_openers: Set[str] = set()
        self.spoken_objections: Set[str] = set()
        self.acknowledgment_counts: Dict[str, int] = {}
        self.phrase_counts: Dict[str, int] = {p: 0 for p in OVERUSED_PHRASE_PATTERNS}

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

            # Rule 1: Guard against exact same sentence (except for standard acknowledgments)
            if norm_s in self.spoken_sentences and norm_s not in STANDARD_ACKNOWLEDGMENTS:
                continue

            # Rule 2: Guard against duplicate openers (first sentence of the response)
            if idx == 0:
                if norm_s in self.spoken_openers:
                    # Strip short openers, but only if there are other sentences in the response
                    if len(clean_s.split()) <= 3 and len(sentences) > 1:
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

            # Rule 5: Count and filter overused phrases within the sentence
            skip_sentence = False
            for phrase, pattern in OVERUSED_PHRASE_PATTERNS.items():
                matches = pattern.findall(clean_s)
                if matches:
                    current_count = self.phrase_counts[phrase]
                    if current_count >= 2:
                        # If the sentence is just the overused phrase, skip it entirely
                        if norm_s in (phrase.replace(" ", ""), "absolutely", "perfect", "understood", "gotcha"):
                            skip_sentence = True
                            break
                        # Otherwise, strip it out or replace it in the sentence
                        if phrase == "absolutely":
                            clean_s = pattern.sub("yes", clean_s)
                        elif phrase == "gotcha":
                            clean_s = pattern.sub("okay", clean_s)
                        elif phrase == "understood":
                            clean_s = pattern.sub("okay", clean_s)
                        elif phrase == "perfect":
                            clean_s = pattern.sub("okay", clean_s)
                        else:
                            clean_s = pattern.sub("", clean_s)
                    else:
                        self.phrase_counts[phrase] = current_count + len(matches)

            if skip_sentence:
                continue

            # Clean up double spaces/leading punctuation left from regex replacement
            clean_s = re.sub(r'\s+', ' ', clean_s).strip()
            clean_s = re.sub(r'^[.,\s]+', '', clean_s)
            if clean_s:
                if clean_s[0].islower():
                    clean_s = clean_s[0].upper() + clean_s[1:]
                self.spoken_sentences.add(norm_s)
                filtered_sentences.append(clean_s)

        return " ".join(filtered_sentences)
