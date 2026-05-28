"""Prosody shaping and phone number/number expansion for phone TTS.

Expands numbers, ages, prices, percentages, times, and phone numbers into natural spoken
words, reducing punctuation clutter and shielding alphanumeric IDs from alteration.
"""

from __future__ import annotations

import re


def number_to_words(n: int) -> str:
    """Convert an integer up to millions into its English word representation."""
    if n == 0:
        return "zero"

    units = [
        "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"
    ]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    if n < 20:
        return units[n]
    elif n < 100:
        suffix = "" if n % 10 == 0 else " " + units[n % 10]
        return tens[n // 10] + suffix
    elif n < 1000:
        suffix = "" if n % 100 == 0 else " " + number_to_words(n % 100)
        return units[n // 100] + " hundred" + suffix
    elif n < 1000000:
        suffix = "" if n % 1000 == 0 else " " + number_to_words(n % 1000)
        return number_to_words(n // 1000) + " thousand" + suffix

    return str(n)


def expand_phone_digits(match: re.Match) -> str:
    """Convert a matched phone number into space-separated digit words."""
    digits = [c for c in match.group(0) if c.isdigit()]
    digit_words = {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"
    }
    return " ".join(digit_words[d] for d in digits)


def expand_time(match: re.Match) -> str:
    """Convert time format (e.g. 3:30) to spoken text."""
    hour = int(match.group(1))
    minute = int(match.group(2))

    hour_word = number_to_words(hour)
    if minute == 0:
        return f"{hour_word} o'clock"
    elif minute < 10:
        return f"{hour_word} oh {number_to_words(minute)}"
    else:
        return f"{hour_word} {number_to_words(minute)}"


class ProsodyController:
    """Prepares response texts for smooth and natural phone TTS pronunciation."""

    def format_for_tts(self, text: str) -> str:
        """Applies abbreviations expansion, number/currency expansion, and punctuation adjustments."""
        if not text:
            return text

        # 1. Expand age ranges (e.g. "40-85" or "40 to 85")
        text = re.sub(
            r'\b40\s*-\s*85\b', 
            "forty to eighty-five", 
            text, 
            flags=re.IGNORECASE
        )
        text = re.sub(
            r'\b40\s+to\s+85\b', 
            "forty to eighty-five", 
            text, 
            flags=re.IGNORECASE
        )

        # 2. Money expansion (e.g. $50 -> fifty dollars)
        text = re.sub(
            r'\$(\d+)', 
            lambda m: f"{number_to_words(int(m.group(1)))} dollars", 
            text
        )

        # 3. Percent expansion (e.g. 20% -> twenty percent)
        text = re.sub(
            r'(\d+)%', 
            lambda m: f"{number_to_words(int(m.group(1)))} percent", 
            text
        )

        # 4. Time expansion (e.g. 3:30 -> three thirty)
        text = re.sub(r'\b(\d{1,2}):(\d{2})\b', expand_time, text)

        # 5. Phone number digit-by-digit expansion
        # Match E.164 pattern (+13055550199) or standard formats (305-555-0199)
        text = re.sub(
            r'\+?\b1?\d{10}\b|\+?\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b', 
            expand_phone_digits, 
            text
        )

        # 6. Standalone number expansion (shielding alphanumeric IDs)
        # Match standalone numbers not preceded/followed by letters or hyphens
        text = re.sub(
            r'(?<![a-zA-Z-])\b\d+\b(?![a-zA-Z-])', 
            lambda m: number_to_words(int(m.group(0))), 
            text
        )

        # 7. Convert semicolons and break compound sentences for smooth phrasing
        text = text.replace(";", ".")
        text = re.sub(r',\s+but\b', '. But', text, flags=re.IGNORECASE)
        text = re.sub(r',\s+and\b', '. And', text, flags=re.IGNORECASE)
        text = re.sub(r',\s+so\b', '. So', text, flags=re.IGNORECASE)

        # 8. Symbols to words
        text = text.replace("&", " and ")
        text = text.replace("@", " at ")

        # 9. Reduce excessive commas (limit to max 2 commas per sentence)
        sentences = re.split(r'(?<=[.!?])\s+', text)
        clean_sentences = []
        for s in sentences:
            if s.count(",") > 2:
                parts = s.split(",")
                s = parts[0] + ", " + " ".join(parts[1:])
            clean_sentences.append(s)
        text = " ".join(clean_sentences)

        # 10. Fix common abbreviations that cause issues in phone TTS
        text = re.sub(r'\bprosp\.', 'prospect', text, flags=re.IGNORECASE)
        text = re.sub(r'\bapp\.', 'application', text, flags=re.IGNORECASE)
        text = re.sub(r'\bcoord\.', 'coordinator', text, flags=re.IGNORECASE)
        text = re.sub(r'\be\.164\b', 'phone number', text, flags=re.IGNORECASE)
        text = re.sub(r'\bSIP\b', 'sip', text)

        # Final whitespace cleanup
        text = re.sub(r'\s+', ' ', text).strip()
        return text
