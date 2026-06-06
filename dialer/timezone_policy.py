"""Timezone resolution and calling window compliance validation."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any, Dict, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from compliance.calling_window import resolve_lead_timezone
from dialer.schemas import TimezoneWindow

logger = logging.getLogger(__name__)


class TimezonePolicy:
    """Evaluates timezone rules to determine if a lead can be called at a specific time."""

    @staticmethod
    def resolve_timezone(lead: Union[dict, Any]) -> Tuple[Optional[str], str, str]:
        """Resolve the lead's timezone using state or area code mapping."""
        return resolve_lead_timezone(lead)

    @staticmethod
    def get_local_time(timezone_str: str, now: datetime) -> datetime:
        """Get the local time for a timezone from a UTC/aware datetime."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        tz = ZoneInfo(timezone_str)
        return now.astimezone(tz)

    @classmethod
    def get_timezone_window(
        cls,
        lead: Union[dict, Any],
        campaign: Dict[str, Any],
        now: Optional[datetime] = None
    ) -> TimezoneWindow:
        """Generate a TimezoneWindow object for a lead's calling status."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        tz_str, _, _ = cls.resolve_timezone(lead)
        if not tz_str:
            return TimezoneWindow(
                timezone_str="UTC",
                local_time=now,
                is_allowed=False,
                reason="missing_timezone_no_fallback"
            )

        try:
            local_time_val = cls.get_local_time(tz_str, now)
        except Exception as e:
            logger.error("Failed to parse ZoneInfo for timezone %s: %s", tz_str, e)
            return TimezoneWindow(
                timezone_str=tz_str,
                local_time=now,
                is_allowed=False,
                reason=f"invalid_timezone_format: {e}"
            )

        # 1. Day of Week Check
        allowed_days = campaign.get("allowed_days") or ["mon", "tue", "wed", "thu", "fri"]
        local_day = local_time_val.strftime("%a").lower()
        if local_day not in allowed_days:
            return TimezoneWindow(
                timezone_str=tz_str,
                local_time=local_time_val,
                is_allowed=False,
                reason=f"day_not_allowed: {local_day}"
            )

        # 2. Strict TCPA Hours Check (Never call before 8 AM or after 9 PM local time)
        local_hour = local_time_val.hour
        local_minute = local_time_val.minute
        local_time_of_day = time(local_hour, local_minute)

        if local_time_of_day < time(8, 0) or local_time_of_day >= time(21, 0):
            return TimezoneWindow(
                timezone_str=tz_str,
                local_time=local_time_val,
                is_allowed=False,
                reason=f"tcpa_violation_hours: {local_time_val.strftime('%H:%M')}"
            )

        # 3. Campaign Hours Check
        # Supports tuple (start_hour, end_hour) or string "HH:MM"
        allowed_hours = campaign.get("allowed_calling_hours")
        window_start = campaign.get("calling_window_start")
        window_end = campaign.get("calling_window_end")

        start_time_limit = time(8, 0)
        end_time_limit = time(20, 0)  # Default campaign ceiling is 8 PM local

        if allowed_hours:
            try:
                start_hour, end_hour = allowed_hours
                start_time_limit = time(start_hour, 0)
                end_time_limit = time(end_hour, 0)
            except (ValueError, TypeError):
                pass
        elif window_start and window_end:
            try:
                sh, sm = map(int, window_start.split(":"))
                eh, em = map(int, window_end.split(":"))
                start_time_limit = time(sh, sm)
                end_time_limit = time(eh, em)
            except Exception:
                pass

        if local_time_of_day < start_time_limit or local_time_of_day >= end_time_limit:
            return TimezoneWindow(
                timezone_str=tz_str,
                local_time=local_time_val,
                is_allowed=False,
                reason=f"outside_campaign_window: {local_time_val.strftime('%H:%M')}"
            )

        return TimezoneWindow(
            timezone_str=tz_str,
            local_time=local_time_val,
            is_allowed=True
        )

    @classmethod
    def is_allowed_to_call(
        cls,
        lead: Union[dict, Any],
        campaign: Dict[str, Any],
        now: Optional[datetime] = None
    ) -> bool:
        """Helper returning True if caller window allows dialing, False otherwise."""
        window = cls.get_timezone_window(lead, campaign, now)
        return window.is_allowed
