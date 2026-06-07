import os
import sys

# Safety fallback loading
try:
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()
except ImportError:
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config.env_loader import load_environment
    from config.runtime_env import get_runtime_env
    load_environment()

import uuid
import hashlib
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, Tuple
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from storage.repository import Repository
from telephony.campaign_models import CampaignSummary
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig, LiveKitDialResult
from compliance.calling_window import resolve_lead_timezone


class DialerTickConfig(BaseModel):
    """Configuration for running a single dialer tick."""

    campaign_id: str
    live_mode: bool = False
    dry_run: bool = True
    max_calls: Optional[int] = None
    now: Optional[datetime] = None
    operator: Optional[str] = None
    force: bool = False


class DialerTickResult(BaseModel):
    """The result summary of a dialer queue tick."""

    campaign_id: str
    campaign_status: str
    eligible_leads: int = 0
    blocked_reason: Optional[str] = None
    calls_started: int = 0
    attempts_created: int = 0
    dry_run: bool = True
    live_mode: bool = False
    attempt_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class DialerQueue:
    """Evaluates campaign leads and schedules outbound dials."""

    def __init__(
        self, repository: Repository | None = None, adapter: LiveKitOutboundAdapter | None = None
    ) -> None:
        self.repository = repository or Repository()
        self.adapter = adapter or LiveKitOutboundAdapter()

    def is_within_calling_window(self, campaign: dict, now: datetime) -> tuple[bool, Optional[str]]:
        """Validate if the current time matches allowed campaign day and hours."""
        campaign_tz_str = campaign.get("timezone", "America/New_York")
        try:
            tz = ZoneInfo(campaign_tz_str)
            local_now = now.astimezone(tz)
        except Exception as e:
            return False, f"Invalid campaign timezone: {campaign_tz_str} ({e})"

        # Check allowed days
        allowed_days = campaign.get("allowed_days") or ["mon", "tue", "wed", "thu", "fri"]
        day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
        current_day = day_map[local_now.weekday()]
        if current_day not in allowed_days:
            return False, f"Day {current_day} is not allowed by campaign policy"

        # Check time window
        start_str = campaign.get("calling_window_start", "09:30")
        end_str = campaign.get("calling_window_end", "18:00")
        try:
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            
            # Build local datetime objects for start and end
            local_start = local_now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            local_end = local_now.replace(hour=eh, minute=em, second=0, microsecond=0)
            
            if not (local_start <= local_now <= local_end):
                return False, f"Local time {local_now.strftime('%H:%M')} is outside campaign window {start_str}-{end_str}"
        except Exception as e:
            return False, f"Error parsing window start/end: {e}"

        return True, None

    async def current_active_call_count(self, campaign_id: str) -> int:
        """Count active calls (dialing or in_call status) for this campaign."""
        # 1. Count by lead status
        leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
        leads_count = sum(1 for l in leads if l.get("status") in ("dialing", "in_call"))
        
        # 2. Count by active live call sessions
        sessions = await self.repository.query_live_call_sessions({"campaign_id": campaign_id})
        sessions_count = sum(1 for s in sessions if s.get("status") not in ("ended", "failed"))
        
        return max(leads_count, sessions_count)

    async def daily_cap_remaining(self, campaign: dict, now: datetime) -> int:
        """Calculate how many calls can still be placed today under the campaign cap."""
        campaign_id = campaign["id"].replace("campaign:", "") if "id" in campaign else campaign.get("campaign_id")
        daily_cap = campaign.get("daily_call_cap", 100)
        
        # Find attempts created today in the campaign timezone
        campaign_tz_str = campaign.get("timezone", "America/New_York")
        try:
            tz = ZoneInfo(campaign_tz_str)
            local_today = now.astimezone(tz).date()
        except Exception:
            local_today = now.date()

        attempts = await self.repository.query_call_attempts({"campaign_id": campaign_id})
        
        calls_placed_today = 0
        for att in attempts:
            created_at_raw = att.get("created_at") or att.get("started_at")
            if created_at_raw:
                try:
                    dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                    if dt.astimezone(tz).date() == local_today:
                        # Exclude blocked calls from cap if desired, but let's count all attempts
                        calls_placed_today += 1
                except Exception:
                    pass

        return max(0, daily_cap - calls_placed_today)

    def lead_is_callable(self, campaign: dict, lead: dict, now: datetime) -> tuple[bool, Optional[str]]:
        """Verify if a lead is eligible to be dialed."""
        status = lead.get("status", "new")
        if status not in ("new", "queued", "callback", "failed"):
            return False, f"Invalid lead status: {status}"

        # Check attempt count
        attempt_count = lead.get("attempt_count", 0)
        max_attempts = lead.get("max_attempts", 3)
        if attempt_count >= max_attempts:
            return False, f"Attempt limit reached ({attempt_count}/{max_attempts})"

        # Check next_attempt_at if scheduled
        next_attempt_str = lead.get("next_attempt_at")
        if next_attempt_str:
            try:
                next_attempt = datetime.fromisoformat(next_attempt_str.replace("Z", "+00:00"))
                if now < next_attempt:
                    return False, f"Next attempt scheduled at {next_attempt_str}"
            except Exception:
                pass

        # Check calling window in lead's resolved timezone
        tz_str, source, confidence = resolve_lead_timezone(lead)
        if tz_str:
            try:
                tz = ZoneInfo(tz_str)
                lead_local_now = now.astimezone(tz)
                
                # Check day
                allowed_days = campaign.get("allowed_days") or ["mon", "tue", "wed", "thu", "fri"]
                day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
                lead_day = day_map[lead_local_now.weekday()]
                if lead_day not in allowed_days:
                    return False, f"Day {lead_day} is not allowed in lead timezone: {tz_str}"

                # Check time
                start_str = campaign.get("calling_window_start", "09:30")
                end_str = campaign.get("calling_window_end", "18:00")
                sh, sm = map(int, start_str.split(":"))
                eh, em = map(int, end_str.split(":"))
                
                lead_start = lead_local_now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                lead_end = lead_local_now.replace(hour=eh, minute=em, second=0, microsecond=0)
                
                if not (lead_start <= lead_local_now <= lead_end):
                    return False, f"Outside calling window in lead timezone: {tz_str} ({lead_local_now.strftime('%H:%M')})"
            except Exception as e:
                # Fall back to true if parsing failed, but log warning
                pass

        return True, None

    async def get_next_eligible_leads(self, campaign: dict, capacity: int, now: datetime) -> list[dict]:
        """Fetch the next batch of leads that are eligible for dialing."""
        campaign_id = campaign["id"].replace("campaign:", "") if "id" in campaign else campaign.get("campaign_id")
        leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
        
        # Sort leads: higher priority first, then oldest created_at first
        leads.sort(key=lambda x: (-x.get("priority", 0), x.get("created_at") or ""))

        eligible = []
        for lead in leads:
            is_callable, _ = self.lead_is_callable(campaign, lead, now)
            if is_callable:
                eligible.append(lead)
                if len(eligible) >= capacity:
                    break
        return eligible

    async def run_tick(self, config: DialerTickConfig) -> DialerTickResult:
        """Run a single tick of the dialer queue."""
        campaign_id = config.campaign_id
        now = config.now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        result = DialerTickResult(
            campaign_id=campaign_id,
            campaign_status="draft",
            dry_run=config.dry_run,
            live_mode=config.live_mode,
        )

        controlled_live_test = os.getenv("DANA_CONTROLLED_LIVE_TEST", "false").lower() in ("true", "1", "yes")
        if controlled_live_test:
            result.blocked_reason = "Campaign dialing is disabled because DANA_CONTROLLED_LIVE_TEST is active."
            result.warnings.append("Controlled live test mode active - campaign dialing blocked.")
            return result

        # 1. Load Campaign
        campaign = await self.repository.get_outbound_campaign(campaign_id)
        if not campaign:
            result.blocked_reason = f"Campaign {campaign_id} not found."
            result.errors.append("CAMPAIGN_NOT_FOUND")
            return result

        status = campaign.get("status", "draft")
        result.campaign_status = status

        if status != "running" and not config.force:
            result.blocked_reason = f"Campaign is in {status} status (must be running)."
            result.warnings.append("Campaign is not running")
            return result

        # 2. Check calling window
        window_ok, window_err = self.is_within_calling_window(campaign, now)
        if not window_ok and not config.force:
            result.blocked_reason = f"Calling window check failed: {window_err}"
            result.warnings.append(result.blocked_reason)
            return result

        # 3. Check daily cap
        remaining_cap = await self.daily_cap_remaining(campaign, now)
        if remaining_cap <= 0:
            result.blocked_reason = "Daily call cap reached."
            result.warnings.append("daily call cap reached")
            return result

        # 4. Check concurrency limits
        max_concurrent = campaign.get("max_concurrent_calls", 1)
        active_calls = await self.current_active_call_count(campaign_id)
        capacity = max_concurrent - active_calls
        if capacity <= 0:
            result.blocked_reason = f"Max concurrency limit reached ({active_calls}/{max_concurrent} active)."
            result.warnings.append("concurrency limit reached")
            return result

        # Limit capacity by config max_calls or remaining cap
        if config.max_calls is not None:
            capacity = min(capacity, config.max_calls)
        capacity = min(capacity, remaining_cap)

        # 5. Fetch eligible leads
        eligible_leads = await self.get_next_eligible_leads(campaign, capacity, now)
        result.eligible_leads = len(eligible_leads)

        if not eligible_leads:
            result.blocked_reason = "No eligible leads remaining in queue."
            return result

        if config.dry_run:
            return result

        # 6. Dial Leads
        from telephony.did_pool import DIDPoolManager
        from storage.schemas import CallerIdSelectionConfig
        did_pool_manager = DIDPoolManager(self.repository)

        for lead in eligible_leads:
            lead_phone = lead.get("phone_number")
            lead_id = lead["id"]

            if config.dry_run:
                # Dry run mode: do not write to DB, just mock the count
                result.calls_started += 1
                result.attempts_created += 1
                result.attempt_ids.append(f"dry-run-attempt-{lead_id}")
                continue

            # Create attempt record
            attempt_id = str(uuid.uuid4())
            phone_redacted = lead_phone[:-4] + "****" if len(lead_phone) > 4 else lead_phone
            phone_hash = hashlib.sha256(lead_phone.encode("utf-8")).hexdigest()

            # Determine provider config ID
            provider_config_id = campaign.get("provider_config_id")

            # Update lead status
            lead["status"] = "dialing"
            lead["attempt_count"] = lead.get("attempt_count", 0) + 1
            lead["last_attempt_at"] = now.isoformat()
            lead["updated_at"] = now.isoformat()
            await self.repository.save_campaign_lead(**lead)

            # Define LiveKit dial config
            room_template = "dana-{campaign_id}-{lead_id}-{attempt_id}"
            provider_config = None
            if provider_config_id:
                provider_config = await self.repository.get_telephony_provider_config(provider_config_id)
            if provider_config:
                room_template = provider_config.get("room_name_template", room_template)

            room_name = self.adapter.build_room_name(campaign_id, lead_id, attempt_id, room_template)
            part_identity = self.adapter.build_participant_identity(lead_id, attempt_id)

            env = get_runtime_env()
            provider = env["active_provider"]
            strategy = campaign.get("caller_id_strategy") or "health_weighted"
            allow_cross = os.environ.get("DANA_ALLOW_CROSS_PROVIDER_CALLER_ID", "").strip().lower() == "true"
            require_verified = True

            caller_id = None
            caller_id_source = "none"

            if not config.live_mode:
                caller_id = campaign.get("caller_id") or env.get("outbound_caller_id") or "+15550000"
                caller_id_source = "mock_fallback"

            attempt = {
                "id": attempt_id,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "provider_config_id": provider_config_id,
                "status": "dialing",
                "phone_number_redacted": phone_redacted,
                "phone_number_hash": phone_hash,
                "livekit_room_name": room_name,
                "started_at": now.isoformat(),
                "created_at": now,
                "updated_at": now,
                "metadata": {
                    "selected_caller_id": caller_id,
                    "caller_id_source": caller_id_source
                }
            }

            # If Live Mode is requested
            if config.live_mode:
                # Check environment flags first
                if not self.adapter.live_mode_enabled():
                    result.errors.append(f"Live mode is not enabled in this environment (disabled for lead {lead_id}).")
                    # Mark attempt failed
                    attempt["status"] = "failed"
                    attempt["failure_reason"] = "Live mode dialing blocked (environment flags not active)."
                    attempt["ended_at"] = now.isoformat()
                    await self.repository.save_call_attempt(**attempt)

                    lead["status"] = "failed"
                    await self.repository.save_campaign_lead(**lead)
                    continue

                # Load trunk ID from provider_config or env
                outbound_trunk_id = None
                if provider_config:
                    outbound_trunk_id = provider_config.get("livekit_sip_outbound_trunk_id")
                if not outbound_trunk_id:
                    outbound_trunk_id = env["livekit_sip_outbound_trunk_id"]

                if not outbound_trunk_id:
                    result.errors.append(f"Live dial failed: missing outbound trunk ID for lead {lead_id}.")
                    attempt["status"] = "failed"
                    attempt["failure_reason"] = "Missing LiveKit SIP Outbound Trunk ID."
                    attempt["ended_at"] = now.isoformat()
                    await self.repository.save_call_attempt(**attempt)

                    lead["status"] = "failed"
                    await self.repository.save_campaign_lead(**lead)
                    continue

                selection_config = CallerIdSelectionConfig(
                    provider=provider,
                    strategy=strategy,
                    allow_cross_provider=allow_cross,
                    require_verified=require_verified
                )
                
                selection_res = await did_pool_manager.select_caller_id(selection_config)
                if not selection_res.success:
                    result.blocked_reason = f"No eligible caller ID: {selection_res.reason}"
                    result.errors.append("NO_ELIGIBLE_CALLER_ID")
                    
                    attempt["status"] = "failed"
                    attempt["failure_reason"] = f"No eligible caller ID: {selection_res.reason}"
                    attempt["ended_at"] = now.isoformat()
                    await self.repository.save_call_attempt(**attempt)

                    lead["status"] = "failed"
                    await self.repository.save_campaign_lead(**lead)
                    return result

                caller_id = selection_res.phone_number
                caller_id_source = selection_res.source
                
                # Update attempt metadata with actual selection details
                attempt["metadata"]["selected_caller_id"] = caller_id
                attempt["metadata"]["caller_id_source"] = caller_id_source

                # Determine wait_until_answered and krisp_enabled
                wait_until_answered_env = os.environ.get("DANA_WAIT_UNTIL_ANSWERED", "true").lower() == "true"
                wait_until_answered = True
                if provider_config and "wait_until_answered" in provider_config:
                    wait_until_answered = bool(provider_config.get("wait_until_answered"))
                else:
                    wait_until_answered = wait_until_answered_env

                krisp_enabled_env = os.environ.get("DANA_KRISP_ENABLED", "true").lower() == "true"
                krisp_enabled = True
                if provider_config and "krisp_enabled" in provider_config:
                    krisp_enabled = bool(provider_config.get("krisp_enabled"))
                else:
                    krisp_enabled = krisp_enabled_env

                dial_conf = LiveKitDialConfig(
                    live_mode=True,
                    livekit_url=provider_config.get("livekit_url") if provider_config else env["livekit_url"],
                    api_key=(provider_config.get("livekit_api_key") if provider_config else None) or env["livekit_api_key"],
                    api_secret=(provider_config.get("livekit_api_secret") if provider_config else None) or env["livekit_api_secret"],
                    outbound_trunk_id=outbound_trunk_id,
                    room_name=room_name,
                    phone_number=lead_phone,
                    caller_id=caller_id,
                    participant_identity=part_identity,
                    wait_until_answered=wait_until_answered,
                    krisp_enabled=krisp_enabled,
                    metadata={"campaign_id": campaign_id, "lead_id": lead_id, "attempt_id": attempt_id},
                )

                # Save CallAttempt *before* dialing with status "dialing"
                await self.repository.save_call_attempt(**attempt)

                dial_res = await self.adapter.dial(dial_conf)

                if dial_res.success:
                    status_val = "in_progress"
                    if wait_until_answered and dial_res.answered:
                        status_val = "answered"
                    
                    attempt["status"] = status_val
                    attempt["livekit_participant_id"] = dial_res.livekit_participant_id
                    attempt["livekit_sip_call_id"] = dial_res.livekit_sip_call_id
                    attempt["provider_call_id"] = dial_res.provider_call_id
                    await self.repository.save_call_attempt(**attempt)

                    # Update usage count after attempt starts
                    await did_pool_manager.record_call_use(selection_res.phone_number)

                    # Create LiveCallSession
                    session_id = str(uuid.uuid4())
                    await self.repository.save_live_call_session(
                        id=session_id,
                        campaign_id=campaign_id,
                        lead_id=lead_id,
                        attempt_id=attempt_id,
                        call_id=attempt_id,
                        status="active",
                        current_stage="OPENING",
                        livekit_room_name=room_name,
                        participant_identity=part_identity,
                        started_at=now,
                        updated_at=now,
                    )
                    
                    lead["status"] = "in_call"
                    await self.repository.save_campaign_lead(**lead)

                    result.calls_started += 1
                    result.attempts_created += 1
                    result.attempt_ids.append(attempt_id)
                else:
                    result.errors.append(f"Live dial failed for lead {lead_id}: {dial_res.message}")
                    attempt["status"] = "failed"
                    attempt["failure_reason"] = dial_res.message
                    attempt["sip_call_status"] = dial_res.sip_call_status
                    attempt["sip_status_code"] = dial_res.sip_status_code
                    attempt["sip_status"] = dial_res.sip_status
                    attempt["ended_at"] = now.isoformat()
                    await self.repository.save_call_attempt(**attempt)

                    # Lead status "failed" unless retryable
                    max_attempts = lead.get("max_attempts", 3)
                    if lead.get("attempt_count", 0) < max_attempts:
                        lead["status"] = "queued"
                    else:
                        lead["status"] = "failed"
                    await self.repository.save_campaign_lead(**lead)

            else:
                # Local Mock Mode (live_mode = False, dry_run = False)
                # Create a mock attempt with status completed and outcome answered
                attempt["status"] = "completed"
                attempt["outcome"] = "answered"
                attempt["answered_at"] = now.isoformat()
                attempt["ended_at"] = now.isoformat()
                await self.repository.save_call_attempt(**attempt)

                # Update lead status to completed
                lead["status"] = "completed"
                lead["outcome"] = "answered"
                await self.repository.save_campaign_lead(**lead)

                result.calls_started += 1
                result.attempts_created += 1
                result.attempt_ids.append(attempt_id)

        # Log CampaignControlEvent for dialing tick
        if result.attempts_created > 0:
            await self.repository.save_campaign_control_event(
                campaign_id=campaign_id,
                event_type="dialer_tick",
                operator=config.operator or "system",
                reason=f"Dialer tick started {result.attempts_created} call attempts",
                metadata={"attempts": result.attempts_created, "dry_run": config.dry_run, "live_mode": config.live_mode},
            )

        return result
