from datetime import datetime, timezone
import uuid
from typing import Any, Optional, Dict, List

from storage.repository import Repository
from telephony.campaign_models import CampaignActionResult, CampaignSummary


class TelephonyCampaignService:
    """Service layer for managing telephony provider configs and outbound campaigns."""

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    # -------------------------------------------------------------------------
    # Provider Configs
    # -------------------------------------------------------------------------

    async def create_provider_config(self, **kwargs: Any) -> str:
        """Create a new TelephonyProviderConfig."""
        config_id = kwargs.get("id") or str(uuid.uuid4())
        kwargs["id"] = config_id
        kwargs.setdefault("status", "draft")
        kwargs.setdefault("telnyx_phone_numbers", [])
        kwargs.setdefault("room_name_template", "dana-{campaign_id}-{lead_id}-{attempt_id}")
        kwargs.setdefault("metadata", {})
        kwargs.setdefault("created_at", datetime.now(timezone.utc))
        kwargs.setdefault("updated_at", datetime.now(timezone.utc))
        return await self.repository.save_telephony_provider_config(**kwargs)

    async def get_provider_config(self, provider_config_id: str) -> Optional[dict]:
        """Retrieve a TelephonyProviderConfig by ID."""
        return await self.repository.get_telephony_provider_config(provider_config_id)

    async def list_provider_configs(self, limit: int = 50) -> list[dict]:
        """List recent TelephonyProviderConfigs."""
        return await self.repository.list_recent_telephony_provider_configs(limit=limit)

    # -------------------------------------------------------------------------
    # Campaigns CRUD
    # -------------------------------------------------------------------------

    async def create_campaign(self, **kwargs: Any) -> str:
        """Create a new OutboundCampaign in draft status."""
        campaign_id = kwargs.get("id") or str(uuid.uuid4())
        kwargs["id"] = campaign_id
        kwargs.setdefault("status", "draft")
        kwargs.setdefault("campaign_type", "final_expense_outbound")
        kwargs.setdefault("prompt_name", "final_expense_alex")
        kwargs.setdefault("max_concurrent_calls", 1)
        kwargs.setdefault("daily_call_cap", 100)
        kwargs.setdefault("calls_started_today", 0)
        kwargs.setdefault("timezone", "America/New_York")
        kwargs.setdefault("calling_window_start", "09:30")
        kwargs.setdefault("calling_window_end", "18:00")
        kwargs.setdefault("allowed_days", ["mon", "tue", "wed", "thu", "fri"])
        kwargs.setdefault("retry_policy", {})
        kwargs.setdefault("dnc_scrub_required", True)
        kwargs.setdefault("require_live_mode", True)
        kwargs.setdefault("metadata", {})
        kwargs.setdefault("created_at", datetime.now(timezone.utc))
        kwargs.setdefault("updated_at", datetime.now(timezone.utc))

        saved_id = await self.repository.save_outbound_campaign(**kwargs)
        
        # Log control event
        await self.repository.save_campaign_control_event(
            campaign_id=campaign_id,
            event_type="created",
            operator=kwargs.get("operator", "system"),
            reason="Campaign created",
            new_status="draft",
        )
        return saved_id

    async def get_campaign(self, campaign_id: str) -> Optional[dict]:
        """Retrieve an OutboundCampaign by ID."""
        return await self.repository.get_outbound_campaign(campaign_id)

    async def list_campaigns(self, status: str | None = None, limit: int = 50) -> list[dict]:
        """List campaigns, optionally filtered by status."""
        if status:
            return await self.repository.query_outbound_campaigns({"status": status})
        return await self.repository.list_recent_outbound_campaigns(limit=limit)

    async def update_campaign(self, campaign_id: str, updates: dict, operator: str | None = None) -> CampaignActionResult:
        """Update fields of a campaign."""
        campaign = await self.repository.get_outbound_campaign(campaign_id)
        if not campaign:
            return CampaignActionResult(
                action="update",
                success=False,
                campaign_id=campaign_id,
                message=f"Campaign {campaign_id} not found.",
                error="NOT_FOUND",
            )

        # Do not allow modifying status directly via generic update
        if "status" in updates:
            updates.pop("status")

        campaign.update(updates)
        campaign["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        await self.repository.save_outbound_campaign(**campaign)
        
        return CampaignActionResult(
            action="update",
            success=True,
            campaign_id=campaign_id,
            message="Campaign updated successfully.",
            data={"campaign": campaign},
        )

    # -------------------------------------------------------------------------
    # Campaign Lifecycle Control
    # -------------------------------------------------------------------------

    async def _transition_status(
        self,
        campaign_id: str,
        allowed_from: List[str],
        target_status: str,
        operator: str,
        action_name: str,
        reason: Optional[str] = None,
        additional_updates: Optional[dict] = None,
    ) -> CampaignActionResult:
        if not operator or not operator.strip():
            return CampaignActionResult(
                action=action_name,
                success=False,
                campaign_id=campaign_id,
                message="Operator name is required.",
                error="OPERATOR_REQUIRED",
            )

        campaign = await self.repository.get_outbound_campaign(campaign_id)
        if not campaign:
            return CampaignActionResult(
                action=action_name,
                success=False,
                campaign_id=campaign_id,
                message=f"Campaign {campaign_id} not found.",
                error="NOT_FOUND",
            )

        current_status = campaign.get("status", "draft")
        if current_status not in allowed_from:
            return CampaignActionResult(
                action=action_name,
                success=False,
                campaign_id=campaign_id,
                previous_status=current_status,
                message=f"Cannot transition campaign {campaign_id} from {current_status} to {target_status}.",
                error="INVALID_TRANSITION",
            )

        campaign["status"] = target_status
        campaign["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # Apply timestamp fields
        now_str = datetime.now(timezone.utc).isoformat()
        if target_status == "running" and current_status != "paused":
            campaign["started_at"] = now_str
        elif target_status == "paused":
            campaign["paused_at"] = now_str
        elif target_status == "stopped":
            campaign["stopped_at"] = now_str

        if additional_updates:
            campaign.update(additional_updates)

        await self.repository.save_outbound_campaign(**campaign)

        # Log CampaignControlEvent
        await self.repository.save_campaign_control_event(
            campaign_id=campaign_id,
            event_type=action_name,
            operator=operator,
            reason=reason or f"Campaign status updated to {target_status}",
            previous_status=current_status,
            new_status=target_status,
        )

        return CampaignActionResult(
            action=action_name,
            success=True,
            campaign_id=campaign_id,
            previous_status=current_status,
            new_status=target_status,
            message=f"Campaign transitioned to {target_status} status.",
            data={"campaign": campaign},
        )

    async def mark_ready(self, campaign_id: str, operator: str, reason: str | None = None) -> CampaignActionResult:
        """Mark a draft campaign as ready to run."""
        return await self._transition_status(
            campaign_id=campaign_id,
            allowed_from=["draft"],
            target_status="ready",
            operator=operator,
            action_name="ready",
            reason=reason,
        )

    async def start_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> CampaignActionResult:
        """Start a ready, paused, or stopped campaign."""
        # Reset today's count if starting fresh, or keep it if resuming
        return await self._transition_status(
            campaign_id=campaign_id,
            allowed_from=["ready", "paused", "stopped"],
            target_status="running",
            operator=operator,
            action_name="started",
            reason=reason,
        )

    async def pause_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> CampaignActionResult:
        """Pause a running campaign."""
        return await self._transition_status(
            campaign_id=campaign_id,
            allowed_from=["running"],
            target_status="paused",
            operator=operator,
            action_name="paused",
            reason=reason,
        )

    async def resume_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> CampaignActionResult:
        """Resume a paused campaign."""
        return await self._transition_status(
            campaign_id=campaign_id,
            allowed_from=["paused"],
            target_status="running",
            operator=operator,
            action_name="resumed",
            reason=reason,
        )

    async def stop_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> CampaignActionResult:
        """Stop a running, paused, or ready campaign."""
        return await self._transition_status(
            campaign_id=campaign_id,
            allowed_from=["running", "paused", "ready"],
            target_status="stopped",
            operator=operator,
            action_name="stopped",
            reason=reason,
        )

    async def complete_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> CampaignActionResult:
        """Mark a campaign as completed."""
        return await self._transition_status(
            campaign_id=campaign_id,
            allowed_from=["running", "paused", "stopped", "ready"],
            target_status="completed",
            operator=operator,
            action_name="completed",
            reason=reason,
        )

    # -------------------------------------------------------------------------
    # Analytics & Summary
    # -------------------------------------------------------------------------

    async def get_campaign_summary(self, campaign_id: str) -> CampaignSummary:
        """Calculate and return a summary of campaign stats."""
        campaign = await self.repository.get_outbound_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Query all leads for the campaign
        leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})

        # Calculate counts
        total_leads = len(leads)
        queued = sum(1 for l in leads if l.get("status") in ("new", "queued"))
        active = sum(1 for l in leads if l.get("status") in ("dialing", "in_call"))
        completed = sum(1 for l in leads if l.get("status") == "completed")
        failed = sum(1 for l in leads if l.get("status") == "failed")
        dnc = sum(1 for l in leads if l.get("status") in ("dnc", "do_not_call"))
        wrong_number = sum(1 for l in leads if l.get("status") == "wrong_number")
        callback = sum(1 for l in leads if l.get("status") == "callback")
        transfer = sum(1 for l in leads if l.get("status") == "transferred")

        # Count attempts created today
        today = datetime.now(timezone.utc).date()
        attempts = await self.repository.query_call_attempts({"campaign_id": campaign_id})
        
        started_today = 0
        for att in attempts:
            created_at_raw = att.get("created_at") or att.get("started_at")
            if created_at_raw:
                try:
                    dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                    if dt.date() == today:
                        started_today += 1
                except Exception:
                    pass

        return CampaignSummary(
            campaign_id=campaign_id,
            name=campaign.get("name", ""),
            status=campaign.get("status", "draft"),
            total_leads=total_leads,
            queued_leads=queued,
            active_calls=active,
            completed_calls=completed,
            failed_calls=failed,
            dnc_count=dnc,
            wrong_number_count=wrong_number,
            callback_count=callback,
            transfer_count=transfer,
            calls_started_today=started_today,
            daily_call_cap=campaign.get("daily_call_cap", 100),
            max_concurrent_calls=campaign.get("max_concurrent_calls", 1),
        )

    async def list_live_calls(self, campaign_id: str | None = None, limit: int = 100) -> list[dict]:
        """List active live call sessions."""
        filters = {}
        if campaign_id:
            filters["campaign_id"] = campaign_id
        sessions = await self.repository.query_live_call_sessions(filters)
        # Filter for active statuses only
        active_sessions = [s for s in sessions if s.get("status") not in ("ended", "failed")]
        return active_sessions[:limit]

    async def list_call_attempts(
        self, campaign_id: str | None = None, lead_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        """List call attempts."""
        filters = {}
        if campaign_id:
            filters["campaign_id"] = campaign_id
        if lead_id:
            filters["lead_id"] = lead_id
        
        attempts = await self.repository.query_call_attempts(filters)
        # Redact phone numbers for compliance
        redacted = []
        for att in attempts:
            att_dict = dict(att)
            if att_dict.get("phone_number_redacted"):
                att_dict["phone_number"] = att_dict["phone_number_redacted"]
            elif "phone_number" in att_dict:
                # Mask phone number just in case
                phone = att_dict["phone_number"]
                if len(phone) > 4:
                    att_dict["phone_number"] = phone[:-4] + "****"
            redacted.append(att_dict)
        return redacted[:limit]
