"""Extraction utilities for parsing user utterances.

All functions are *best-effort* — they return ``None`` when the input
cannot be confidently parsed so the agent can re-prompt.
"""

from __future__ import annotations

import re
from typing import Optional

# ── US state lookup tables ──────────────────────────────────────────

_STATE_ABBREVS: dict[str, str] = {
    "AL": "AL", "AK": "AK", "AZ": "AZ", "AR": "AR", "CA": "CA",
    "CO": "CO", "CT": "CT", "DE": "DE", "FL": "FL", "GA": "GA",
    "HI": "HI", "ID": "ID", "IL": "IL", "IN": "IN", "IA": "IA",
    "KS": "KS", "KY": "KY", "LA": "LA", "ME": "ME", "MD": "MD",
    "MA": "MA", "MI": "MI", "MN": "MN", "MS": "MS", "MO": "MO",
    "MT": "MT", "NE": "NE", "NV": "NV", "NH": "NH", "NJ": "NJ",
    "NM": "NM", "NY": "NY", "NC": "NC", "ND": "ND", "OH": "OH",
    "OK": "OK", "OR": "OR", "PA": "PA", "RI": "RI", "SC": "SC",
    "SD": "SD", "TN": "TN", "TX": "TX", "UT": "UT", "VT": "VT",
    "VA": "VA", "WA": "WA", "WV": "WV", "WI": "WI", "WY": "WY",
    "DC": "DC",
}

_STATE_NAMES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}

# ── Yes / No keyword sets ──────────────────────────────────────────

_YES_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "absolutely", "correct",
    "right", "affirmative", "of course", "definitely", "you bet",
    "uh huh", "uh-huh", "ok", "okay", "mhm", "mm-hmm",
})

_NO_WORDS = frozenset({
    "no", "nah", "nope", "not really", "negative", "no thanks",
    "no thank you", "i don't think so", "not interested",
})

# ── DNC / callback phrases ─────────────────────────────────────────

_DNC_PHRASES = [
    "do not call", "don't call", "stop calling", "remove me",
    "take me off", "unsubscribe", "never call", "quit calling",
    "remove my number", "put me on the do not call",
]

_CALLBACK_PHRASES = [
    "call me back", "call back", "callback", "call later",
    "try again later", "not a good time", "call me tomorrow",
    "call another time", "reschedule",
]

# ── Public API ──────────────────────────────────────────────────────


def extract_age(text: str) -> Optional[int]:
    """Extract a numeric age from *text*.

    Handles patterns like "I'm 67", "sixty seven", "age 67", "67 years old".
    Returns ``None`` if no plausible age (18-120) is found.
    """
    # Written-out numbers (common in voice transcription)
    _word_numbers: dict[str, int] = {
        "eighteen": 18, "nineteen": 19,
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }
    _ones = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9,
    }

    lower = text.lower().strip()

    # Try word-based ages first (e.g. "sixty seven", "seventy-two")
    for tens_word, tens_val in _word_numbers.items():
        if tens_word in lower:
            # check for a ones component
            for ones_word, ones_val in _ones.items():
                pattern = rf"{tens_word}[\s\-]+{ones_word}"
                if re.search(pattern, lower):
                    age = tens_val + ones_val
                    if 18 <= age <= 120:
                        return age
            # standalone decade word
            if tens_val >= 18:
                return tens_val

    # Numeric patterns
    match = re.search(r"\b(\d{2,3})\b", lower)
    if match:
        age = int(match.group(1))
        if 18 <= age <= 120:
            return age

    return None


def extract_state(text: str) -> Optional[str]:
    """Extract a US state abbreviation from *text*.

    Accepts full state names ("North Carolina") or two-letter abbreviations.
    Returns the uppercase two-letter code or ``None``.
    """
    lower = text.lower().strip()

    # Try full names first (longer matches win over partial)
    for name, abbr in sorted(_STATE_NAMES.items(), key=lambda kv: -len(kv[0])):
        if name in lower:
            return abbr

    # Try two-letter abbreviations (word-boundary constrained)
    words = re.findall(r"\b([a-zA-Z]{2})\b", text)
    candidates = []
    conflict_words = {"in", "or", "me", "hi", "oh", "ok", "la", "id"}
    for w in words:
        w_upper = w.upper()
        if w_upper in _STATE_ABBREVS:
            is_conflict = w.lower() in conflict_words
            if is_conflict:
                score = 1 if w.isupper() else 0
            else:
                score = 2 if w.isupper() else 1
            candidates.append((score, w_upper))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_abbr = candidates[0]
        if best_score > 0:
            return best_abbr

    return None


def extract_phone_type(text: str) -> Optional[str]:
    """Return ``'cell'`` or ``'landline'`` if mentioned in *text*."""
    lower = text.lower()
    cell_keywords = ["cell", "mobile", "smartphone", "iphone", "android"]
    landline_keywords = ["landline", "land line", "home phone", "house phone"]

    for kw in cell_keywords:
        if kw in lower:
            return "cell"
    for kw in landline_keywords:
        if kw in lower:
            return "landline"
    return None


def extract_yes_no(text: str) -> Optional[bool]:
    """Return ``True`` for affirmative, ``False`` for negative, else ``None``."""
    lower = text.lower().strip()

    # Check multi-word phrases first with word boundaries
    for phrase in sorted(_NO_WORDS, key=len, reverse=True):
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, lower):
            return False
    for phrase in sorted(_YES_WORDS, key=len, reverse=True):
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, lower):
            return True

    return None


def extract_name(text: str) -> Optional[str]:
    """Attempt to extract a person's first name from *text*.

    Very simple heuristic — looks for common intro patterns.
    Returns ``None`` when uncertain.
    """
    patterns = [
        r"(?:my name is|i'm|i am|this is|call me)\s+([A-Z][a-z]+)",
        r"^([A-Z][a-z]+)$",  # single capitalised word
    ]
    for pat in patterns:
        match = re.search(pat, text.strip(), re.IGNORECASE)
        if match:
            return match.group(1).capitalize()
    return None


def detect_dnc_request(text: str) -> bool:
    """Return ``True`` if *text* contains a do-not-call request."""
    lower = text.lower()
    return any(phrase in lower for phrase in _DNC_PHRASES)


def detect_callback_request(text: str) -> bool:
    """Return ``True`` if *text* contains a callback / reschedule request."""
    lower = text.lower()
    return any(phrase in lower for phrase in _CALLBACK_PHRASES)
