"""Pydantic schema for training notes extracted from video transcripts and training materials."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class TrainingNote(BaseModel):
    """A single training lesson extracted from sales training material.

    Each note captures a specific sales lesson including examples of good
    and bad responses, the applicable call stage, and any compliance risks.
    """

    source: str = Field(
        ...,
        description="File path or URL of the source material",
    )
    topic: str = Field(
        ...,
        description="Topic category (e.g. 'objection_handling', 'opening', 'compliance')",
    )
    sales_lesson: str = Field(
        ...,
        description="The lesson learned from the training material",
    )
    bad_response_example: str = Field(
        ...,
        description="Example of what NOT to say",
    )
    good_response_example: str = Field(
        ...,
        description="Example of what TO say",
    )
    call_stage: Optional[str] = Field(
        default=None,
        description="Which call stage this applies to (e.g. 'opening', 'qualifying', 'closing')",
    )
    objection_type: Optional[str] = Field(
        default=None,
        description="Which objection type this applies to, if applicable",
    )
    compliance_risk: Optional[str] = Field(
        default=None,
        description="Any compliance risk identified in the training material",
    )
    use_in_live_call: bool = Field(
        default=True,
        description="Whether this note should be used during live calls",
    )
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this note was extracted",
    )
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this training note",
    )
