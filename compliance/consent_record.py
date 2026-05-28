"""Consent record schema for TCPA compliance."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class ConsentRecord(BaseModel):
    """Represents a lead's record of explicit marketing consent (e.g. TCPA/TrustedForm)."""

    consent_artifact_id: str
    lead_id: str
    phone_e164: str
    source_vendor: str
    consent_text: str
    consent_timestamp: str  # ISO 8601 string
    landing_page_url: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    tcpa_consent_version: Optional[str] = None
    campaign_id: Optional[str] = None
