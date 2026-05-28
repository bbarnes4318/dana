"""Dialogue style and brevity controller for Dana.

Enforces stage-specific word count limits and single-question per turn rules,
guaranteeing that critical stage questions are preserved and never truncated.
"""

from __future__ import annotations

import re
from typing import Optional

STAGE_BREVITY_RULES = {
    "answered": 30,
    "opening": 30,
    "interest_check": 30,
    "age_range": 25,
    "living_situation": 25,
    "decision_maker": 25,
    "transfer_consent": 25,
    "transfer_ready": 35,
    "callback": 30,
    "dnc": 20,
    "disqualified": 25,
    "end": 25,
}

CORPORATE_REPLACEMENTS = {
    r"\bas an ai assistant\b": "",
    r"\bi am a virtual agent\b": "",
    r"\bi am an ai\b": "",
    r"\bi'm an ai\b": "",
    r"\bcertainly\b": "sure",
    r"\babsolutely\b": "yes",
    r"\bhere to assist\b": "here to help",
    r"\bour records show\b": "it looks like",
    r"\bhow can i help you today\b": "what can I help you with",
    r"\bhow may i assist you\b": "how can I help",
    r"\bi can definitely help with that\b": "Sure thing.",
    r"\bdefinitely\b": "sure",
    r"\bapologize for the inconvenience\b": "sorry about that",
    r"\bfeel free to\b": "go ahead and",
}


def get_critical_sentence(sentences: list[str], stage: str) -> Optional[str]:
    """Identify the sentence in a response that contains the stage-critical question."""
    stage_lower = stage.lower()
    keywords = []
    
    if stage_lower == "interest_check":
        keywords = ["open", "looking", "burial", "expense", "options"]
    elif stage_lower == "age_range":
        keywords = ["forty", "eighty-five", "age", "old", "40", "85"]
    elif stage_lower == "living_situation":
        keywords = ["independent", "nursing", "assisted", "home", "living"]
    elif stage_lower == "decision_maker":
        keywords = ["financial", "decision", "maker", "handle"]
    elif stage_lower == "transfer_consent":
        keywords = ["hold", "line", "connect", "licensed", "coordinator", "agent"]
    elif stage_lower == "callback":
        keywords = ["today", "tomorrow", "back", "callback", "time"]

    if not keywords:
        return None

    best_s = None
    max_matches = 0
    for s in sentences:
        s_lower = s.lower()
        matches = sum(1 for kw in keywords if kw in s_lower)
        if matches > max_matches:
            max_matches = matches
            best_s = s
            
    return best_s


class DialogueStyleController:
    """Enforces brevity and single question rules deterministically."""

    def remove_markdown(self, text: str) -> str:
        """Strip all markdown syntax from the text."""
        # Bold/Italic
        text = re.sub(r'\*+', '', text)
        # Headers
        text = re.sub(r'#+\s*', '', text)
        # Bullet points
        text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
        # Backticks
        text = re.sub(r'`', '', text)
        return text

    def clean_corporate_phrases(self, text: str) -> str:
        """Replace corporate and awkward assistant phrases with natural alternatives."""
        for pattern, replacement in CORPORATE_REPLACEMENTS.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        # Clean up leading commas, periods, or spaces left over from stripping phrases at sentence start
        text = re.sub(r'^[.,\s]+', '', text)
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Ensure capitalization if stripped from start
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        return text

    def enforce_one_question(self, text: str, stage: str) -> str:
        """Ensures at most one question is asked, preserving the stage-critical question."""
        if text.count("?") <= 1:
            return text

        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        critical_s = get_critical_sentence(sentences, stage)
        
        # If no critical sentence matched, default to the last sentence ending with ?
        if not critical_s:
            questions = [s for s in sentences if s.endswith("?")]
            if questions:
                critical_s = questions[-1]

        new_sentences = []
        for s in sentences:
            if s.endswith("?"):
                if s == critical_s:
                    new_sentences.append(s)
                else:
                    # Strip simple conversational icebreakers
                    if any(w in s.lower() for w in ["how are you", "how's it going", "how is your day"]):
                        continue
                    # Convert other questions into statements
                    new_sentences.append(s[:-1] + ".")
            else:
                new_sentences.append(s)

        return " ".join(new_sentences)

    def enforce_brevity(self, text: str, stage: str) -> str:
        """Truncates responses to stage max word counts without discarding stage questions."""
        max_words = STAGE_BREVITY_RULES.get(stage.lower(), 30)
        words = text.split()
        if len(words) <= max_words:
            return text

        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        critical_s = get_critical_sentence(sentences, stage)

        current_text = ""
        critical_added = False

        for s in sentences:
            if s == critical_s:
                temp = (current_text + " " + s).strip()
                if len(temp.split()) <= max_words or not current_text:
                    current_text = temp
                else:
                    # Clear out preceding text to prioritize critical question
                    current_text = s
                critical_added = True
            else:
                temp = (current_text + " " + s).strip()
                if len(temp.split()) <= max_words:
                    current_text = temp

        if not critical_added and critical_s:
            current_text = critical_s

        if not current_text and sentences:
            current_text = sentences[0]

        return current_text

    def process(self, text: str, stage: str) -> str:
        """Applies markdown removal, phrase cleaning, single-question, and brevity rules."""
        text = self.remove_markdown(text)
        text = self.clean_corporate_phrases(text)
        text = self.enforce_one_question(text, stage)
        text = self.enforce_brevity(text, stage)
        return text
