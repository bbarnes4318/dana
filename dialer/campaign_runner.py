"""Async campaign runner orchestrator for outbound calls."""

from __future__ import annotations

import asyncio
import logging
import uuid
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from storage.repository import Repository
from dialer.lead_queue import LeadQueue
from dialer.caller_id_pool import CallerIdPool
from dialer.retry_policy import RetryPolicy
from compliance.pre_dial_gate import PreDialGate, PreDialDecision
from compliance.consent_record import ConsentRecord
from compliance.dnc_registry import DatabaseDNCRegistry
from telephony.agent_availability import AgentAvailabilityStore
from dialer.call_service import CallService
from dialer.answering_machine_detection import AnsweringMachineDetector

logger = logging.getLogger(__name__)


class CampaignRunner:
    """Orchestrates outbound call dialing for a campaign."""

    status_label = "dry-run and orchestration-ready, live AgentSession handoff pending"

    def __init__(
        self,
        repository: Repository,
        lead_queue: Optional[LeadQueue] = None,
        caller_id_pool: Optional[CallerIdPool] = None,
        pre_dial_gate: Optional[PreDialGate] = None,
        agent_availability_store: Optional[AgentAvailabilityStore] = None,
        call_service: Optional[CallService] = None,
    ) -> None:
        self.repository = repository
        self.lead_queue = lead_queue or LeadQueue(repository)
        self.caller_id_pool = caller_id_pool or CallerIdPool(repository)
        self.pre_dial_gate = pre_dial_gate or PreDialGate()
        self.agent_availability_store = agent_availability_store
        self.call_service = call_service or CallService()

        self._running = False
        self._paused = False
        self._loop_task: Optional[asyncio.Task] = None
        self._active_calls: Dict[str, dict] = {}  # call_id -> call details
        self._call_timestamps: List[datetime] = []

        from dialer.pacing import CampaignPacer
        self.campaign_pacer = CampaignPacer(repository)

    def _check_pacing(self, max_concurrent: int, cpm: int, now: datetime) -> bool:
        # Clean up old timestamps (older than 60 seconds)
        cutoff = now - timedelta(seconds=60)
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]

        if len(self._active_calls) >= max_concurrent:
            logger.debug(
                "Pacing block: Active calls (%d) >= max_concurrent (%d)",
                len(self._active_calls),
                max_concurrent,
            )
            return False
        if len(self._call_timestamps) >= cpm:
            logger.debug(
                "Pacing block: Calls in last 60s (%d) >= cpm (%d)",
                len(self._call_timestamps),
                cpm,
            )
            return False
        return True

    async def start(self, campaign_id: str) -> None:
        if self._running:
            return
        self._running = True
        self._paused = False
        self._loop_task = asyncio.create_task(self._run_loop(campaign_id))
        logger.info("Campaign runner started for campaign %s", campaign_id)

    async def pause(self) -> None:
        self._paused = True
        logger.info("Campaign runner paused")

    async def resume(self) -> None:
        self._paused = False
        logger.info("Campaign runner resumed")

    async def stop(self) -> None:
        self._running = False
        self._paused = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("Campaign runner stopped")

    async def _run_loop(self, campaign_id: str) -> None:
        while self._running:
            if self._paused:
                await asyncio.sleep(1.0)
                continue

            now = datetime.now(timezone.utc)
            try:
                # Enforce pacing limits before pulling a lead
                campaign = await self.repository.get_campaign(campaign_id)
                if not campaign:
                    logger.warning("Campaign %s not found. Runner stopping.", campaign_id)
                    self._running = False
                    break

                if campaign.get("is_paused", False):
                    logger.info("Campaign %s is paused. Sleeping.", campaign_id)
                    await asyncio.sleep(5.0)
                    continue

                max_concurrent = campaign.get("max_concurrent_calls", 5)
                cpm = campaign.get("calls_per_minute", 20)

                # Check CampaignPacer pacing BEFORE dialing step
                if not await self.campaign_pacer.can_start_call(campaign_id):
                    await asyncio.sleep(1.0)
                    continue

                # Run a single dialing step
                await self.run_once(campaign_id, now)
            except Exception as e:
                logger.error("Error in campaign runner loop iteration: %s", e, exc_info=True)

            await asyncio.sleep(0.5)

    async def run_once(
        self,
        campaign_id: str,
        now: Optional[datetime] = None,
        simulated_outcome: Optional[str] = None
    ) -> Optional[str]:
        """Runs a single dialing iteration.
        
        Pulls a lead, runs compliance, starts the call, handles the outcome, and updates tables.
        Returns a string status for testing, or None.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 1. Fetch campaign details
        campaign = await self.repository.get_campaign(campaign_id)
        if not campaign:
            return "campaign_not_found"

        if campaign.get("is_paused", False):
            return "campaign_paused"

        # 2. Precheck Agent Availability for campaign target states
        target_states = campaign.get("target_states")  # e.g., ["FL", "TX"]
        require_live_transfer = campaign.get("require_live_transfer", False)
        if target_states and require_live_transfer and self.agent_availability_store:
            agent_available = False
            for state in target_states:
                avail = await self.agent_availability_store.get_available_agent(state)
                if avail:
                    agent_available = True
                    break
            if not agent_available:
                logger.info("No agents available for target states %s. Skipping dialing.", target_states)
                return "no_agents_available_precheck"

        # 2.5 CampaignPacer Pacing Check (BEFORE locking the lead)
        if not await self.campaign_pacer.can_start_call(campaign_id):
            logger.info("Pacing check blocked call for campaign %s", campaign_id)
            return "pacing_blocked"

        # 3. Pull and lock the next eligible lead
        lock_holder_id = f"runner-{uuid.uuid4().hex[:8]}"
        lead = await self.lead_queue.get_next_eligible_lead(campaign_id, lock_holder_id, now)
        if not lead:
            return "no_eligible_leads"

        lead_id = lead.get("id") or lead.get("lead_id")
        lead_state = lead.get("lead_state") or lead.get("state")

        # 4. Agent Availability checks for the selected lead's state
        if require_live_transfer and self.agent_availability_store:
            if not lead_state:
                # Wildcard agents only
                wildcard_agent = await self.agent_availability_store.get_available_agent(None)
                if not wildcard_agent:
                    logger.info("Lead %s state is missing and no wildcard agents are available. Releasing lead.", lead_id)
                    retry_after = now + timedelta(seconds=campaign.get("cooldown_no_agent", 1800))
                    await self.lead_queue.release_lead_on_failure(
                        lead_id, "missing_state_for_transfer_routing", retry_after
                    )
                    return "missing_state_no_wildcard"
            else:
                specific_agent = await self.agent_availability_store.get_available_agent(lead_state)
                if not specific_agent:
                    logger.info("No agents available for lead %s state %s. Releasing lead.", lead_id, lead_state)
                    retry_after = now + timedelta(seconds=campaign.get("cooldown_no_agent", 1800))
                    await self.lead_queue.release_lead_on_failure(
                        lead_id, "no_agent_available_for_live_transfer", retry_after
                    )
                    return "no_agent_available_state"

        # 5. Select caller ID from pool
        caller_id = await self.caller_id_pool.get_next_caller_id(campaign_id, campaign, now)
        if not caller_id:
            logger.warning("No active caller ID available for campaign %s. Releasing lead %s.", campaign_id, lead_id)
            retry_after = now + timedelta(seconds=campaign.get("cooldown_caller_id", 300))
            await self.lead_queue.release_lead_on_failure(
                lead_id, "caller_id_inactive", retry_after
            )
            return "compliance_blocked_caller_id_inactive"

        # 6. Fetch consent record
        phone_e164 = lead.get("lead_phone_e164") or lead.get("phone_e164") or lead.get("phone_number")
        consent_record = None
        if phone_e164:
            consent_dict = await self.repository.get_consent_record_for_lead(lead_id, phone_e164, campaign_id)
            if consent_dict:
                consent_record = ConsentRecord(
                    consent_artifact_id=consent_dict.get("consent_artifact_id"),
                    lead_id=consent_dict.get("lead_id"),
                    phone_e164=consent_dict.get("phone_e164"),
                    source_vendor=consent_dict.get("source_vendor"),
                    consent_text=consent_dict.get("consent_text"),
                    consent_timestamp=consent_dict.get("consent_timestamp")
                )

        # 7. Run compliance pre-dial gate
        dnc_registry = DatabaseDNCRegistry(self.repository)
        
        # Build campaign context for PreDialGate validation
        campaign_context = dict(campaign)
        campaign_context["caller_id"] = caller_id
        campaign_context["active_caller_ids"] = [caller_id]

        decision = await self.pre_dial_gate.check(
            lead=lead,
            campaign=campaign_context,
            consent_record=consent_record,
            dnc_registry=dnc_registry,
            now=now
        )

        if not decision.allowed:
            # Blocked by compliance rules. Release lead lock.
            reason = "other"
            if "outside_calling_window" in decision.blocked_by:
                reason = "outside_calling_window"
            elif "missing_consent_record" in decision.blocked_by or "consent_source_not_approved" in decision.blocked_by:
                reason = "missing_consent_record"
            elif "caller_id_inactive" in decision.blocked_by or "caller_id_missing" in decision.blocked_by:
                reason = "caller_id_inactive"
            elif "phone_on_dnc" in decision.blocked_by:
                reason = "phone_on_dnc"
            
            logger.info("Lead %s compliance check failed: %s. Releasing lock.", lead_id, decision.reason)
            
            # Map release reasons to cooldowns
            retry_after = None
            if reason == "outside_calling_window":
                # Wait until next day/window. Default to 12 hours
                retry_after = now + timedelta(hours=12)
            elif reason == "caller_id_inactive":
                retry_after = now + timedelta(minutes=10)

            # If it is DNC, update status to DNC directly
            if reason == "phone_on_dnc":
                await self.lead_queue.mark_dnc(lead_id, phone_e164, campaign_id, "compliance_gate_dnc")
                return "compliance_blocked_dnc"

            await self.lead_queue.release_lead_on_failure(lead_id, reason, retry_after)
            return f"compliance_blocked_{reason}"

        # 8. Start call attempt
        # Increment attempt counter only now when real/dry call attempt starts
        call_id = f"call-{uuid.uuid4().hex[:12]}"
        
        # Record call started in CampaignPacer
        await self.campaign_pacer.mark_call_started(campaign_id, call_id)
        
        # Atomically mark lead as dialing (increments attempts, updates last_attempt_at)
        await self.repository.mark_lead_attempted(lead_id, call_id, caller_id, now)
        await self.caller_id_pool.mark_used(caller_id, campaign_id, now)
        self._call_timestamps.append(now)

        # Emit call.attempt_started event
        from integrations.crm_webhooks import emit_crm_event_async
        await emit_crm_event_async(
            "call.attempt_started",
            repository=self.repository,
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            phone_e164=phone_e164,
            lead_profile=lead
        )

        # Place the call
        call_details = None
        try:
            call_details = await self.call_service.place_call(lead, call_id, caller_id)
        except Exception as e:
            logger.error("Failed to place call via call service: %s", e)
            # Record call finished in CampaignPacer
            await self.campaign_pacer.mark_call_finished(campaign_id, call_id)
            # Revert/release lead as carrier failure
            retry_after = now + timedelta(seconds=campaign.get("cooldown_carrier_failure", 3600))
            await self.lead_queue.release_lead_on_failure(lead_id, "carrier_failure", retry_after)
            await self.repository.save_call_disposition(
                call_id=call_id,
                lead_id=lead_id,
                campaign_id=campaign_id,
                outcome="failed_to_place_call",
                amd_result="failed_to_place_call",
                retry_after=retry_after,
                caller_id=caller_id
            )
            return "failed_to_place_call"

        # Create initial call record in database for webhook/AMD outcome tracking
        is_dry_run = call_details.get("status") == "dry_run"
        await self.repository.save_call(
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            phone_e164=phone_e164,
            caller_id=caller_id,
            outcome="placed",
            amd_result="initiated",
            dry_run=is_dry_run,
            started_at=now
        )

        # 9. Evaluate Outcome and Answering Machine Detection (AMD)
        # Use simulated outcome if provided (mainly for testing)
        outcome = simulated_outcome
        if not outcome:
            # Map CallService dry-run status to default simulated outcome
            if is_dry_run:
                outcome = "human_answered"  # default dry run connects
            else:
                # In real scenario, we no longer block/wait for AMD to eliminate post-dial delay.
                # We instantly bridge the media stream and assume human_answered.
                outcome = "human_answered"


        # Update caller ID metrics
        await self.caller_id_pool.update_metrics_and_cooldown(caller_id, campaign_id, campaign, outcome, now)

        # Determine retry policy
        lead_callback_time = None
        c_time = lead.get("callback_time")
        if c_time:
            if isinstance(c_time, str):
                try:
                    if c_time.endswith("Z"):
                        c_time = c_time.replace("Z", "+00:00")
                    lead_callback_time = datetime.fromisoformat(c_time)
                except ValueError:
                    pass
            elif isinstance(c_time, datetime):
                lead_callback_time = c_time

        attempts = (lead.get("attempts", 0)) + 1
        retry_after = RetryPolicy.get_retry_after(outcome, campaign, attempts, now, callback_time=lead_callback_time)

        # Save call disposition (merges with the initial record)
        await self.repository.save_call_disposition(
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            outcome=outcome,
            amd_result=outcome if outcome != "human_answered" else None,
            retry_after=retry_after,
            caller_id=caller_id,
            dry_run=is_dry_run
        )

        # Emit call.connection_dispositioned event
        from integrations.crm_webhooks import emit_crm_event_async
        await emit_crm_event_async(
            "call.connection_dispositioned",
            repository=self.repository,
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            phone_e164=phone_e164,
            outcome=outcome,
            lead_profile=lead
        )

        # 10. Process Outcome Statuses
        if outcome == "human_answered":
            # For human answer: Bridge call and start AgentSession voice flow.
            logger.info("Call %s answered by human. Starting conversational session.", call_id)
            if not is_dry_run:
                await self._handoff_to_live_agent_session(call_id, lead, campaign)
            await self.lead_queue.mark_completed(lead_id, outcome="completed")
            return "success_human_answered"

        # Save outcome metrics and costs for non-human-answered call
        # Record call finished in CampaignPacer since it's not human answered and doesn't hand off
        await self.campaign_pacer.mark_call_finished(campaign_id, call_id)

        tele_provider = "telnyx" if not is_dry_run else "none"
        duration_val = 0.0
        if 'call_details' in locals() and isinstance(call_details, dict):
            duration_val = call_details.get("duration") or call_details.get("duration_seconds") or 0.0
        
        try:
            from metrics.model_cost_metrics import calculate_and_save_costs
            from metrics.outcome_metrics import save_outcome_for_call
            await calculate_and_save_costs(
                repository=self.repository,
                call_id=call_id,
                campaign_id=campaign_id,
                stt_provider="none",
                stt_seconds=0.0,
                llm_model="none",
                prompt_tokens=0,
                completion_tokens=0,
                tts_provider="none",
                tts_characters=0,
                telephony_provider=tele_provider,
                telephony_seconds=float(duration_val),
                dry_run=is_dry_run,
                llm_tokens_estimated=False
            )
            await save_outcome_for_call(self.repository, call_id, campaign_id, outcome, cost=0.0)
            try:
                from routing.model_router import ModelRouter
                ModelRouter.cleanup_call_routing(call_id)
            except Exception as re:
                logger.error("Failed to cleanup routing state in dialer: %s", re)
        except Exception as me:
            logger.error("Failed to save non-human answered metrics: %s", me)

        # Emit call.completed event for all other outcomes
        from integrations.crm_webhooks import emit_crm_event_async
        await emit_crm_event_async(
            "call.completed",
            repository=self.repository,
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            phone_e164=phone_e164,
            outcome=outcome,
            lead_profile=lead
        )
            
        if outcome == "dnc":
            # Lead requested DNC
            logger.info("Call %s returned DNC request. Registering DNC.", call_id)
            await self.lead_queue.mark_dnc(lead_id, phone_e164, campaign_id, "prospect_dnc_request")
            return "completed_dnc"
            
        elif outcome == "wrong_number":
            # Wrong number
            logger.info("Call %s returned wrong number. Registering wrong number.", call_id)
            await self.lead_queue.mark_wrong_number(lead_id)
            return "completed_wrong_number"

        elif outcome in ("hostile_refusal", "disconnected", "disconnected_bad_number", "consent_invalid"):
            logger.info("Call %s finished with final outcome %s. No retries.", call_id, outcome)
            mapped_reason = "disconnected_bad_number" if outcome == "disconnected" else outcome
            await self.lead_queue.release_lead_on_failure(lead_id, mapped_reason, retry_after=None)
            return f"completed_{outcome}"
            
        else:
            # Soft failure (no answer, busy, voicemail, carrier failure)
            logger.info("Call %s finished with outcome %s. Retry scheduled at %s", call_id, outcome, retry_after)
            
            release_reason = "transient_call_failure"
            if outcome == "carrier_failure":
                release_reason = "carrier_failure"
            elif outcome == "no_answer":
                release_reason = "transient_call_failure"
            elif outcome == "busy":
                release_reason = "transient_call_failure"
            elif outcome == "voicemail":
                release_reason = "transient_call_failure"
                
            await self.lead_queue.release_lead_on_failure(lead_id, release_reason, retry_after)
            return f"retryable_failure_{outcome}"

    async def _wait_for_amd_outcome(self, call_id: str, timeout: float = 15.0) -> str:
        """Polls the database/repository for updates to the call's AMD result or outcome.
        
        Checks for:
        1. Telnyx AMD webhook result (stored in call's amd_result or outcome field)
        2. LiveKit VAD classification (stored in call's amd_result or outcome field)
        
        Falls back to 'no_answer' if no outcome is determined within the timeout.
        """
        start_time = datetime.now(timezone.utc)
        elapsed = 0.0
        env_timeout = os.environ.get("DANA_AMD_TIMEOUT")
        if env_timeout:
            try:
                timeout = float(env_timeout)
            except ValueError:
                pass
                
        while elapsed < timeout:
            call_rec = await self.repository.get_call_record(call_id)
            if call_rec:
                amd_res = call_rec.get("amd_result")
                outcome = call_rec.get("outcome")
                
                for val in (outcome, amd_res):
                    if val and val not in ("placed", "initiated", "ringing"):
                        if val in ("human", "human_answered"):
                            return "human_answered"
                        if val in ("voicemail", "machine_greeting", "silence", "busy", "no_answer", "wrong_number", "dnc", "hostile_refusal", "disconnected", "disconnected_bad_number", "consent_invalid", "carrier_failure"):
                            return val
                            
            await asyncio.sleep(0.05)
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            
        logger.info("AMD outcome wait timed out for call %s. Falling back to no_answer.", call_id)
        return "no_answer"

    async def _handoff_to_live_agent_session(
        self,
        call_id: str,
        lead: dict[str, Any],
        campaign: dict[str, Any]
    ) -> None:
        """Handoff the connected call to Dana's LiveKit AgentSession.
        
        This method:
        - Creates/connects LiveKit room / SIP participant (if not already done)
        - Attaches campaign/lead metadata
        - Starts the AgentSession by allowing the LiveKit worker/agent to join
        - Let Dana runtime handle the conversation
        """
        logger.info("Initiating handoff for call %s to Dana live AgentSession", call_id)
        
        confirm_place_call = os.environ.get("DANA_CONFIRM_PLACE_CALL", "").lower() == "yes"
        if confirm_place_call:
            try:
                from livekit import api
                import json
                
                config = self.call_service.config
                lkapi = api.LiveKitAPI(
                    url=config.livekit_url,
                    api_key=config.livekit_api_key,
                    api_secret=config.livekit_api_secret
                )
                try:
                    # Resolve room name
                    call_rec = await self.repository.get_call_record(call_id)
                    actual_room = call_rec.get("room_name") if call_rec else f"{config.dana_room_prefix or 'dana-call'}-{call_id[-8:]}"
                    
                    metadata_payload = json.dumps({
                        "campaign_id": campaign.get("campaign_id") or campaign.get("id"),
                        "lead_id": lead.get("id") or lead.get("lead_id"),
                        "call_id": call_id
                    })
                    # Update room metadata
                    await lkapi.room.update_room_metadata(
                        room=actual_room,
                        metadata=metadata_payload
                    )
                    logger.info("Successfully updated room %s metadata for handoff", actual_room)
                finally:
                    await lkapi.aclose()
            except Exception as e:
                logger.error("Failed to update LiveKit room metadata during handoff: %s", e)
        else:
            logger.info("[Dry Run] Skipped LiveKit room metadata update for handoff")
