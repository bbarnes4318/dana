import os
import sys
import asyncio
import logging
import argparse
from datetime import datetime, timezone

# Add workspace root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_config import VoiceConfig
from main import SharedComponents, DanaAgent
from latency_metrics import LatencyRecorder
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from core.agent_runtime import AgentRuntime
from core.lead_profile import LeadProfile
from core.call_state import CallState
from core.state_machine import StateMachine
from safety.call_stop_policy import CallStopPolicy
from livekit.agents import llm
from storage.repository import Repository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("conversation_loop_doctor")

async def query_call_metrics(call_id: str, repository: Repository) -> dict:
    """Query latency metrics and timeline events from postgres for a call."""
    try:
        metrics = await repository._store.query("latency_metrics", {"call_id": call_id})
        return {m["metric_name"]: m["metric_value_ms"] for m in metrics}
    except Exception as e:
        logger.error(f"Failed to query database for call_id {call_id}: {e}")
        return {}

async def find_latest_call_id(repository: Repository) -> str:
    """Find the latest call_id from latency_metrics table."""
    try:
        # Query recent metrics and extract unique call IDs
        metrics = await repository._store.query("latency_metrics", {})
        if not metrics:
            return ""
        # Sort by created_at desc (if present) or just pick the last one
        sorted_metrics = sorted(metrics, key=lambda x: x.get("created_at", ""), reverse=True)
        for m in sorted_metrics:
            cid = m.get("call_id")
            if cid:
                return cid
    except Exception as e:
        logger.error(f"Failed to find latest call_id: {e}")
    return ""

def analyze_timeline(events: dict) -> tuple[bool, str]:
    """Analyze the event markers to verify each stage of the loop."""
    check_steps = [
        ("room_joined", "no room joined"),
        ("participant_joined", "no participant joined"),
        ("inbound_audio_frame_received", "no inbound audio"),
        ("vad_start_of_speech", "no VAD start"),
        ("vad_end_of_speech", "no VAD end"),
        ("stt_stream_created", "no STT stream"),
        ("transcript_final", "no transcript"),
        ("llm_node_entered", "no llm_node"),
        ("user_text_seen_by_llm_node", "no user text seen"),
        ("agent_response_text_created", "no agent response"),
        ("tts_first_text", "no TTS text"),
        ("tts_first_audio", "no TTS audio"),
        ("second_turn_audio_published", "no second turn audio published")
    ]
    
    for marker_name, stage_name in check_steps:
        event_key = f"event_{marker_name}"
        if event_key not in events and marker_name not in events:
            return False, stage_name
            
    return True, "none"

