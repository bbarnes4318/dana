#!/usr/bin/env python3
import os
import sys
import uuid
import argparse
import asyncio
import logging
from pathlib import Path

# Setup sys.path to root directory
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from config.runtime_env import get_runtime_env
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("live_phone_audio_diagnostic")

async def monitor_output(stream, collected_markers, raw_logs):
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode('utf-8', errors='ignore').strip()
        raw_logs.append(decoded)
        # Check for markers
        for marker in [
            "CALL_PARTICIPANT_JOINED",
            "TRACK_SUBSCRIBED_AUDIO",
            "DIAG_GREETING_SAY_CALLED",
            "TTS_FIRST_AUDIO_SENT",
            "AGENT_SPEAKING_STARTED",
            "DIAG_SESSION_START_SUCCEEDED",
            "DIAG_GREETING_SAY_COMPLETED",
            "TTS_STREAM_COMPLETED",
            "AGENT_SESSION_STARTED",
            "ROOM_AUDIO_OUTPUT_ENABLED",
            "ROOM_AUDIO_INPUT_ENABLED",
            "AGENT_SPEAKING_STOPPED"
        ]:
            if marker in decoded:
                collected_markers.add(marker)
                logger.info(f"[MARKER DETECTED] {marker}")
        # Print raw log if it contains errors or critical info
        if "ERROR" in decoded or "FATAL" in decoded or "exception" in decoded.lower():
            logger.error(f"[WORKER LOG] {decoded}")

async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Dana Live Call Audio Diagnostic Tool")
    parser.add_argument("--to", required=True, help="Destination phone number in E.164 format")
    parser.add_argument("--from", dest="from_num", required=True, help="Outbound caller ID phone number in E.164 format")
    args = parser.parse_args()

    # 1. Kill any existing agent worker processes to avoid routing conflicts
    logger.info("Cleaning up any existing main.py or worker processes...")
    try:
        os.system("pkill -9 -f 'main.py dev'")
        os.system("pkill -9 -f 'main.py'")
        await asyncio.sleep(2.0)
    except Exception as e:
        logger.warning(f"Failed to kill existing workers: {e}")

    # 2. Start worker subprocess with diagnostic greeting enabled
    logger.info("Starting diagnostic agent worker in background...")
    env = os.environ.copy()
    env["DANA_FORCE_DIAGNOSTIC_GREETING"] = "true"
    env["DANA_DIAGNOSTIC_GREETING_TEXT"] = "Hello, can you hear me?"
    env["DANA_CONTROLLED_LIVE_TEST"] = "true"
    
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "main.py", "dev",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(root_dir),
        env=env
    )

    collected_markers = set()
    raw_logs = []
    
    # Start stdout/stderr monitoring tasks
    stdout_task = asyncio.create_task(monitor_output(proc.stdout, collected_markers, raw_logs))
    stderr_task = asyncio.create_task(monitor_output(proc.stderr, collected_markers, raw_logs))

    # Wait 8 seconds for worker to boot and register
    logger.info("Waiting for agent worker to initialize and prewarm components...")
    await asyncio.sleep(8.0)

    # 3. Place outbound live SIP call
    logger.info("Placing outbound LiveKit SIP call...")
    telephony_env = get_runtime_env()
    outbound_trunk_id = telephony_env.get("livekit_sip_outbound_trunk_id") or os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
    if not outbound_trunk_id:
        logger.error("Error: LIVEKIT_SIP_OUTBOUND_TRUNK_ID is not configured.")
        proc.terminate()
        return 1

    adapter = LiveKitOutboundAdapter()
    room_name = f"dana-smoke-test-{uuid.uuid4().hex[:8]}"
    part_identity = f"smoke-test-participant-{uuid.uuid4().hex[:8]}"

    dial_config = LiveKitDialConfig(
        live_mode=True,
        livekit_url=telephony_env.get("livekit_url"),
        api_key=telephony_env.get("livekit_api_key"),
        api_secret=telephony_env.get("livekit_api_secret"),
        outbound_trunk_id=outbound_trunk_id,
        room_name=room_name,
        phone_number=args.to.strip(),
        caller_id=args.from_num.strip(),
        participant_identity=part_identity,
        wait_until_answered=True,
        krisp_enabled=True,
    )

    try:
        dial_res = await adapter.dial(dial_config)
        if not dial_res.success:
            logger.error(f"Failed to place SIP call: {dial_res.message}")
            proc.terminate()
            return 1
        logger.info(f"SIP call placed successfully. Call ID: {dial_res.livekit_sip_call_id}")
    except Exception as e:
        logger.error(f"Dialer threw exception: {e}")
        proc.terminate()
        return 1

    # 4. Monitor room events and markers for up to 30 seconds
    logger.info("Monitoring call and audio events for 30 seconds...")
    try:
        await asyncio.wait_for(asyncio.gather(stdout_task, stderr_task), timeout=30.0)
    except asyncio.TimeoutError:
        logger.info("Monitoring timeout reached.")

    # Terminate worker process
    logger.info("Terminating diagnostic worker...")
    try:
        proc.terminate()
        await proc.wait()
    except Exception:
        pass

    # 5. Verify markers and produce diagnostic report
    required_markers = [
        "CALL_PARTICIPANT_JOINED",
        "TRACK_SUBSCRIBED_AUDIO",
        "DIAG_GREETING_SAY_CALLED",
        "TTS_FIRST_AUDIO_SENT",
        "AGENT_SPEAKING_STARTED"
    ]

    missing_markers = [m for m in required_markers if m not in collected_markers]

    logger.info("==============================================")
    logger.info("       LIVE CALL DIAGNOSTIC RESULTS           ")
    logger.info("==============================================")
    for m in required_markers:
        status = "PASSED" if m in collected_markers else "FAILED"
        logger.info(f"  {m}: {status}")

    if missing_markers:
        logger.error(f"DIAGNOSTIC TEST FAILED: Missing checkpoints: {missing_markers}")
        # Output some trailing log lines to help debug
        logger.error("Showing last 30 log lines of worker for context:")
        for log in raw_logs[-30:]:
            print(f"  [worker] {log}")
        return 1

    logger.info("CONVERSATION_LOOP_READY=true - Diagnostic greeting & audio delivery verified successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
