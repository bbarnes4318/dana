"""Campaign scheduling logic, daily caps, and lead eligibility checks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from dialer.timezone_policy import TimezonePolicy
from compliance.dnc_registry import InternalDNCRegistry

logger = logging.getLogger(__name__)


class CampaignScheduler:
    """Orchestrates campaign-level checks and lead selection criteria."""

    @staticmethod
    def is_campaign_active(campaign: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        """Verify campaign status, active days, and daily caps."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 1. Status check
        status = campaign.get("status", "draft")
        if status not in ("ready", "running", "active"):
            return False

        if campaign.get("is_paused", False):
            return False

        # 2. Daily Call Cap Check
        daily_call_cap = campaign.get("daily_call_cap", 100)
        calls_started_today = campaign.get("calls_started_today", 0)
        if calls_started_today >= daily_call_cap:
            return False

        # 3. Timezone policy day checks for campaign timezone
        campaign_tz = campaign.get("timezone", "America/New_York")
        try:
            local_time = TimezonePolicy.get_local_time(campaign_tz, now)
            allowed_days = campaign.get("allowed_days") or ["mon", "tue", "wed", "thu", "fri"]
            local_day = local_time.strftime("%a").lower()
            if local_day not in allowed_days:
                return False
        except Exception as e:
            logger.error("Error checking campaign timezone active window: %s", e)
            return False

        return True

    @staticmethod
    async def is_lead_eligible(
        lead: Union[dict, Any],
        campaign: Dict[str, Any],
        now: Optional[datetime] = None,
        dnc_registry: Optional[InternalDNCRegistry] = None
    ) -> bool:
        """Validate if a lead is eligible for immediate dialing.
        
        Checks:
        - Lead status (DNC / wrong number / completed are suppressed)
        - Max attempt limits
        - Retry policy cooldowns (next_attempt_at)
        - Timezone policies (allowed calling window hours and days)
        - DNC registry suppression
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        def get_val(key: str) -> Optional[Any]:
            if isinstance(lead, dict):
                return lead.get(key)
            return getattr(lead, key, None)

        # 1. Suppression of DNC/Wrong Number/Completed Leads
        status = get_val("status")
        if status in ("dnc", "wrong_number", "completed", "suppressed", "do_not_call"):
            return False

        # 2. Attempts Limit Check
        attempts = get_val("attempt_count") or get_val("attempts") or 0
        campaign_max = campaign.get("max_attempts", 3)
        lead_max = get_val("max_attempts")
        max_attempts = lead_max if lead_max is not None else campaign_max

        if attempts >= max_attempts:
            return False

        # 3. Retry Cooldown Check
        next_attempt_at = get_val("next_attempt_at")
        if next_attempt_at:
            if isinstance(next_attempt_at, str):
                try:
                    if next_attempt_at.endswith("Z"):
                        next_attempt_at = next_attempt_at.replace("Z", "+00:00")
                    next_attempt_at = datetime.fromisoformat(next_attempt_at)
                except ValueError:
                    pass
            if isinstance(next_attempt_at, datetime):
                if next_attempt_at.tzinfo is None:
                    next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
                if next_attempt_at > now:
                    return False

        # 4. DNC Registry Check (Scrubbing check)
        phone = get_val("lead_phone_e164") or get_val("phone_e164") or get_val("phone_number")
        if dnc_registry and phone:
            campaign_id = campaign.get("campaign_id") or campaign.get("id")
            if await dnc_registry.contains(phone, campaign_id=campaign_id):
                return False

        # 5. Timezone Window Check
        if not TimezonePolicy.is_allowed_to_call(lead, campaign, now):
            return False

        return True

    @classmethod
    async def get_eligible_leads(
        cls,
        leads: List[Union[dict, Any]],
        campaign: Dict[str, Any],
        now: Optional[datetime] = None,
        dnc_registry: Optional[InternalDNCRegistry] = None
    ) -> List[Union[dict, Any]]:
        """Filter and return only the eligible leads from a list."""
        eligible = []
        for lead in leads:
            if await cls.is_lead_eligible(lead, campaign, now, dnc_registry):
                eligible.append(lead)
        return eligible
