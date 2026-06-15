"""
Final Expense Licensed Agent Call Transfer Orchestrator
Uses TransferRouter to coordinate warm bridging, cold transferring, and callback fallbacks.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional, Any

from telephony.agent_availability import LicensedAgent, InMemoryAgentAvailabilityStore
from telephony.handoff_summary import build_handoff_summary
from telephony.transfer_router import TransferRouter
from telephony.warm_bridge import LiveKitWarmBridgeProvider
from telephony.cold_transfer import TelnyxColdTransferProvider

# Setup logger
logger = logging.getLogger(__name__)


@dataclass
class FeTransferResult:
    """Outcome status returned by the fe_transfer workflow."""
    success: bool
    reason: str
    transfer_mode: str  # "warm_bridge" | "cold_transfer" | "failed" | "callback_required" | "dry_run"
    agent_id: Optional[str] = None
    call_summary: Optional[str] = None
    provider_call_id: Optional[str] = None


# Package-level defaults for routing
_agent_store = InMemoryAgentAvailabilityStore()

# Populate default agent from environment if set
_default_agent_num = os.getenv("LICENSED_AGENT_PHONE_NUMBER")
if _default_agent_num and _default_agent_num != "replace_me":
    _agent_store.add_agent(
        LicensedAgent(
            agent_id="default-agent",
            name="Default Licensed Agent",
            phone_number=_default_agent_num,
            licensed_states=["*"],
            status="available"
        )
    )

_warm_bridge_provider = LiveKitWarmBridgeProvider()
_cold_transfer_provider = TelnyxColdTransferProvider()
_transfer_router = TransferRouter(_agent_store)


_reserved_agents_by_call: dict[str, str] = {}


async def release_call_agent(call_id: str) -> None:
    """Release any agent reserved for the given call ID."""
    agent_id = _reserved_agents_by_call.pop(call_id, None)
    if agent_id:
        logger.info("Releasing reserved agent '%s' for call '%s'", agent_id, call_id)
        await _agent_store.release_agent(agent_id, call_id)


async def fe_transfer(
    room_name: str,
    prospect_identity: Optional[str],
    licensed_agent_phone_number: Optional[str],
    call_summary: str,
    transfer_reason: str,
    lead_profile: dict[str, Any],
    lead_state: Optional[str],
    call_id: str,
    call_control_id: Optional[str] = None,
) -> FeTransferResult:
    """Bridge qualified prospect to a licensed agent using TransferRouter routing logic."""
    logger.info("Initializing fe_transfer routing for room: %s", room_name)

    # 1. Update/Add agent dynamically if a phone number was passed explicitly
    if licensed_agent_phone_number and licensed_agent_phone_number != "replace_me":
        _agent_store.add_agent(
            LicensedAgent(
                agent_id="explicit-agent",
                name="Explicit Licensed Agent",
                phone_number=licensed_agent_phone_number,
                licensed_states=["*"],
                status="available"
            )
        )

    # 2. Execute routing decision
    decision = await _transfer_router.route_transfer(
        lead_state=lead_state,
        call_id=call_id,
        lead_profile=lead_profile
    )
    logger.info("Transfer routing decision: mode=%s, success=%s", decision.transfer_mode, decision.success)

    from integrations.crm_webhooks import emit_crm_event_async
    from storage.repository import Repository
    repo = Repository()

    lead_id = lead_profile.get("lead_id") or lead_profile.get("id")
    campaign_id = lead_profile.get("campaign_id")
    phone_e164 = lead_profile.get("lead_phone_e164")

    # 1. Emit transfer.started event
    await emit_crm_event_async(
        "transfer.started",
        repository=repo,
        call_id=call_id,
        lead_id=lead_id,
        campaign_id=campaign_id,
        phone_e164=phone_e164,
        transfer={
            "call_id": call_id,
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "transfer_mode": decision.transfer_mode,
            "agent_id": decision.agent.agent_id if decision.agent else None,
            "success": False,
            "failure_reason": None,
            "provider_call_id": None
        }
    )

    if decision.transfer_mode == "warm_bridge" and decision.agent:
        # Build internal agent summary using the structured lead profile
        summary_text = build_handoff_summary(lead_profile)
        
        # Execute warm bridge
        try:
            res = await _warm_bridge_provider.initiate_warm_bridge(
                room_name=room_name,
                agent=decision.agent,
                summary=summary_text,
                call_id=call_id,
                prospect_identity=prospect_identity
            )
        except Exception as e:
            logger.exception("Failed to execute warm bridge: %s", e)
            await _agent_store.release_agent(decision.agent.agent_id, call_id)
            raise
        
        # If warm bridge execution failed, release agent
        if not res.success:
            await _agent_store.release_agent(decision.agent.agent_id, call_id)
        else:
            # Register the reserved agent ID for the call session
            _reserved_agents_by_call[call_id] = decision.agent.agent_id

        transfer_meta = {
            "call_id": call_id,
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "transfer_mode": "warm_bridge",
            "agent_id": decision.agent.agent_id,
            "success": res.success,
            "failure_reason": None if res.success else res.reason,
            "provider_call_id": res.provider_call_id
        }

        # Emit transfer outcomes
        if res.success:
            await emit_crm_event_async("transfer.succeeded", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)
            await emit_crm_event_async("lead.transferred", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)
        else:
            await emit_crm_event_async("transfer.failed", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)

        try:
            await repo.save_transfer(
                call_id=call_id,
                lead_id=lead_id,
                transfer_mode="warm",
                agent_id=decision.agent.agent_id,
                target_phone=decision.phone_number,
                success=res.success,
                failure_reason=None if res.success else res.reason,
                provider_call_id=res.provider_call_id
            )
        except Exception as db_err:
            logger.error("Failed to record transfer in repository: %s", db_err)

        return FeTransferResult(
            success=res.success,
            reason=res.reason,
            transfer_mode=res.transfer_mode or "warm_bridge",
            agent_id=decision.agent.agent_id,
            call_summary=summary_text,
            provider_call_id=res.provider_call_id
        )

    elif decision.transfer_mode == "cold_transfer" and decision.phone_number:
        # Execute cold transfer
        res = await _cold_transfer_provider.initiate_cold_transfer(
            room_name=room_name,
            phone_number=decision.phone_number,
            call_control_id=call_control_id
        )

        transfer_meta = {
            "call_id": call_id,
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "transfer_mode": "cold_transfer",
            "agent_id": None,
            "success": res.success,
            "failure_reason": None if res.success else res.reason,
            "provider_call_id": res.provider_call_id
        }

        # Emit transfer outcomes
        if res.success:
            await emit_crm_event_async("transfer.succeeded", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)
            await emit_crm_event_async("lead.transferred", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)
        else:
            await emit_crm_event_async("transfer.failed", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)

        try:
            await repo.save_transfer(
                call_id=call_id,
                lead_id=lead_id,
                transfer_mode="cold",
                agent_id=None,
                target_phone=decision.phone_number,
                success=res.success,
                failure_reason=None if res.success else res.reason,
                provider_call_id=res.provider_call_id
            )
        except Exception as db_err:
            logger.error("Failed to record transfer in repository: %s", db_err)

        return FeTransferResult(
            success=res.success,
            reason=res.reason,
            transfer_mode=res.transfer_mode or "cold_transfer",
            agent_id=None,
            call_summary=None,
            provider_call_id=res.provider_call_id
        )

    # Fallback callback required
    fail_reason = decision.reason or "no_agent_available"
    transfer_meta = {
        "call_id": call_id,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "transfer_mode": "callback_required",
        "agent_id": None,
        "success": False,
        "failure_reason": fail_reason,
        "provider_call_id": None
    }
    await emit_crm_event_async("transfer.failed", repository=repo, call_id=call_id, lead_id=lead_id, campaign_id=campaign_id, phone_e164=phone_e164, transfer=transfer_meta)

    try:
        await repo.save_transfer(
            call_id=call_id,
            lead_id=lead_id,
            transfer_mode="callback_required",
            agent_id=None,
            target_phone=None,
            success=False,
            failure_reason=fail_reason,
            provider_call_id=None
        )
    except Exception as db_err:
        logger.error("Failed to record transfer fallback in repository: %s", db_err)

    return FeTransferResult(
        success=False,
        reason=fail_reason,
        transfer_mode="callback_required",
        agent_id=None,
        call_summary=None,
        provider_call_id=None
    )
