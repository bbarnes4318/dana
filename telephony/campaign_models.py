from typing import Any, Optional, List, Dict
from pydantic import BaseModel, Field


class CampaignActionResult(BaseModel):
    """The structured outcome of an operator action on a campaign."""

    action: str
    success: bool
    campaign_id: Optional[str] = None
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    message: str
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class CampaignSummary(BaseModel):
    """Analytics and status rollup summary for a campaign."""

    campaign_id: str
    name: str
    status: str
    total_leads: int = 0
    queued_leads: int = 0
    active_calls: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    dnc_count: int = 0
    wrong_number_count: int = 0
    callback_count: int = 0
    transfer_count: int = 0
    calls_started_today: int = 0
    daily_call_cap: int = 100
    max_concurrent_calls: int = 1
