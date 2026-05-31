from datetime import datetime, timezone
import uuid
from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field

from storage.repository import Repository
from training.post_call_exporter import PostCallExporter, PostCallExportConfig


class CallControlResult(BaseModel):
    """Result of a call control action."""

    action: str
    success: bool
    call_session_id: Optional[str] = None
    attempt_id: Optional[str] = None
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    message: str
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class TelephonyCallControl:
    """Manages active live calls, outcome updates, DNC scrub enforcement, and exports."""

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    async def list_live_calls(self, campaign_id: str | None = None, limit: int = 100) -> list[dict]:
        """List active live call sessions."""
        filters = {}
        if campaign_id:
            filters["campaign_id"] = campaign_id
        sessions = await self.repository.query_live_call_sessions(filters)
        # Filter for active statuses only
        active_sessions = [s for s in sessions if s.get("status") not in ("ended", "failed")]
        return active_sessions[:limit]

    async def end_call(self, call_session_id: str, operator: str, reason: str) -> CallControlResult:
        """Terminate a live call session locally (and update database records)."""
        if not operator or not operator.strip():
            return CallControlResult(
                action="end_call",
                success=False,
                call_session_id=call_session_id,
                message="Operator name is required.",
                error="OPERATOR_REQUIRED",
            )

        # 1. Fetch Session
        session = await self.repository.get_live_call_session(call_session_id)
        if not session:
            # Fall back to checking if ID matches attempt_id or call_id
            sessions = await self.repository.query_live_call_sessions({"call_id": call_session_id})
            if sessions:
                session = sessions[0]
            else:
                return CallControlResult(
                    action="end_call",
                    success=False,
                    call_session_id=call_session_id,
                    message=f"LiveCallSession {call_session_id} not found.",
                    error="NOT_FOUND",
                )

        prev_status = session.get("status")
        now_str = datetime.now(timezone.utc).isoformat()

        # Update Session
        session["status"] = "ended"
        session["ended_at"] = now_str
        session["updated_at"] = now_str
        session.setdefault("metadata", {})
        session["metadata"]["end_reason"] = reason
        session["metadata"]["ended_by"] = operator
        await self.repository.save_live_call_session(**session)

        attempt_id = session.get("attempt_id")
        lead_id = session.get("lead_id")
        campaign_id = session.get("campaign_id")

        # 2. Update Attempt
        if attempt_id:
            attempt = await self.repository.get_call_attempt(attempt_id)
            if attempt:
                attempt["status"] = "completed"
                attempt["ended_at"] = now_str
                attempt["outcome"] = "ended"
                attempt.setdefault("metadata", {})
                attempt["metadata"]["end_reason"] = reason
                await self.repository.save_call_attempt(**attempt)

        # 3. Update Lead
        if lead_id:
            lead = await self.repository.get_campaign_lead(lead_id)
            if lead:
                lead["status"] = "completed"
                lead["updated_at"] = now_str
                await self.repository.save_campaign_lead(**lead)

        # 4. Log control event
        if campaign_id:
            await self.repository.save_campaign_control_event(
                campaign_id=campaign_id,
                event_type="call_ended",
                operator=operator,
                reason=f"Call session {call_session_id} ended by operator. Reason: {reason}",
                previous_status=prev_status,
                new_status="ended",
            )

        return CallControlResult(
            action="end_call",
            success=True,
            call_session_id=call_session_id,
            attempt_id=attempt_id,
            previous_status=prev_status,
            new_status="ended",
            message=f"Call ended successfully. Reason: {reason}",
        )

    async def mark_call_outcome(
        self, attempt_id: str, outcome: str, operator: str, metadata: dict | None = None
    ) -> CallControlResult:
        """Mark final call attempt outcome and update lead status accordingly."""
        if not operator or not operator.strip():
            return CallControlResult(
                action="mark_outcome",
                success=False,
                attempt_id=attempt_id,
                message="Operator name is required.",
                error="OPERATOR_REQUIRED",
            )

        # Allowed outcomes check
        allowed_outcomes = (
            "no_answer", "voicemail", "busy", "failed", "answered",
            "callback", "not_interested", "dnc", "wrong_number",
            "transferred", "sale", "unknown"
        )
        if outcome not in allowed_outcomes:
            return CallControlResult(
                action="mark_outcome",
                success=False,
                attempt_id=attempt_id,
                message=f"Invalid outcome: {outcome}. Allowed: {allowed_outcomes}",
                error="INVALID_OUTCOME",
            )

        # 1. Fetch Attempt
        attempt = await self.repository.get_call_attempt(attempt_id)
        if not attempt:
            return CallControlResult(
                action="mark_outcome",
                success=False,
                attempt_id=attempt_id,
                message=f"CallAttempt {attempt_id} not found.",
                error="NOT_FOUND",
            )

        prev_outcome = attempt.get("outcome")
        now_str = datetime.now(timezone.utc).isoformat()

        # Update attempt
        attempt["outcome"] = outcome
        attempt["status"] = "completed"
        attempt["ended_at"] = attempt.get("ended_at") or now_str
        attempt.setdefault("metadata", {})
        if metadata:
            attempt["metadata"].update(metadata)
        attempt["metadata"]["outcome_marked_by"] = operator
        
        # Check transfer status if transferred
        if outcome == "transferred":
            attempt["transfer_consent"] = True
            attempt["transfer_attempted"] = True
            attempt["transfer_successful"] = True

        await self.repository.save_call_attempt(**attempt)

        # 2. Fetch and Update Lead
        lead_id = attempt.get("lead_id")
        campaign_id = attempt.get("campaign_id")

        if lead_id:
            lead = await self.repository.get_campaign_lead(lead_id)
            if lead:
                lead["outcome"] = outcome
                lead["updated_at"] = now_str

                # Map outcome to lead status
                if outcome in ("dnc", "wrong_number"):
                    lead["status"] = outcome
                    # Also register phone in the general dnc_requests collection for suppression scrub
                    phone = lead.get("phone_number")
                    if phone:
                        await self.repository.save_dnc_request(
                            call_id=attempt_id,
                            lead_id=lead_id,
                            phone_e164=phone,
                            campaign_id=campaign_id,
                            reason=f"Operator marked outcome: {outcome}"
                        )
                elif outcome == "callback":
                    lead["status"] = "callback"
                    # Set next attempt to 24 hours later if not explicitly set in metadata
                    next_att = now_str
                    if metadata and metadata.get("callback_time"):
                        next_att = metadata["callback_time"]
                    else:
                        from datetime import timedelta
                        next_att = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
                    lead["next_attempt_at"] = next_att
                elif outcome == "transferred":
                    lead["status"] = "completed"
                else:
                    # new|queued|dialing|in_call|completed|callback|dnc|wrong_number|failed|suppressed|do_not_call
                    lead["status"] = "completed"

                await self.repository.save_campaign_lead(**lead)

        # 3. Clean up Live Call Session if still active
        sessions = await self.repository.query_live_call_sessions({"attempt_id": attempt_id})
        for s in sessions:
            if s.get("status") not in ("ended", "failed"):
                s["status"] = "ended"
                s["ended_at"] = now_str
                s["outcome"] = outcome
                await self.repository.save_live_call_session(**s)

        return CallControlResult(
            action="mark_outcome",
            success=True,
            attempt_id=attempt_id,
            previous_status=prev_outcome,
            new_status=outcome,
            message=f"Outcome updated to {outcome} successfully.",
        )

    async def export_call_to_training(self, attempt_id: str, operator: str) -> CallControlResult:
        """Export completed call attempt transcript and metadata to training intake folder."""
        if not operator or not operator.strip():
            return CallControlResult(
                action="export_training",
                success=False,
                attempt_id=attempt_id,
                message="Operator name is required.",
                error="OPERATOR_REQUIRED",
            )

        # 1. Fetch Attempt
        attempt = await self.repository.get_call_attempt(attempt_id)
        if not attempt:
            return CallControlResult(
                action="export_training",
                success=False,
                attempt_id=attempt_id,
                message=f"CallAttempt {attempt_id} not found.",
                error="NOT_FOUND",
            )

        lead_id = attempt.get("lead_id")
        campaign_id = attempt.get("campaign_id")

        # 2. Query Turns, Tools, and QA
        # We query call_turns table using call_id = attempt_id or call_id = attempt.get("livekit_room_name")
        turns = []
        try:
            raw_turns = await self.repository._store.query("call_turns", {"call_id": attempt_id})
            raw_turns.sort(key=lambda t: t.get("turn_number", 0))
            for t in raw_turns:
                turns.append({
                    "speaker": t.get("speaker", "unknown"),
                    "text": t.get("text", ""),
                    "timestamp": t.get("created_at") or t.get("timestamp"),
                })
        except Exception:
            pass

        tools = []
        try:
            raw_tools = await self.repository._store.query("tool_events", {"call_id": attempt_id})
            for tool in raw_tools:
                tools.append(dict(tool))
        except Exception:
            pass

        qa = {}
        try:
            raw_qa = await self.repository._store.query("qa_reports", {"call_id": attempt_id})
            if raw_qa:
                qa = dict(raw_qa[0])
        except Exception:
            pass

        # 3. Build PostCallPayload
        lead = await self.repository.get_campaign_lead(lead_id) if lead_id else None
        phone = lead.get("phone_number") if lead else None

        payload = {
            "call_id": attempt_id,
            "started_at": attempt.get("started_at"),
            "ended_at": attempt.get("ended_at"),
            "direction": "outbound",
            "campaign": campaign_id,
            "prospect_phone": phone,
            "outcome": attempt.get("outcome", "unknown"),
            "transfer_consent": bool(attempt.get("transfer_consent", False)),
            "turns": turns,
            "tool_events": tools,
            "qa": qa,
            "metadata": {
                "lead_id": lead_id,
                "exported_by": operator,
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
        }

        # 4. Invoke PostCallExporter
        exporter = PostCallExporter(self.repository)
        config = PostCallExportConfig(
            enabled=True,
            run_intake_after_export=True,
            intake_sync=True,
        )

        res = await exporter.export_completed_call(payload, config)

        if res.exported:
            attempt["post_call_export_path"] = res.output_path
            attempt.setdefault("metadata", {})
            attempt["metadata"]["training_exported"] = True
            await self.repository.save_call_attempt(**attempt)

            return CallControlResult(
                action="export_training",
                success=True,
                attempt_id=attempt_id,
                message=f"Call successfully exported to training. Output path: {res.output_path}",
                warnings=res.warnings,
            )
        else:
            return CallControlResult(
                action="export_training",
                success=False,
                attempt_id=attempt_id,
                message=f"Export failed: {res.error}",
                error="EXPORT_FAILED",
                warnings=res.warnings,
            )
