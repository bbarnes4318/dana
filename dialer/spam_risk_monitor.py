"""Monitors and evaluates spam risk metrics for caller IDs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dialer.schemas import SpamRiskReport

logger = logging.getLogger(__name__)


class SpamRiskMonitor:
    """Tracks performance anomalies indicating that a caller ID has been flagged as spam."""

    @staticmethod
    def calculate_spam_risk_score(
        caller_id_metrics: Dict[str, Any],
        recent_calls: List[Dict[str, Any]],
        now: Optional[datetime] = None
    ) -> SpamRiskReport:
        """Calculate the spam risk score (0.0 to 1.0) for a caller ID.
        
        Evaluates:
        1. Answer Rate Drop: Sudden drop in recent answer rate compared to lifetime average.
        2. Short Call Hangup Rate: Calls answered but hung up in < 10 seconds (signals spam labels).
        3. DNC/Complaint Rate: Leads flagging calls as DNC immediately.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        caller_id = caller_id_metrics.get("caller_id", "unknown")
        total_calls = caller_id_metrics.get("total_calls", 0)
        lifetime_ans_rate = caller_id_metrics.get("answer_rate", 0.0)

        # 1. Evaluate recent calls (e.g., last 10-20 calls)
        recent_total = len(recent_calls)
        recent_answers = 0
        recent_short_hangups = 0
        recent_dncs = 0

        for call in recent_calls:
            outcome = call.get("outcome")
            duration = call.get("duration_seconds") or call.get("duration") or 0.0

            if outcome == "human_answered":
                recent_answers += 1
                # Short calls under 10 seconds are highly indicative of user hangup due to spam labels
                if 0.0 < float(duration) < 10.0:
                    recent_short_hangups += 1
            elif outcome == "dnc":
                recent_dncs += 1

        # Calculate metrics
        recent_ans_rate = recent_answers / recent_total if recent_total > 0 else 0.0
        short_call_hangup_rate = recent_short_hangups / recent_answers if recent_answers > 0 else 0.0
        dnc_complaint_rate = recent_dncs / recent_total if recent_total > 0 else 0.0

        # Check for sudden answer rate drop
        # Triggered if lifetime answer rate is decent (> 5%) and recent answer rate drops by 50%+ relative
        # Requires at least 5 recent calls to be statistically meaningful.
        ans_rate_drop_detected = False
        if recent_total >= 5 and lifetime_ans_rate >= 0.05:
            relative_drop = (lifetime_ans_rate - recent_ans_rate) / lifetime_ans_rate
            if relative_drop >= 0.50:
                ans_rate_drop_detected = True

        # Calculate risk score (0.0 to 1.0)
        # Weights:
        # - Answer rate drop: 0.4
        # - Short call hangup rate: 0.4
        # - DNC complaint rate: 0.2 (DNCs are heavily penalized)
        score = 0.0
        if ans_rate_drop_detected:
            score += 0.4
        score += min(0.4, short_call_hangup_rate * 0.4)
        score += min(0.2, dnc_complaint_rate * 2.0)

        # Add penalty for direct complaint flags in lifetime metrics if any
        total_dncs = caller_id_metrics.get("total_dncs", 0)
        total_complaints = caller_id_metrics.get("total_complaints", 0)
        if total_calls > 0:
            lifetime_dnc_comp_rate = (total_dncs + total_complaints) / total_calls
            score += min(0.2, lifetime_dnc_comp_rate * 2.0)

        score = min(1.0, max(0.0, score))

        # Classify risk status
        if score >= 0.7:
            status = "high_risk"
        elif score >= 0.3:
            status = "medium_risk"
        else:
            status = "low_risk"

        return SpamRiskReport(
            caller_id=caller_id,
            score=score,
            answer_rate_drop_detected=ans_rate_drop_detected,
            short_call_hangup_rate=short_call_hangup_rate,
            dnc_complaint_rate=dnc_complaint_rate,
            status=status,
            calculated_at=now
        )
