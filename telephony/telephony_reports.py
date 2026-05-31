from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from storage.repository import Repository

class TelephonyReports:
    """Service for generating detailed analytics and performance reports for campaigns."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository or Repository()

    async def get_campaign_analytics(self, campaign_id: str) -> Dict[str, Any]:
        """Get comprehensive analytics for a campaign."""
        campaign = await self.repository.get_outbound_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
        attempts = await self.repository.query_call_attempts({"campaign_id": campaign_id})

        total_leads = len(leads)
        total_attempts = len(attempts)

        # Status counts
        lead_status_counts: Dict[str, int] = {}
        for lead in leads:
            status = lead.get("status", "new")
            lead_status_counts[status] = lead_status_counts.get(status, 0) + 1

        # Attempt outcome counts
        outcome_counts: Dict[str, int] = {}
        duration_sum = 0
        duration_count = 0
        transfers_attempted = 0
        transfers_successful = 0

        for attempt in attempts:
            outcome = attempt.get("outcome") or "unknown"
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

            duration = attempt.get("duration_seconds")
            if duration is not None:
                duration_sum += duration
                duration_count += 1

            if attempt.get("transfer_attempted"):
                transfers_attempted += 1
            if attempt.get("transfer_successful"):
                transfers_successful += 1

        avg_duration = duration_sum / duration_count if duration_count > 0 else 0.0

        # Calculations
        answered_outcomes = {"answered", "completed", "transferred", "sale", "not_interested", "callback"}
        total_answered = sum(outcome_counts.get(out, 0) for out in answered_outcomes)
        answer_rate = (total_answered / total_attempts) * 100.0 if total_attempts > 0 else 0.0
        transfer_rate = (transfers_successful / total_answered) * 100.0 if total_answered > 0 else 0.0

        # Hourly distribution of attempts (based on UTC/local hour of attempt start)
        hourly_attempts: Dict[int, int] = {h: 0 for h in range(24)}
        for attempt in attempts:
            started_at = attempt.get("started_at") or attempt.get("created_at")
            if started_at:
                try:
                    if isinstance(started_at, datetime):
                        dt = started_at
                    else:
                        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    hourly_attempts[dt.hour] += 1
                except Exception:
                    pass

        return {
            "campaign_id": campaign_id,
            "campaign_name": campaign.get("name", ""),
            "status": campaign.get("status", "draft"),
            "total_leads": total_leads,
            "total_attempts": total_attempts,
            "avg_duration_seconds": round(avg_duration, 1),
            "lead_status_counts": lead_status_counts,
            "attempt_outcome_counts": outcome_counts,
            "answer_rate_percent": round(answer_rate, 2),
            "transfer_success_rate_percent": round(transfer_rate, 2),
            "transfers_attempted": transfers_attempted,
            "transfers_successful": transfers_successful,
            "hourly_attempts_distribution": hourly_attempts,
            "daily_call_cap": campaign.get("daily_call_cap", 100),
            "max_concurrent_calls": campaign.get("max_concurrent_calls", 1),
        }

    async def get_recent_attempts(self, campaign_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent call attempts, redacting phone numbers for compliance."""
        filters = {}
        if campaign_id:
            filters["campaign_id"] = campaign_id

        attempts = await self.repository.query_call_attempts(filters)
        
        # Sort attempts by created_at or started_at desc if available
        def parse_date(x):
            d = x.get("created_at") or x.get("started_at")
            if not d:
                return datetime.min.replace(tzinfo=timezone.utc)
            if isinstance(d, datetime):
                return d
            try:
                return datetime.fromisoformat(d.replace("Z", "+00:00"))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        attempts_sorted = sorted(attempts, key=parse_date, reverse=True)

        redacted = []
        for att in attempts_sorted[:limit]:
            att_dict = dict(att)
            # Standard compliance redaction
            if att_dict.get("phone_number_redacted"):
                att_dict["phone_number"] = att_dict["phone_number_redacted"]
            elif "phone_number" in att_dict:
                phone = att_dict["phone_number"]
                if len(phone) > 4:
                    att_dict["phone_number"] = phone[:-4] + "****"
                else:
                    att_dict["phone_number"] = "****"
            redacted.append(att_dict)
        return redacted
