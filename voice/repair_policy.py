"""Repair policy for Dana.

Determines the appropriate repair language (e.g., "Sorry, go ahead.")
to prepend when the agent is interrupted by the prospect.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RepairPolicy:
    """Enforces repair prefix injection rules when a turn is interrupted."""

    def select_repair_prefix(self, stage: str, turn_count: int) -> str:
        """Selects a repair phrase based on stage and turn count.

        Phrases:
        - "Sorry, go ahead."
        - "I didn’t mean to cut you off."
        - "Sure, I’ll keep it quick."
        """
        stage_lower = stage.lower().strip()

        # If interrupted on opening or initial contact, apologize for cutting off
        if stage_lower in ("opening", "answered"):
            if turn_count <= 2:
                return "I didn't mean to cut you off."
            return "Sorry, go ahead."

        # If they interrupt during an objection or callback, keep it quick
        if stage_lower in ("objection", "callback", "interest_check"):
            if turn_count >= 4:
                return "Sure, I'll keep it quick."
            return "Sorry, go ahead."

        # Default repair prefix
        return "Sorry, go ahead."

    def inject_repair(self, text: str, stage: str, turn_count: int) -> str:
        """Injects the selected repair prefix into the text response."""
        if not text:
            return text

        prefix = self.select_repair_prefix(stage, turn_count)
        
        # Avoid double prepending if already present
        if prefix.lower() in text.lower():
            return text

        # Make sure the rest of the text starts lowercase if it starts with a capital
        rest = text[0].lower() + text[1:] if text[0].isupper() else text
        
        # Connect prefix naturally
        return f"{prefix} {rest}"