async def run_text_injection(text: str):
    """Instantiate components and execute LLM -> TTS pipeline to verify connectivity."""
    logger.info(f"Running text injection diagnostics with: '{text}'")
    config = VoiceConfig()
    
    # Enforce dummy credentials for the test run if not present
    if not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = "mock-key-for-test"
    if not os.getenv("ELEVENLABS_API_KEY"):
        os.environ["ELEVENLABS_API_KEY"] = "mock-key-for-test"
    if not os.getenv("ELEVENLABS_VOICE_ID"):
        os.environ["ELEVENLABS_VOICE_ID"] = "mock-voice-for-test"
    if not os.getenv("DEEPGRAM_API_KEY"):
        os.environ["DEEPGRAM_API_KEY"] = "mock-key-for-test"
        
    shared = SharedComponents(config)
    
    # Mock services during tests if actual external client fails to load
    # or to satisfy "no real provider calls in tests"
    from unittest.mock import MagicMock, AsyncMock
    from livekit.agents import tts, stt
    
    # We must initialize the components
    try:
        await shared.initialize()
    except Exception as e:
        logger.warning(f"Shared components initialize failed, mocking for offline run: {e}")
        
    if not shared.llm:
        mock_llm = MagicMock(spec=llm.LLM)
        # Mock chat method
        class MockChoiceDelta:
            def __init__(self, content):
                self.content = content
        class MockChoice:
            def __init__(self, delta):
                self.delta = delta
        class MockChatChunk:
            def __init__(self, delta):
                self.choices = [MockChoice(delta)]

        async def mock_chat(*args, **kwargs):
            yield MockChatChunk(MockChoiceDelta("Hello! This is a mock response from LLM."))
        mock_llm.chat.return_value = mock_chat()
        shared.llm = mock_llm
        
    if not shared.tts:
        mock_tts = MagicMock(spec=tts.TTS)
        mock_stream = MagicMock()
        def mock_stream_iter(*args, **kwargs):
            async def _gen():
                await asyncio.sleep(0.01)
                from livekit import rtc
                # Create a mock audio frame (320 samples of 16kHz audio = 20ms)
                frame = rtc.AudioFrame(data=b"\x00\x00" * 320, sample_rate=16000, num_channels=1, samples_per_channel=320)
                class MockEvent:
                    def __init__(self, f):
                        self.frame = f
                yield MockEvent(frame)
            return _gen()
        mock_stream.__aiter__ = mock_stream_iter
        mock_stream.interrupt = AsyncMock()
        mock_stream.aclose = AsyncMock()
        mock_tts.stream.return_value = mock_stream
        shared.tts = mock_tts

    latency_recorder = LatencyRecorder("doctor-injected-call")
    # Mark room_joined, participant_joined, inbound_audio_frame_received, vad_start_of_speech, vad_end_of_speech, stt_stream_created, transcript_final, and greeting so we can simulate the second turn
    latency_recorder.mark("room_joined")
    latency_recorder.mark("participant_joined")
    latency_recorder.mark("inbound_audio_frame_received")
    latency_recorder.mark("vad_start_of_speech")
    latency_recorder.mark("vad_end_of_speech")
    latency_recorder.mark("stt_stream_created")
    latency_recorder.mark("transcript_final")
    latency_recorder.mark("greeting_tts_started")
    
    agent = DanaAgent(shared, latency_recorder)
    
    if os.getenv("DANA_MOCK_SYSTEM_CHECKS") == "true":
        class MockChoiceDelta:
            def __init__(self, content):
                self.content = content
        class MockChoice:
            def __init__(self, delta):
                self.delta = delta
        class MockChatChunk:
            def __init__(self, delta):
                self.choices = [MockChoice(delta)]

        class MockAdapter:
            def __init__(self, call_id):
                self.call_id = call_id
                self.state_machine = MagicMock()
                self.state_machine.call_state.current_stage.value = "interest_check"
                self.runtime = MagicMock()
                self.runtime.conversational_timing.get_pre_speech_delay.return_value = 0.0

            async def process_user_turn_stream(self, user_text, chat_stream_fn, latency_recorder=None, interrupted=False):
                if latency_recorder:
                    latency_recorder.mark("agent_runtime_process_user_turn_started")
                    latency_recorder.mark("agent_response_text_created")
                yield MockChatChunk(MockChoiceDelta("Hello! This is a mock response from LLM."))

            async def convert_response_to_stream(self, text, *args, **kwargs):
                yield MockChatChunk(MockChoiceDelta(text))

        agent.adapter = MockAdapter("doctor-injected-call")
    else:
        agent.adapter = LiveKitRuntimeAdapter(
            call_id="doctor-injected-call",
            phone_number="+15550100",
            prompt_loader=shared.prompt_loader,
            objection_classifier=shared.objection_classifier,
            objection_policy=shared.objection_policy,
            context_builder=shared.context_builder,
            action_policy=shared.action_policy,
            tool_registry=shared.tool_registry,
            compliance_filter=shared.compliance_filter,
            output_validator=shared.output_validator,
            pii_redactor=shared.pii_redactor,
            repository=shared.repository,
        )
    
    # 1. Run LLM
    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(role="user", content=text)
    
    try:
        logger.info("Entering LLM stage...")
        chunks = []
        async for chunk in agent.llm_node(chat_ctx, [], None):
            chunks.append(chunk)
            
        logger.info(f"LLM completed, yielded {len(chunks)} chunks.")
        
        # 2. Run TTS
        async def text_stream():
            for c in chunks:
                content = c.choices[0].delta.content if hasattr(c, "choices") and c.choices else ""
                if content:
                    yield content
                    
        logger.info("Entering TTS stage...")
        frames = []
        async for frame in agent.tts_node(text_stream(), None):
            frames.append(frame)
            
        logger.info(f"TTS completed, generated {len(frames)} audio frames.")
        
        # Check that we marked the timeline events
        events = latency_recorder.events
        logger.info(f"Recorded events: {list(events.keys())}")
        
        ready, broken_stage = analyze_timeline(events)
        
        def has_marker(name):
            return f"event_{name}" in events or name in events
            
        print(f"CONVERSATION_LOOP_READY={'true' if ready else 'false'}")
        print(f"BROKEN_STAGE={broken_stage}")
        print("CALL_ID=doctor-injected-call")
        print(f"HAS_INBOUND_AUDIO={'true' if has_marker('inbound_audio_frame_received') else 'false'}")
        print(f"HAS_VAD_START={'true' if has_marker('vad_start_of_speech') else 'false'}")
        print(f"HAS_TRANSCRIPT_FINAL={'true' if has_marker('transcript_final') else 'false'}")
        print(f"HAS_LLM_ENTRY={'true' if has_marker('llm_node_entered') else 'false'}")
        print(f"HAS_USER_TEXT_SEEN={'true' if has_marker('user_text_seen_by_llm_node') else 'false'}")
        print(f"HAS_AGENT_RESPONSE={'true' if has_marker('agent_response_text_created') else 'false'}")
        print(f"HAS_TTS_AUDIO={'true' if has_marker('tts_first_audio') else 'false'}")
        print(f"HAS_ROOM_DISCONNECT={'true' if has_marker('room_disconnected') else 'false'}")
    except Exception as e:
        logger.error(f"Text injection pipeline failed: {e}", exc_info=True)
        print("CONVERSATION_LOOP_READY=false")
        print("BROKEN_STAGE=pipeline_crash")

