"""Objection classifier for detecting prospect objections from utterances.

Uses keyword-based matching against objection definitions loaded from YAML
to classify prospect utterances into objection intents.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Default path to the objection definitions YAML
_DEFAULT_YAML_PATH = Path(__file__).resolve().parent.parent / "kb" / "objections" / "final_expense_objections.yaml"


@dataclass
class ObjectionMatch:
    """Result of an objection classification attempt."""

    intent: str
    confidence: float
    matched_keywords: list[str] = field(default_factory=list)


class ObjectionClassifier:
    """Classifies prospect utterances into objection intents using keyword matching.

    Loads objection definitions from a YAML file and uses keyword-based matching
    to determine which objection intent (if any) a given utterance maps to.

    Args:
        yaml_path: Path to the YAML file containing objection definitions.
            Defaults to the bundled final_expense_objections.yaml.
        confidence_threshold: Minimum confidence score (0.0–1.0) required to
            return a classification. Defaults to 0.3.
    """

    def __init__(
        self,
        yaml_path: str | Path | None = None,
        confidence_threshold: float = 0.3,
    ) -> None:
        self._confidence_threshold = confidence_threshold
        self._objection_defs: list[dict] = []
        self._load_definitions(yaml_path or _DEFAULT_YAML_PATH)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, utterance: str) -> Optional[str]:
        """Classify an utterance and return the objection intent, or None.

        Args:
            utterance: The raw text spoken by the prospect.

        Returns:
            The objection intent string (e.g. ``"not_interested"``) if an
            objection is detected above the confidence threshold, otherwise
            ``None``.
        """
        match = self._score_utterance(utterance)
        if match is not None and match.confidence >= self._confidence_threshold:
            return match.intent
        return None

    def classify_with_details(self, utterance: str) -> Optional[ObjectionMatch]:
        """Classify an utterance and return full match details, or None.

        Args:
            utterance: The raw text spoken by the prospect.

        Returns:
            An :class:`ObjectionMatch` if an objection is detected above the
            confidence threshold, otherwise ``None``.
        """
        match = self._score_utterance(utterance)
        if match is not None and match.confidence >= self._confidence_threshold:
            return match
        return None

    @property
    def known_intents(self) -> list[str]:
        """Return a list of all known objection intent names."""
        return [d["intent"] for d in self._objection_defs]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_definitions(self, yaml_path: str | Path) -> None:
        """Load objection definitions from a YAML file."""
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

        self._objection_defs = data["objections"]

        # Pre-compile keyword patterns for faster matching
        for defn in self._objection_defs:
            # Sort keywords longest-first so longer phrases match preferentially
            keywords = sorted(defn.get("keywords", []), key=len, reverse=True)
            # Build a compiled regex for each keyword (word-boundary wrapped)
            defn["_compiled_keywords"] = [
                (kw, re.compile(rf"\b{re.escape(kw.lower())}\b"))
                for kw in keywords
            ]

    def _score_utterance(self, utterance: str) -> Optional[ObjectionMatch]:
        """Score an utterance against all objection definitions.

        Returns the best-matching :class:`ObjectionMatch`, or ``None`` if
        no keywords matched at all.
        """
        if not utterance or not utterance.strip():
            return None

        utterance_lower = utterance.lower().strip()
        best_match: Optional[ObjectionMatch] = None

        for defn in self._objection_defs:
            matched_keywords: list[str] = []
            for kw, pattern in defn["_compiled_keywords"]:
                if pattern.search(utterance_lower):
                    matched_keywords.append(kw)

            if not matched_keywords:
                continue

            confidence = self._compute_confidence(
                utterance_lower, matched_keywords, defn
            )

            if best_match is None or confidence > best_match.confidence:
                best_match = ObjectionMatch(
                    intent=defn["intent"],
                    confidence=confidence,
                    matched_keywords=matched_keywords,
                )

        return best_match

    @staticmethod
    def _compute_confidence(
        utterance: str,
        matched_keywords: list[str],
        defn: dict,
    ) -> float:
        """Compute a confidence score for a keyword match.

        The score is based on:
        - Proportion of the utterance covered by the longest matched keyword
        - Base confidence of 0.5 for any word-boundary match
        - Exact match bonus

        Returns a float between 0.0 and 1.0.
        """
        if not matched_keywords:
            return 0.0

        # Coverage: how much of the utterance is covered by the longest matched keyword
        longest_kw_len = max(len(kw) for kw in matched_keywords)
        utterance_len = max(len(utterance), 1)
        coverage = min(longest_kw_len / utterance_len, 1.0)

        # Exact match bonus — if the utterance is essentially just a keyword
        exact_bonus = 0.0
        for kw in matched_keywords:
            if utterance.strip() == kw.lower().strip():
                exact_bonus = 0.2
                break

        # Weighted combination
        confidence = 0.5 + (coverage * 0.3) + exact_bonus

        # Clamp to [0.0, 1.0]
        return min(max(confidence, 0.0), 1.0)
