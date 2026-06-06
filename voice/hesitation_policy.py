"""Hesitation policy for Dana.

Determines when and where to insert natural filler words (e.g., "uh", "um", "well")
to humanize conversational pacing, particularly before objection handling.
"""

from __future__ import annotations

import re
import random
from typing import Optional


class HesitationPolicy:
    """Enforces rules for introducing natural hesitations/fillers in agent responses."""

    def __init__(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            random.seed(seed)

    def add_hesitation(
        self,
        text: str,
        stage: str,
        turn_count: int,
        is_objection: bool = False,
        deterministic: bool = False,
    ) -> str:
        """Determines if a hesitation should be added and inserts it naturally.

        Rules:
        - Do NOT add hesitations in compliance-critical stages or endings:
          'opening', 'dnc', 'disqualified', 'end', 'transfer_ready'.
        - Do NOT add if the response starts with repair language or canonical responses.
        - Occasional: ~25% chance of hesitation.
        - Higher chance (50%) if responding to an objection.
        """
        if not text:
            return text

        stage_lower = stage.lower().strip()
        # Stage protection
        if stage_lower in ("opening", "dnc", "disqualified", "end", "transfer_ready"):
            return text

        # Do not add to canonical responses
        text_lower = text.lower()
        if any(c in text_lower for c in ("alex with american beneficiary", "not the licensed agent", "coordinator", "licensed benefits coordinator")):
            return text

        # Check probability
        chance = 0.50 if is_objection else 0.25
        
        # For testing/deterministic behavior, use turn_count
        if deterministic:
            should_hesitate = (turn_count * 7) % 100 < (chance * 100)
        else:
            should_hesitate = random.random() < chance

        if not should_hesitate:
            return text

        # Pick filler
        fillers = ["um, ", "uh, ", "well, "]
        if deterministic:
            filler = fillers[turn_count % len(fillers)]
        else:
            filler = random.choice(fillers)

        # Prepend filler if not already present
        if not text_lower.startswith(("um,", "uh,", "well,")):
            # Handle starting backchannels (e.g. "Okay. Let me check." -> "Okay. Um, let me check.")
            # If it starts with a backchannel and has a second clause, insert it after the backchannel.
            sentences = re.split(r'(?<=[.!?])\s+', text.strip())
            if len(sentences) > 1 and sentences[0].lower().strip() in ("okay.", "gotcha.", "understood.", "right.", "fair enough.", "that makes sense."):
                second_s = sentences[1]
                if second_s and not second_s.lower().startswith(("um", "uh", "well")):
                    # Capitalize filler
                    filler_cap = filler.capitalize()
                    # Ensure second sentence starts lowercase after filler
                    rest = second_s[0].lower() + second_s[1:] if second_s[0].isupper() else second_s
                    sentences[1] = f"{filler_cap}{rest}"
                    return " ".join(sentences)
            
            # Default: Prepend to the start of the response
            text = f"{filler.capitalize()}{text[0].lower() + text[1:] if text[0].isupper() else text}"

        return text
