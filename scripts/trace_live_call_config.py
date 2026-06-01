#!/usr/bin/env python3
"""
Trace and retrieve configuration/trunk details used for a previously successful live call.
"""

import os
import sys
import json
import argparse
import asyncio
from pathlib import Path

# Ensure parent directory is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
load_environment()

from storage.repository import Repository

async def get_repository():
    """Initialize Repository with fallback to local JSONL if Postgres is unreachable."""
    try:
        repo = Repository()
        # Test connection quickly
        await repo.list_recent_call_attempts(limit=1)
        return repo
    except Exception as e:
        sys.stderr.write(f"Postgres connection failed ({e}). Falling back to local JSONL storage.\n")
        # Temporarily strip DATABASE_URL to force JsonlStore initialization
        db_url = os.environ.pop("DATABASE_URL", None)
        try:
            repo = Repository()
            return repo
        finally:
            if db_url:
                os.environ["DATABASE_URL"] = db_url

async def main():
    parser = argparse.ArgumentParser(description="Trace the telephony provider configuration of a previous call attempt.")
    parser.add_argument("--call-attempt-id", help="ID of the call attempt to trace")
    parser.add_argument("--room-name", help="LiveKit room name of the call to trace")
    parser.add_argument("--sip-call-id", help="LiveKit SIP call ID of the call to trace")
    args = parser.parse_args()

    if not any([args.call_attempt_id, args.room_name, args.sip_call_id]):
        parser.print_help()
        sys.exit(1)

    repo = await get_repository()

    attempt = None
    if args.call_attempt_id:
        attempt = await repo.get_call_attempt(args.call_attempt_id)
    elif args.room_name:
        results = await repo.query_call_attempts({"livekit_room_name": args.room_name})
        if results:
            attempt = results[0]
    elif args.sip_call_id:
        results = await repo.query_call_attempts({"livekit_sip_call_id": args.sip_call_id})
        if results:
            attempt = results[0]

    if not attempt:
        print(json.dumps({
            "success": false,
            "error": "Call attempt not found.",
            "search_params": {
                "call_attempt_id": args.call_attempt_id,
                "room_name": args.room_name,
                "sip_call_id": args.sip_call_id
            }
        }, indent=2))
        sys.exit(1)

    # Resolve trunk / config details
    provider_config_id = attempt.get("provider_config_id")
    provider_config = None
    if provider_config_id:
        provider_config = await repo.get_telephony_provider_config(provider_config_id)

    # Trace metadata
    metadata = attempt.get("metadata") or {}
    
    # Try to extract trunk ID and caller ID from attempt or provider config
    caller_id = attempt.get("phone_number_redacted") or metadata.get("outbound_caller_id") or metadata.get("caller_id")
    trunk_id = metadata.get("livekit_sip_outbound_trunk_id") or metadata.get("trunk_id")
    
    if provider_config:
        caller_id = caller_id or provider_config.get("default_caller_id")
        trunk_id = trunk_id or provider_config.get("livekit_sip_outbound_trunk_id")

    # Scrub credentials
    scrubbed_config = {}
    if provider_config:
        for k, v in provider_config.items():
            if any(secret in k.lower() for secret in ["secret", "key", "token", "password"]):
                scrubbed_config[k] = v[:3] + "..." + v[-3:] if v and len(v) > 6 else "***"
            else:
                scrubbed_config[k] = v

    result = {
        "success": True,
        "attempt": {
            "id": attempt.get("id"),
            "campaign_id": attempt.get("campaign_id"),
            "lead_id": attempt.get("lead_id"),
            "provider_config_id": provider_config_id,
            "status": attempt.get("status"),
            "livekit_room_name": attempt.get("livekit_room_name"),
            "livekit_participant_id": attempt.get("livekit_participant_id"),
            "livekit_sip_call_id": attempt.get("livekit_sip_call_id"),
            "provider_call_id": attempt.get("provider_call_id"),
            "duration_seconds": attempt.get("duration_seconds"),
            "outcome": attempt.get("outcome"),
            "started_at": attempt.get("started_at"),
            "answered_at": attempt.get("answered_at"),
            "ended_at": attempt.get("ended_at"),
        },
        "resolved_trunk_id": trunk_id,
        "resolved_caller_id": caller_id,
        "provider_config": scrubbed_config or None,
        "attempt_metadata": metadata
    }

    print(json.dumps(result, indent=2))
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
