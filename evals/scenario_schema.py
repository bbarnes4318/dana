"""Pydantic models for eval scenario definitions.

Scenarios are authored as YAML files and loaded into these models for
type-safe access by the :class:`ScenarioRunner`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class EvalTurn(BaseModel):
    """A single conversational turn in an eval scenario."""

    speaker: Literal["prospect", "agent"]
    text: str


class EvalAssertion(BaseModel):
    """A single assertion to check against agent behaviour."""

    type: Literal[
        "no_forbidden_phrase",
        "no_approval_claim",
        "no_premium_quote",
        "one_question_max",
        "response_under_word_limit",
        "correct_next_stage",
        "dnc_honored",
        "callback_captured",
        "transfer_only_when_ready",
        # New assertion types
        "required_profile_field",
        "profile_field_equals",
        "final_outcome",
        "final_stage",
        "no_sensitive_info_request",
        "no_human_claim",
        "no_licensed_claim",
        "required_identity_response",
        "no_transfer_before_ready",
        "transfer_failure_callback_offer",
        "disqualified_only_after_confirmation",
        "dnc_stops_call",
        "wrong_number_stops_call",
        "no_agent_turn_after_terminal",
        "no_markdown",
        "max_one_question",
        "max_agent_words",
        "required_phrase",
        "forbidden_phrase",
        "voicemail_does_not_start_agent",
    ]
    expected: Any = None
    params: dict[str, Any] = Field(default_factory=dict)


class EvalScenario(BaseModel):
    """Full eval scenario loaded from a YAML file.

    Attributes:
        name: Human-readable scenario name.
        description: What this scenario tests.
        prospect_persona: Brief description of the simulated prospect.
        initial_stage: The :class:`CallStage` value to start from.
        turns: Ordered list of conversational turns.
        assertions: Checks to run after each agent response.
        expected_final_stage: The stage the call should end in.
        tags: Categorical tags for filtering/grouping scenarios.
    """

    name: str
    description: str
    prospect_persona: str
    initial_stage: str
    turns: list[EvalTurn]
    assertions: list[EvalAssertion]
    expected_final_stage: str
    tags: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> EvalScenario:
        """Load an :class:`EvalScenario` from a YAML file.

        Parameters
        ----------
        path:
            Path to a ``.yaml`` file conforming to the scenario schema.

        Returns
        -------
        EvalScenario
            Validated scenario instance.
        """
        filepath = Path(path)
        with open(filepath, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.model_validate(data)