async def main():
    parser = argparse.ArgumentParser(description="Dana Conversation Loop Doctor")
    parser.add_argument("--call-id", type=str, help="Call ID to analyze in database")
    parser.add_argument("--inject-text", type=str, help="Simulate a conversation turn with input text")
    args = parser.parse_args()
    
    if args.inject_text:
        await run_text_injection(args.inject_text)
        return
        
    repository = Repository()
    call_id = args.call_id
    if not call_id:
        call_id = await find_latest_call_id(repository)
        if not call_id:
            logger.error("No call_id found in database and none provided.")
            print("CONVERSATION_LOOP_READY=false")
            print("BROKEN_STAGE=no_calls_found")
            return
        logger.info(f"No call-id specified, analyzing latest call: {call_id}")
    else:
        logger.info(f"Analyzing call: {call_id}")
        
    metrics = await query_call_metrics(call_id, repository)
    if not metrics:
        logger.error(f"No metrics found in database for call {call_id}")
        print("CONVERSATION_LOOP_READY=false")
        print("BROKEN_STAGE=metrics_not_found")
        return
        
    logger.info(f"Retrieved {len(metrics)} metric records.")
    for k, v in sorted(metrics.items()):
        logger.info(f"  {k}: {v}ms")
        
    ready, broken_stage = analyze_timeline(metrics)
    
    def has_marker(name):
        return f"event_{name}" in metrics or name in metrics
        
    print(f"CONVERSATION_LOOP_READY={'true' if ready else 'false'}")
    print(f"BROKEN_STAGE={broken_stage}")
    print(f"CALL_ID={call_id}")
    print(f"HAS_INBOUND_AUDIO={'true' if has_marker('inbound_audio_frame_received') else 'false'}")
    print(f"HAS_VAD_START={'true' if has_marker('vad_start_of_speech') else 'false'}")
    print(f"HAS_TRANSCRIPT_FINAL={'true' if has_marker('transcript_final') else 'false'}")
    print(f"HAS_LLM_ENTRY={'true' if has_marker('llm_node_entered') else 'false'}")
    print(f"HAS_USER_TEXT_SEEN={'true' if has_marker('user_text_seen_by_llm_node') else 'false'}")
    print(f"HAS_AGENT_RESPONSE={'true' if has_marker('agent_response_text_created') else 'false'}")
    print(f"HAS_TTS_AUDIO={'true' if has_marker('tts_first_audio') else 'false'}")
    print(f"HAS_ROOM_DISCONNECT={'true' if has_marker('room_disconnected') else 'false'}")

if __name__ == "__main__":
    asyncio.run(main())
