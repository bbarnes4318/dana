"""Objection response policy engine.

Provides guidance for how Dana should respond to detected objection intents,
including attempt tracking, max-attempt enforcement, and stage transitions.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Default path to the objection definitions YAML
_DEFAULT_YAML_PATH = (
    Path(__file__).resolve().parent.parent
    / "kb"
    / "objections"
    / "final_expense_objections.yaml"
)


@dataclass
class ObjectionGuidance:
    """Response guidance returned by the policy engine for a given objection.

    Attributes:
        intent: The objection intent that was matched.
        guidance_text: Human-readable guidance for Dana on how to respond.
        max_attempts: Maximum number of rebuttal attempts allowed.
        should_end_call: Whether the call should be ended after this response.
        next_stage: The conversation stage to transition to.
        compliance_warning: Any compliance-related warnings or notes.
    """

    intent: str
    guidance_text: str
    max_attempts: int
    should_end_call: bool
    next_stage: str
    compliance_warning: Optional[str] = None


class ObjectionResponsePolicy:
    """Policy engine that determines how Dana should respond to objections.

    Tracks the number of rebuttal attempts per objection type and enforces
    limits. Provides response guidance including allowed responses, goals,
    and compliance notes.

    Args:
        yaml_path: Path to the objection definitions YAML.
            Defaults to the bundled final_expense_objections.yaml.
    """

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        self._objection_defs: dict[str, dict] = {}
        self._attempt_counts: dict[str, int] = defaultdict(int)
        self._load_definitions(yaml_path or _DEFAULT_YAML_PATH)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_response_guidance(
        self,
        intent: str,
        attempt_count: Optional[int] = None,
    ) -> ObjectionGuidance:
        """Get response guidance for a given objection intent.

        If ``attempt_count`` is not provided, the internally tracked count
        is used and auto-incremented.

        Args:
            intent: The objection intent string (e.g. ``"not_interested"``).
            attempt_count: Optional explicit attempt count override. If
                provided, the internal tracker is updated to this value.

        Returns:
            An :class:`ObjectionGuidance` with all relevant response info.

        Raises:
            ValueError: If the intent is not recognized.
        """
        defn = self._objection_defs.get(intent)
        if defn is None:
            raise ValueError(
                f"Unknown objection intent: {intent!r}. "
                f"Known intents: {list(self._objection_defs.keys())}"
            )

        # Resolve attempt count
        if attempt_count is not None:
            self._attempt_counts[intent] = attempt_count
        else:
            attempt_count = self._attempt_counts[intent]
            self._attempt_counts[intent] += 1

        max_attempts: int = defn.get("max_attempts", 1)
        next_stage: str = defn.get("next_stage", "continue")
        compliance_notes: Optional[str] = defn.get("compliance_notes")

        # Determine if the call should end
        should_end_call = self._should_end_call(intent, attempt_count, max_attempts, next_stage)

        # If max attempts exceeded, override next_stage
        if attempt_count >= max_attempts:
            if next_stage not in ("end_call",):
                next_stage = "closing"

        # Build guidance text
        guidance_text = self._build_guidance(defn, attempt_count, max_attempts)

        return ObjectionGuidance(
            intent=intent,
            guidance_text=guidance_text,
            max_attempts=max_attempts,
            should_end_call=should_end_call,
            next_stage=next_stage if not should_end_call else "end_call",
            compliance_warning=compliance_notes,
        )

    def reset_attempts(self, intent: Optional[str] = None) -> None:
        """Reset attempt counters.

        Args:
            intent: If provided, reset only this intent's counter.
                Otherwise, reset all counters.
        """
        if intent:
            self._attempt_counts[intent] = 0
        else:
            self._attempt_counts.clear()

    def get_attempt_count(self, intent: str) -> int:
        """Return the current attempt count for a given intent."""
        return self._attempt_counts.get(intent, 0)

    @property
    def known_intents(self) -> list[str]:
        """Return a list of all known objection intent names."""
        return list(self._objection_defs.keys())

    def get_allowed_responses(self, intent: str) -> list[str]:
        """Return the list of allowed example responses for an intent.

        Args:
            intent: The objection intent string.

        Returns:
            List of allowed response strings.

        Raises:
            ValueError: If the intent is not recognized.
        """
        defn = self._objection_defs.get(intent)
        if defn is None:
            raise ValueError(f"Unknown objection intent: {intent!r}")
        return list(defn.get("allowed_responses", []))

    def get_forbidden_responses(self, intent: str) -> list[str]:
        """Return the list of forbidden responses for an intent.

        Args:
            intent: The objection intent string.

        Returns:
            List of forbidden response strings.

        Raises:
            ValueError: If the intent is not recognized.
        """
        defn = self._objection_defs.get(intent)
        if defn is None:
            raise ValueError(f"Unknown objection intent: {intent!r}")
        return list(defn.get("forbidden_responses", []))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_definitions(self, yaml_path: str | Path) -> None:
        """Load objection definitions from YAML and index by intent."""
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Objection definitions YAML not found: {path}"
            )

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        if not data or "objections" not in data:
            raise ValueError(
                f"Invalid objection YAML — missing 'objections' key: {path}"
            )

        for defn in data["objections"]:
            intent = defn.get("intent")
            if intent:
                self._objection_defs[intent] = defn

    def _should_end_call(
        self,
        intent: str,
        attempt_count: int,
        max_attempts: int,
        next_stage: str,
    ) -> bool:
        """Determine whether the call should end based on policy rules.

        Rules:
        - DNC/remove_me always ends immediately (max_attempts=0)
        - angry ends after one apology
        - Any intent exceeding max_attempts ends
        - next_stage of 'end_call' always ends
        """
        # Immediate end intents
        if next_stage == "end_call":
            return True

        # Max attempts exceeded
        if attempt_count >= max_attempts:
            # For remove_me (max_attempts=0), always end
            if max_attempts == 0:
                return True
            # For angry (max_attempts=1), end after the one apology
            if intent == "angry":
                return True

        return False

    @staticmethod
    def _build_guidance(defn: dict, attempt_count: int, max_attempts: int) -> str:
        """Build human-readable guidance text for Dana."""
        intent = defn.get("intent", "unknown")
        goal = defn.get("goal", "")
        allowed = defn.get("allowed_responses", [])
        forbidden = defn.get("forbidden_responses", [])

        parts: list[str] = []

        # Goal
        if goal:
            parts.append(f"Goal: {goal}")

        # Attempt status
        if max_attempts == 0:
            parts.append("Action: End the call immediately and politely.")
        elif attempt_count >= max_attempts:
            parts.append(
                f"Max attempts reached ({attempt_count}/{max_attempts}). "
                "Respect their decision and wrap up gracefully."
            )
        else:
            remaining = max_attempts - attempt_count
            parts.append(
                f"Attempt {attempt_count + 1} of {max_attempts}. "
                f"{remaining} rebuttal(s) remaining."
            )

        # Example responses
        if allowed:
            # Suggest a response based on attempt count
            idx = min(attempt_count, len(allowed) - 1)
            parts.append(f"Suggested response: {allowed[idx]}")

        # Warnings
        if forbidden:
            parts.append(
                "NEVER say: " + " | ".join(f'"{f}"' for f in forbidden[:3])
            )

        return "\n".join(parts)
