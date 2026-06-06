"""Optimizes caller ID selection and campaign pacing based on performance."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AnswerRateOptimizer:
    """Selects and optimizes caller ID allocation and pacing based on connection rates."""

    @staticmethod
    def recommend_caller_id(
        caller_ids: List[Dict[str, Any]],
        campaign_config: Dict[str, Any],
        now: Optional[datetime] = None
    ) -> Optional[str]:
        """Recommend the best caller ID from a pool of eligible caller IDs.
        
        Prioritizes:
        1. Non-cooldown, active status, daily limit not reached.
        2. Highest answer rate (above minimum threshold).
        3. Lowest DNC/complaint rate.
        4. Least recently used (LRU) tie-breaker for new/equal caller IDs.
        """
        if not caller_ids:
            return None

        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        daily_limit = campaign_config.get("caller_id_daily_limit", 200)

        # Filter to eligible caller IDs
        eligible = []
        for cid in caller_ids:
            if cid.get("status") == "inactive":
                continue

            # Cooldown check
            cooldown_until = cid.get("cooldown_until")
            if cooldown_until:
                if isinstance(cooldown_until, str):
                    try:
                        cooldown_until = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
                    except ValueError:
                        pass
                if isinstance(cooldown_until, datetime):
                    if cooldown_until.tzinfo is None:
                        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
                    if cooldown_until > now:
                        continue

            # Daily limit check
            if cid.get("daily_call_count", 0) >= daily_limit:
                continue

            eligible.append(cid)

        if not eligible:
            return None

        # Sort key to find the optimal caller ID:
        # We want to maximize answer_rate, minimize dnc_rate, and use LRU as a tie-breaker.
        # Python sort is stable, so we can sort by LRU first, then by rates.
        def get_lru_time(c: Dict[str, Any]) -> datetime:
            last_used = c.get("last_used_at")
            if not last_used:
                return datetime.min.replace(tzinfo=timezone.utc)
            if isinstance(last_used, str):
                try:
                    last_used = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
                except ValueError:
                    pass
            if isinstance(last_used, datetime):
                if last_used.tzinfo is None:
                    last_used = last_used.replace(tzinfo=timezone.utc)
                return last_used
            return datetime.min.replace(tzinfo=timezone.utc)

        # LRU Sort first (older/none first)
        eligible.sort(key=get_lru_time)

        # Then sort by performance. Since we want to MAXIMIZE answer_rate and MINIMIZE dnc_rate,
        # we can compute a score: answer_rate - dnc_rate (or weighted).
        # We also prioritize caller IDs that have enough calls to have a reliable rate,
        # but don't starve new caller IDs (which have total_calls == 0).
        def get_performance_score(c: Dict[str, Any]) -> float:
            total_calls = c.get("total_calls", 0)
            if total_calls == 0:
                # Give new numbers a slight bonus to test them
                return 0.1
            
            ans_rate = c.get("answer_rate", 0.0)
            dnc_rate = c.get("dnc_rate", 0.0)
            comp_rate = c.get("complaint_rate", 0.0)
            
            # Penalize DNC and complaint rates heavily
            return ans_rate - (2.0 * dnc_rate) - (5.0 * comp_rate)

        eligible.sort(key=get_performance_score, reverse=True)

        return eligible[0]["caller_id"]

    @staticmethod
    def adjust_pacing(
        campaign: Dict[str, Any],
        recent_calls: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Dynamically adjust pacing parameters based on recent answer rates.
        
        If recent answer rate is extremely low, reduce concurrency to protect caller IDs from spam flags.
        If recent answer rate is healthy, allow scaling up to max configured concurrent limits.
        """
        max_concurrent = campaign.get("max_concurrent_calls", 5)
        cpm = campaign.get("calls_per_minute", 20)

        if not recent_calls:
            return {"max_concurrent_calls": max_concurrent, "calls_per_minute": cpm}

        total_recent = len(recent_calls)
        answers = sum(1 for c in recent_calls if c.get("outcome") == "human_answered")
        answer_rate = answers / total_recent if total_recent > 0 else 0.0

        suggested_concurrent = max_concurrent
        suggested_cpm = cpm

        # If we have at least 5 recent calls and answer rate is very low (e.g. < 5%)
        if total_recent >= 5:
            if answer_rate < 0.05:
                # Throttle down
                suggested_concurrent = max(1, int(max_concurrent * 0.5))
                suggested_cpm = max(2, int(cpm * 0.5))
                logger.info(
                    "Throttling pacing due to low answer rate (%.1f%%). Concurrency: %d -> %d",
                    answer_rate * 100,
                    max_concurrent,
                    suggested_concurrent
                )
            elif answer_rate > 0.20:
                # Answer rate is very healthy, run at full capacity
                suggested_concurrent = max_concurrent
                suggested_cpm = cpm

        return {
            "max_concurrent_calls": suggested_concurrent,
            "calls_per_minute": suggested_cpm,
            "recent_answer_rate": answer_rate
        }
