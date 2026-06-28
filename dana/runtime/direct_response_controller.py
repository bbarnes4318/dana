"""Direct Response Controller for Dana voice agent.

Orchestrates the direct transcript → runtime → LLM → session.say() path
as a production-ready controller with transcript filtering, dedupe,
stage-aware response policy, queue management, barge-in handling,
echo suppression, and metrics logging.

Extracted from voice_session.py to keep the session file focused on
LiveKit event wiring only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transcript extraction
# ---------------------------------------------------------------------------

def extract_transcript_text(event: Any) -> str:
    """Extract text from a LiveKit transcription event.

    Supports all known event shapes without raising on unknown layouts:
      - event.transcript  (str)
      - event.transcript.text
      - event.text
      - event.alternatives[0]  (str)
      - event.alternatives[0].text
      - event.alternatives[0].transcript
    """
    try:
        if hasattr(event, "transcript") and event.transcript:
            if isinstance(event.transcript, str):
                return event.transcript
            if hasattr(event.transcript, "text") and event.transcript.text:
                if isinstance(event.transcript.text, str):
                    return event.transcript.text
        if hasattr(event, "text") and event.text:
            if isinstance(event.text, str):
                return event.text
        if hasattr(event, "alternatives") and event.alternatives:
            alt = event.alternatives[0]
            if isinstance(alt, str):
                return alt
            if hasattr(alt, "text") and alt.text:
                if isinstance(alt.text, str):
                    return alt.text
            if hasattr(alt, "transcript") and alt.transcript:
                if isinstance(alt.transcript, str):
                    return alt.transcript
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Turn policy returned by DirectResponsePolicy
# ---------------------------------------------------------------------------

@dataclass
class TurnPolicy:
    """Describes how a specific turn should be handled."""
    max_tokens: int = 70
    instruction_suffix: str = ""
    should_end_after_response: bool = False


# ---------------------------------------------------------------------------
# Valid short intents that bypass the min-chars filter
# ---------------------------------------------------------------------------

VALID_SHORT_INTENTS = frozenset([
    "yes", "no", "ok", "okay", "stop",
    "wrong number", "remove me",
    "don't call", "do not call",
])

# ---------------------------------------------------------------------------
# Keyword sets for category detection
# ---------------------------------------------------------------------------

_DNC_KEYWORDS = [
    "do not call", "don't call", "stop calling", "remove me",
    "take me off", "put me on the do not call",
    "dnc", "unsubscribe",
]

_WRONG_NUMBER_KEYWORDS = [
    "wrong number", "wrong person",
]

_CONFUSION_KEYWORDS = [
    "who is this", "who are you", "what is this", "what's this about",
    "why are you calling", "what do you want", "who's calling",
    "what is this about", "who called me",
]

_STOP_KEYWORDS = [
    "stop", "hang up", "go away", "leave me alone", "goodbye",
]

_OBJECTION_KEYWORDS = [
    "not interested", "no thanks", "no thank you",
    "already have", "i have insurance", "too expensive",
    "send me info", "send information", "call later",
    "call me back", "call back", "busy right now",
    "in a meeting", "can't talk",
]


# ---------------------------------------------------------------------------
# DirectResponsePolicy
# ---------------------------------------------------------------------------

class DirectResponsePolicy:
    """Determines stage-aware token limits and instruction suffixes."""

    def __init__(self, config: Any) -> None:
        self._config = config

    def get_turn_policy(self, stage: Any, transcript: str) -> TurnPolicy:
        """Return a TurnPolicy based on the current stage and transcript content.

        Args:
            stage: The current CallStage enum value (or its .value string).
            transcript: The user's transcript text.

        Returns:
            A TurnPolicy with max_tokens, instruction_suffix, and should_end.
        """
        from core.intent.short_response_intent import classify_intent
        intent = classify_intent(transcript)
        hard_max = self._config.direct_response_hard_max_tokens

        # --- DNC / stop / remove me ---
        if intent == "dnc":
            return TurnPolicy(
                max_tokens=min(self._config.direct_response_max_tokens_stop, hard_max),
                instruction_suffix=(
                    "Respond in ONE polite sentence only. Do NOT ask any question. "
                    "Acknowledge the request and confirm removal."
                ),
                should_end_after_response=True,
            )

        # --- wrong number ---
        if intent == "wrong_number":
            return TurnPolicy(
                max_tokens=min(self._config.direct_response_max_tokens_stop, hard_max),
                instruction_suffix=(
                    "Respond in ONE polite sentence only. Do NOT ask any question. "
                    "Apologize for the mistake and confirm this number will not be contacted again."
                ),
                should_end_after_response=True,
            )

        # --- hostile refusal ---
        if intent == "hostile_refusal":
            return TurnPolicy(
                max_tokens=min(40, hard_max),
                instruction_suffix=(
                    "Respond in ONE short polite sentence only. Do NOT ask any question. "
                    "Understood. I won’t keep you. Take care."
                ),
                should_end_after_response=True,
            )

        # --- confusion / who is this ---
        if intent == "confusion":
            return TurnPolicy(
                max_tokens=min(self._config.direct_response_max_tokens_objection, hard_max),
                instruction_suffix=(
                    "Respond in one or two short sentences. Answer the question directly. "
                    "Do NOT restart the full pitch. "
                    "Ask one simple follow-up question only if appropriate."
                ),
                should_end_after_response=False,
            )

        # --- objection / refusal ---
        if intent in ("refusal", "off_topic"):
            return TurnPolicy(
                max_tokens=min(self._config.direct_response_max_tokens_objection, hard_max),
                instruction_suffix=(
                    "Respond in a maximum of two short sentences. "
                    "Acknowledge the concern briefly. "
                    "Move to the next appropriate step in the conversation."
                ),
                should_end_after_response=False,
            )

        # --- normal progression ---
        return TurnPolicy(
            max_tokens=min(self._config.direct_response_max_tokens_default, hard_max),
            instruction_suffix=(
                "Respond in one short sentence. Ask one clear question."
            ),
            should_end_after_response=False,
        )


# ---------------------------------------------------------------------------
# Response cleanup helpers
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(r"^(?:Agent|Dana|Assistant|AI)\s*:\s*", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[\-\*•]\s+", re.MULTILINE)
_MULTI_SPACE_RE = re.compile(r"  +")


def clean_response(text: str) -> str:
    """Strip markdown, accidental labels, and collapse whitespace."""
    if not text:
        return ""
    text = text.strip()
    text = _LABEL_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = text.strip()
    return text


def get_fallback_response(stage: Any, transcript: str) -> str:
    """Return a safe fallback response when the LLM returns empty text."""
    text_lower = transcript.lower().strip() if transcript else ""

    if any(kw in text_lower for kw in _DNC_KEYWORDS + _STOP_KEYWORDS):
        return "I understand, I'll make sure this number is not contacted again."

    if any(kw in text_lower for kw in _WRONG_NUMBER_KEYWORDS):
        return "I understand, I'll make sure this number is not contacted again."

    if any(kw in text_lower for kw in _CONFUSION_KEYWORDS):
        return (
            "I'm calling about the final expense information you requested; "
            "are you still open to looking at it?"
        )

    return "Are you still open to looking at those options?"


# ---------------------------------------------------------------------------
# Similarity helper for echo suppression
# ---------------------------------------------------------------------------

def compute_similarity(a: str, b: str) -> float:
    """Compute character-level similarity ratio between two strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ---------------------------------------------------------------------------
