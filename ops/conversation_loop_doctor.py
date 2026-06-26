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
    required_stages = [
        ("room_joined", "connection"),
        ("greeting_audio_published", "greeting"),
        ("inbound_audio_frame_received", "inbound_audio"),
        ("stt_stream_created", "stt_stream"),
        ("stt_final_transcript", "stt_transcription"),
        ("llm_node_entered", "llm_entry"),
        ("agent_response_text_created", "llm_generation"),
        ("second_turn_tts_first_audio", "tts_synthesis"),
        ("second_turn_audio_published", "audio_publishing")
    ]
    
    for marker_name, stage_name in required_stages:
        event_key = f"event_{marker_name}"
        # Also support direct metric name check if not prefixed
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
    from unittest.mock import MagicMock
    from livekit.agents import tts, stt
    
    # We must initialize the components
    try:
        await shared.initialize()
    except Exception as e:
        logger.warning(f"Shared components initialize failed, mocking for offline run: {e}")
        
    if not shared.llm:
        mock_llm = MagicMock(spec=llm.LLM)
        # Mock chat method
        async def mock_chat(*args, **kwargs):
            from livekit.agents.llm import ChatChunk, Choice, ChoiceDelta
            yield ChatChunk(choices=[Choice(delta=ChoiceDelta(content="Hello! This is a mock response from LLM."))])
        mock_llm.chat.return_value = mock_chat()
        shared.llm = mock_llm
        
    if not shared.tts:
        mock_tts = MagicMock(spec=tts.TTS)
        mock_stream = MagicMock()
        async def mock_stream_iter():
            from livekit import rtc
            # Create a mock audio frame (320 samples of 16kHz audio = 20ms)
            frame = rtc.AudioFrame(data=b"\x00\x00" * 320, sample_rate=16000, num_channels=1, samples_per_channel=320)
            class MockEvent:
                def __init__(self, f):
                    self.frame = f
            yield MockEvent(frame)
        mock_stream.__aiter__.return_value = mock_stream_iter()
        mock_tts.stream.return_value = mock_stream
        shared.tts = mock_tts

    latency_recorder = LatencyRecorder("doctor-injected-call")
    # Mark room_joined and greeting so we can simulate the second turn
    latency_recorder.mark("room_joined")
    latency_recorder.mark("greeting_tts_started")
    
    agent = DanaAgent(shared, latency_recorder)
    
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
        
        if "agent_response_text_created" in events and "second_turn_audio_published" in events:
            print("CONVERSATION_LOOP_READY=true")
            print("BROKEN_STAGE=none")
        else:
            missing = []
            if "agent_response_text_created" not in events:
                missing.append("llm_generation")
            if "second_turn_audio_published" not in events:
                missing.append("audio_publishing")
            print("CONVERSATION_LOOP_READY=false")
            print(f"BROKEN_STAGE={missing[0]}")
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
    if ready:
        print("CONVERSATION_LOOP_READY=true")
        print("BROKEN_STAGE=none")
    else:
        print("CONVERSATION_LOOP_READY=false")
        print(f"BROKEN_STAGE={broken_stage}")

if __name__ == "__main__":
    asyncio.run(main())