# DirectResponseController
# ---------------------------------------------------------------------------

class DirectResponseController:
    """Production-ready controller for the direct transcript → response path.

    Consumes final transcripts from LiveKit, filters/dedupes them,
    routes them through the AgentRuntime via process_user_turn,
    then plays the response via session.say().
    """

    def __init__(
        self,
        session: Any,
        agent: Any,
        adapter: Any,
        latency_recorder: Any,
        room: Any,
        config: Any,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self._session = session
        self._agent = agent
        self._adapter = adapter
        self._latency = latency_recorder
        self._room = room
        self._config = config
        self._log = log or logger
        self._policy = DirectResponsePolicy(config)

        # Queue
        self._queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=config.direct_response_queue_maxsize,
        )
        self._consumer_task: Optional[asyncio.Task] = None

        # Dedupe state
        self._last_transcript: str = ""
        self._last_transcript_time: float = 0.0

        # Echo suppression state
        self._last_assistant_text: str = ""
        self._last_assistant_end_time: float = 0.0
        self._agent_is_speaking: bool = False

        # Barge-in state
        self._interrupted: bool = False

        # Running / ending flags
        self._running: bool = False
        self._ending_call: bool = False
        self._ended_call: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the queue consumer loop."""
        if self._running or (self._consumer_task and not self._consumer_task.done()):
            self._log.info("DIRECT_CONTROLLER_ALREADY_STARTED")
            return
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop())
        self._log.info("DirectResponseController started")

    async def stop(self) -> None:
        """Stop the consumer and drain the queue."""
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        self._log.info("DirectResponseController stopped")

    # ------------------------------------------------------------------
    # Event handlers (called from voice_session event hooks)
    # ------------------------------------------------------------------

    def handle_transcription_event(self, event: Any) -> None:
        """Handle a LiveKit user_input_transcribed event.

        Extracts text, applies filters, and enqueues accepted transcripts.
        Must be safe to call from a sync LiveKit event callback.
        """
        if self._ending_call or self._ended_call:
            self._log.info("DIRECT_POST_END_TURN_IGNORED")
            return

        text = extract_transcript_text(event)
        self._log.info("USER_TRANSCRIPT_FINAL")
        self._log.info("FINAL_TRANSCRIPT_TEXT_LENGTH: %d", len(text))
        self._log.info("DIRECT_TRANSCRIPT_EXTRACTED: '%s'", text[:120])

        # ---- Filters ----

        # 1. Empty
        if not text or not text.strip():
            self._log.info("DIRECT_TRANSCRIPT_IGNORED_EMPTY")
            return

        text = text.strip()

        # 2. Dedupe (exact and near-duplicates)
        now = time.monotonic()
        dedupe_window_s = self._config.direct_response_dedupe_window_ms / 1000.0
        if (now - self._last_transcript_time) < dedupe_window_s:
            if text == self._last_transcript:
                self._log.info("DIRECT_TRANSCRIPT_DEDUPED")
                return
            similarity = compute_similarity(text, self._last_transcript)
            threshold = getattr(self._config, "direct_response_echo_similarity_threshold", 0.85)
            if similarity >= threshold:
                self._log.info("DIRECT_DUPLICATE_FINAL_TRANSCRIPT_SUPPRESSED")
                return

        # 3. Min length (with short-intent allowlist)
        text_lower = text.lower().strip()
        if len(text) < self._config.direct_response_min_chars:
            if text_lower not in VALID_SHORT_INTENTS:
                self._log.info("DIRECT_TRANSCRIPT_IGNORED_TOO_SHORT")
                return

        # Even if above min chars, check if it's a very short non-intent
        if len(text) < self._config.direct_response_min_chars and text_lower in VALID_SHORT_INTENTS:
            pass  # allowed through
        elif len(text) < self._config.direct_response_min_chars:
            self._log.info("DIRECT_TRANSCRIPT_IGNORED_TOO_SHORT")
            return

        # 4. Echo suppression
        if self._last_assistant_text:
            time_since_agent_stop = now - self._last_assistant_end_time
            if self._agent_is_speaking or time_since_agent_stop < 1.0:
                similarity = compute_similarity(text, self._last_assistant_text)
                if similarity >= self._config.direct_response_echo_similarity_threshold:
                    self._log.info(
                        "DIRECT_TRANSCRIPT_IGNORED_ECHO: similarity=%.2f threshold=%.2f",
                        similarity,
                        self._config.direct_response_echo_similarity_threshold,
                    )
                    return

        # ---- Accepted ----
        self._last_transcript = text
        self._last_transcript_time = now
        self._log.info("DIRECT_TRANSCRIPT_ACCEPTED: '%s'", text[:120])

        # Check for final intent before enqueuing
        from core.intent.short_response_intent import classify_intent
        intent = classify_intent(text)
        is_final = (intent in ("dnc", "wrong_number", "hostile_refusal"))
        if is_final:
            self._ending_call = True
            self._log.info("DIRECT_FINAL_INTENT_DETECTED")
            cleared = self._clear_queue()
            if cleared > 0:
                self._log.info("DIRECT_QUEUE_CLEARED_FOR_FINAL_INTENT")

        # Enqueue with overflow handling
        self._enqueue(text)

    def handle_user_state_changed(self, ev: Any) -> None:
        """Handle user state changes for barge-in detection."""
        state_str = str(ev.new_state).lower()
        if "speaking" in state_str and self._agent_is_speaking:
            self._log.info("DIRECT_BARGE_IN_DETECTED")
            self._interrupted = True
            # Interrupt current playback safely
            try:
                if hasattr(self._session, "interrupt"):
                    if asyncio.iscoroutinefunction(self._session.interrupt):
                        asyncio.create_task(self._session.interrupt())
                    else:
                        self._session.interrupt()
            except Exception as ex:
                self._log.error("Error during barge-in interrupt: %s", ex)

    def handle_agent_state_changed(self, ev: Any) -> None:
        """Track agent speaking state for echo suppression timing."""
        state_str = str(ev.new_state).lower()
        old_state_str = str(ev.old_state).lower()
        if "speaking" in state_str:
            self._agent_is_speaking = True
        elif "speaking" in old_state_str:
            self._agent_is_speaking = False
            self._last_assistant_end_time = time.monotonic()

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def _clear_queue(self) -> int:
        """Clear all pending transcripts in the queue and return count of cleared items."""
        count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                count += 1
            except asyncio.QueueEmpty:
                break
        return count

    def _enqueue(self, text: str) -> None:
        """Enqueue a transcript, dropping oldest if queue is full."""
        try:
            self._queue.put_nowait(text)
            self._log.info("DIRECT_QUEUE_PUT: '%s'", text[:80])
        except asyncio.QueueFull:
            # Drop oldest to make room for newest
            try:
                dropped = self._queue.get_nowait()
                self._log.info("DIRECT_QUEUE_DROPPED_OLDEST: '%s'", dropped[:80])
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(text)
                self._log.info("DIRECT_QUEUE_PUT: '%s' (after drop)", text[:80])
            except asyncio.QueueFull:
                self._log.error("DIRECT_QUEUE_STILL_FULL after drop — this should not happen")
        self._log.info("DIRECT_QUEUE_SIZE: %d", self._queue.qsize())

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consumer_loop(self) -> None:
        """Sequential consumer: processes one turn at a time."""
        self._log.info("Direct response consumer loop started")
        try:
            while self._running:
                transcript_text = await self._queue.get()
                try:
                    await self._process_turn(transcript_text)
                except Exception as ex:
                    self._log.error("ERROR_DIRECT_RESPONSE_TURN: %s", ex, exc_info=True)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            self._log.info("Direct response consumer loop cancelled")

    async def _process_turn(self, transcript_text: str) -> None:
        """Process a single direct response turn end-to-end."""
        if self._ended_call:
            self._log.info("DIRECT_POST_END_TURN_IGNORED")
            return

        turn_start = time.monotonic()
        self._log.info("DIRECT_RESPONSE_STARTED")

        # Reset interrupted state for this turn
        was_interrupted = self._interrupted
        self._interrupted = False

        # Add user transcript to session.history (mirror only)
        self._mirror_user_message(transcript_text)

        # Determine turn policy
        stage = None
        if self._adapter and hasattr(self._adapter, "state_machine"):
            stage = self._adapter.state_machine.call_state.current_stage
        
        from core.intent.short_response_intent import classify_intent
        intent = classify_intent(transcript_text)
        self._log.info("DIRECT_INTENT_DETECTED: '%s'", intent)

        # Check for pre-generation final intents
        is_final_intent = (intent in ("dnc", "wrong_number", "hostile_refusal"))
        if is_final_intent:
            self._ending_call = True
            self._log.info("DIRECT_FINAL_INTENT_DETECTED")
            cleared = self._clear_queue()
            if cleared > 0:
                self._log.info("DIRECT_QUEUE_CLEARED_FOR_FINAL_INTENT")

        stage_val = stage.value if hasattr(stage, "value") else str(stage)
        self._log.info("DIRECT_STAGE_BEFORE: '%s'", stage_val)

        policy = self._policy.get_turn_policy(stage, transcript_text)
        self._log.info("DIRECT_POLICY_SELECTED: max_tokens=%d", policy.max_tokens)

        # Build chat_fn
        async def chat_fn(instructions: str) -> str:
            return await self._run_llm(instructions, transcript_text, policy)

        # Call adapter.process_user_turn
        result = await self._adapter.process_user_turn(
            transcript_text, chat_fn, interrupted=was_interrupted,
        )
        response_text = result.agent_response or ""
        self._agent.current_turn_response = response_text

        # Update stage in context registry
        try:
            from speech.context_registry import update_call_stage
            update_call_stage(self._adapter.call_id, result.stage)
        except Exception:
            pass

        # Clean response
        response_text = clean_response(response_text)

        # Direct Response Validation
        from dana.runtime.direct_response_validator import DirectResponseValidator
        validator = DirectResponseValidator(self._config)

        validation_res = validator.validate(response_text, result.stage, transcript_text)
        if not validation_res.is_valid:
            self._log.info("DIRECT_RESPONSE_VALIDATION_FAILED: %s", validation_res.reason)
            
            # 1. Stricter Prompt Instruction
            stricter_instructions = (
                "Respond in a maximum of two short sentences. "
                "Do NOT repeat the greeting or American Beneficiary introduction. "
                "Acknowledge the caller's input and ask exactly one next campaign question. "
                "Do NOT say 'Well, fair enough' or 'I understand'."
            )
            self._log.info("DIRECT_RESPONSE_REGENERATED")
            
            # Rebuild chat_fn with stricter policy instruction suffix
            stricter_policy = TurnPolicy(
                max_tokens=policy.max_tokens,
                instruction_suffix=stricter_instructions,
                should_end_after_response=policy.should_end_after_response,
            )
            async def stricter_chat_fn(instructions: str) -> str:
                return await self._run_llm(instructions, transcript_text, stricter_policy)

            # Re-call adapter.process_user_turn
            result = await self._adapter.process_user_turn(
                transcript_text, stricter_chat_fn, interrupted=was_interrupted,
            )
            response_text = clean_response(result.agent_response or "")
            self._agent.current_turn_response = response_text
            
            # Second validation check
            second_validation_res = validator.validate(response_text, result.stage, transcript_text)
            if not second_validation_res.is_valid:
                self._log.info("DIRECT_RESPONSE_VALIDATION_FAILED: %s", second_validation_res.reason)
                # 2. Stage-aware deterministic fallback
                response_text = validator.get_deterministic_fallback(result.stage, transcript_text)
                self._log.info("DIRECT_RESPONSE_DETERMINISTIC_FALLBACK_USED")
            else:
                self._log.info("DIRECT_RESPONSE_VALIDATION_PASSED")
        else:
            self._log.info("DIRECT_RESPONSE_VALIDATION_PASSED")

        self._log.info("DIRECT_RESPONSE_TEXT_LENGTH: %d", len(response_text))

        # Play response via session.say()
        if response_text.strip():
            self._log.info("DIRECT_SAY_STARTED")
            try:
                handle = self._session.say(response_text)
                await handle.wait_for_playout()
                self._log.info("DIRECT_SAY_COMPLETED")
            except asyncio.CancelledError:
                self._log.info("DIRECT_SAY_INTERRUPTED")
                raise
            except Exception as ex:
                self._log.error("ERROR_DIRECT_SAY: %s", ex, exc_info=True)

        next_stage_val = result.stage
        self._log.info("DIRECT_STAGE_AFTER: '%s'", next_stage_val)

        total_ms = int((time.monotonic() - turn_start) * 1000)
        self._log.info("DIRECT_TURN_TOTAL_MS: %d", total_ms)

        # Track echo suppression
        self._last_assistant_text = response_text
        self._last_assistant_end_time = time.monotonic()

        # Mirror assistant message to session.history
        self._mirror_assistant_message(response_text)

        # Compute turn latency
        turn_elapsed_ms = (time.monotonic() - turn_start) * 1000
        self._log.info("DIRECT_TURN_TOTAL_MS: %.0f", turn_elapsed_ms)
        if turn_elapsed_ms > self._config.direct_response_max_turn_ms:
            self._log.warning(
                "DIRECT_TURN_LATENCY_EXCEEDED: %.0fms > %dms target",
                turn_elapsed_ms,
                self._config.direct_response_max_turn_ms,
            )

        # Handle call ending
        if result.should_end_call or policy.should_end_after_response or self._ending_call:
            if not self._ending_call:
                self._ending_call = True
                self._log.info("DIRECT_FINAL_INTENT_DETECTED")
                cleared = self._clear_queue()
                if cleared > 0:
                    self._log.info("DIRECT_QUEUE_CLEARED_FOR_FINAL_INTENT")
            self._ended_call = True
            await self._handle_call_end(result)

    # ------------------------------------------------------------------
    # LLM call builder
    # ------------------------------------------------------------------

    async def _run_llm(
        self,
        instructions: str,
        transcript_text: str,
        policy: TurnPolicy,
    ) -> str:
        """Build ChatContext and run LLM for a direct response turn."""
        try:
            from livekit.agents import llm
        except ImportError:
            self._log.error("Cannot import livekit.agents.llm")
            return ""

        new_ctx = llm.ChatContext()

        # Build combined system prompt
        loader = getattr(self._agent, "prompt_loader", None)
        if not loader and self._adapter:
            loader = getattr(self._adapter, "prompt_loader", None)
        static_prompt = ""
        if loader and hasattr(loader, "build_system_prompt"):
            static_prompt = loader.build_system_prompt()

        combined_prompt = (
            f"{static_prompt}\n\n{instructions}\n\n{policy.instruction_suffix}"
        )
        new_ctx.add_message(role="system", content=combined_prompt)

        # Copy recent conversation history from repository
        history_msgs = []
        try:
            turns = await self._adapter.repository.query_call_turns({"call_id": self._adapter.call_id})
            turns = sorted(turns, key=lambda t: t.get("turn_number", 0))
            for turn in turns:
                speaker = turn.get("speaker", "")
                text = (turn.get("text") or "").strip()
                if not text:
                    continue
                if speaker == "user":
                    new_ctx.add_message(role="user", content=text)
                elif speaker in ("agent", "assistant"):
                    new_ctx.add_message(role="assistant", content=text)
        except Exception as exc:
            self._log.error("Failed to query call turns from repository: %s", exc)
            # Fallback to session.history if repository query fails
            if hasattr(self._session, "history") and self._session.history:
                raw_msgs = getattr(self._session.history, "messages", [])
                if callable(raw_msgs):
                    raw_msgs = raw_msgs()
                for msg in raw_msgs:
                    if msg.role in ("user", "assistant"):
                        msg_text = _get_msg_text(msg)
                        if msg_text:
                            new_ctx.add_message(role=msg.role, content=msg_text)

        # Estimate prompt tokens
        try:
            from metrics.model_cost_metrics import estimate_llm_tokens
            prompt_str = combined_prompt + "".join(
                _get_msg_text(m) for m in new_ctx.messages if _get_msg_text(m)
            )
            self._agent.prompt_tokens += estimate_llm_tokens(prompt_str)
        except Exception:
            pass

        # Run LLM
        max_tokens = min(policy.max_tokens, self._config.direct_response_hard_max_tokens)
        temperature = getattr(self._config, "temperature", 0.2)
        top_p = getattr(self._config, "top_p", 0.9)

        try:
            extra_args = {
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "frequency_penalty": 0.15,
            }
            stream = self._agent.llm.chat(
                chat_ctx=new_ctx,
                extra_kwargs=extra_args,
            )

            response_text = ""
            first_token_logged = False
            async for chunk in stream:
                content = chunk.delta.content if chunk.delta else ""
                if content:
                    if not first_token_logged:
                        self._log.info("DIRECT_LLM_FIRST_TOKEN")
                        first_token_logged = True
                    response_text += content

            # Estimate completion tokens
            try:
                from metrics.model_cost_metrics import estimate_llm_tokens
                self._agent.completion_tokens += estimate_llm_tokens(response_text)
            except Exception:
                pass

            return response_text
        except Exception as ex:
            self._log.error("ERROR_DIRECT_LLM: %s", ex, exc_info=True)
            return ""

    # ------------------------------------------------------------------
    # Call ending
    # ------------------------------------------------------------------

    async def _handle_call_end(self, result: Any) -> None:
        """Handle clean call disconnect after response playout."""
        self._log.info("DIRECT_CALL_END_REQUESTED")

        # Check for warm bridge
        is_warm_bridge = False
        try:
            from core.runtime_events import ToolTriggeredEvent
            for ev in self._adapter.runtime.events:
                if (
                    isinstance(ev, ToolTriggeredEvent)
                    and ev.tool_name in ("feTransfer", "transfer_to_agent")
                    and ev.success
                ):
                    msg = ev.result_message.lower() if ev.result_message else ""
                    if "warm" in msg or os.getenv("DANA_TRANSFER_MODE", "").lower() == "warm_bridge":
                        is_warm_bridge = True
                        break
        except Exception:
            pass

        if is_warm_bridge:
            self._log.info("Warm bridge transfer — Dana will mute and leave later.")
            self._agent.should_disconnect = False
            self._agent.warm_bridge_active = True

            async def warm_bridge_leave():
                await asyncio.sleep(15.0)
                self._log.info("warm_bridge_active_dana_suppressed: Dana leaving agent session.")
                try:
                    await self._session.aclose()
                except Exception as e:
                    self._log.error("Error closing session during warm bridge: %s", e)

            asyncio.create_task(warm_bridge_leave())
        else:
            self._agent.should_disconnect = True

            # Cancel existing fallback disconnect
            if getattr(self._agent, "fallback_disconnect_task", None):
                self._agent.fallback_disconnect_task.cancel()

            # Determine fast disconnect delay (0.5s - 1.0s) for DNC/wrong number/hostile/refusal
            from core.intent.short_response_intent import classify_intent
            last_intent = classify_intent(self._last_transcript)
            is_fast_disconnect = (
                last_intent in ("dnc", "wrong_number", "hostile_refusal", "refusal")
                or (result and getattr(result, "stage", None) in ("end", "disqualified"))
            )
            default_delay = 0.8 if is_fast_disconnect else 8.0
            
            # Allow environment override
            env_delay = os.getenv("DANA_FINAL_DISCONNECT_DELAY")
            if env_delay is not None:
                try:
                    delay = float(env_delay)
                except ValueError:
                    delay = default_delay
            else:
                delay = default_delay

            async def disconnect_after_delay(d: float):
                try:
                    await asyncio.sleep(d)
                    if self._room and hasattr(self._room, "isconnected"):
                        connected = self._room.isconnected
                        if callable(connected):
                            connected = connected()
                        if connected:
                            self._log.info("DIRECT_CALL_DISCONNECTED")
                            await self._room.disconnect()
                except asyncio.CancelledError:
                    self._log.info("Fallback disconnect cancelled")

            self._agent.fallback_disconnect_task = asyncio.create_task(
                disconnect_after_delay(delay)
            )

    # ------------------------------------------------------------------
    # Session.history mirror helpers
    # ------------------------------------------------------------------

    def _mirror_user_message(self, text: str) -> None:
        """Add user message to session.history as a debugging mirror."""
        try:
            if hasattr(self._session, "history") and self._session.history:
                msgs = getattr(self._session.history, "messages", [])
                if callable(msgs):
                    msgs = msgs()
                last_msg = msgs[-1] if msgs else None
                last_text = _get_msg_text(last_msg) if last_msg else ""
                if not last_msg or last_msg.role != "user" or text != last_text:
                    self._session.history.add_message(role="user", content=text)
        except Exception as ex:
            self._log.debug("Failed to mirror user message: %s", ex)

    def _mirror_assistant_message(self, text: str) -> None:
        """Add assistant message to session.history as a debugging mirror."""
        try:
            if hasattr(self._session, "history") and self._session.history:
                self._session.history.add_message(role="assistant", content=text)
        except Exception as ex:
            self._log.debug("Failed to mirror assistant message: %s", ex)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_msg_text(m: Any) -> str:
    """Safely extract text content from a ChatMessage."""
    if not m:
        return ""
    try:
        tc = getattr(m, "text_content", None)
        if isinstance(tc, str):
            return tc
        c = getattr(m, "content", None)
        if isinstance(c, str):
            return c
    except Exception:
        pass
    return ""
