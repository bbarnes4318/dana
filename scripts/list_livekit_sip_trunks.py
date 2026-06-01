#!/usr/bin/env python3
"""
List existing LiveKit SIP Outbound Trunks (read-only).
"""

import asyncio
import os
import sys
import json
from pathlib import Path

# Ensure parent directory is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_loader import load_environment
load_environment()

from config.runtime_env import get_runtime_env
from livekit import api

def mask_trunk_id(trunk_id: str) -> str:
    if not trunk_id:
        return ""
    if len(trunk_id) <= 8:
        return "***"
    return trunk_id[:6] + "..." + trunk_id[-4:]

async def main():
    env = get_runtime_env()
    
    # Initialize LiveKitAPI
    lkapi = api.LiveKitAPI(env["livekit_url"], env["livekit_api_key"], env["livekit_api_secret"])
    
    try:
        # Check if list_sip_outbound_trunk is available
        if not hasattr(lkapi, "sip") or not hasattr(lkapi.sip, "list_sip_outbound_trunk"):
            print(json.dumps({
                "success": False,
                "error": "LiveKit Python SDK does not support SIP administration or list_sip_outbound_trunk method."
            }, indent=2))
            return

        request = api.ListSIPOutboundTrunkRequest()
        res = await lkapi.sip.list_sip_outbound_trunk(request)
        
        items = getattr(res, "items", [])
        trunks_list = []
        
        for item in items:
            address = item.address
            name = item.name
            trunk_id = item.sip_trunk_id
            
            # Parse numbers
            numbers = list(getattr(item, "numbers", []))
            
            # Guess provider
            provider_guess = "unknown"
            if address:
                addr_lower = address.lower()
                if "telnyx" in addr_lower:
                    provider_guess = "telnyx"
                elif "signalwire" in addr_lower:
                    provider_guess = "signalwire"
                elif "twilio" in addr_lower:
                    provider_guess = "twilio"
                elif "bulkvs" in addr_lower:
                    provider_guess = "bulkvs"

            matches_telnyx = (provider_guess == "telnyx" or (address and "sip.telnyx.com" in address.lower()))
            
            trunks_list.append({
                "trunk_id_raw_for_script": trunk_id, # not printed to stdout when we filter it below
                "trunk_id_masked": mask_trunk_id(trunk_id),
                "trunk_name": name,
                "address": address,
                "numbers": numbers,
                "provider_guess": provider_guess,
                "matches_telnyx": matches_telnyx
            })

        # Return a scrubbed output to stdout
        scrubbed = []
        for t in trunks_list:
            item_copy = t.copy()
            # Remove raw ID from printed output
            item_copy.pop("trunk_id_raw_for_script", None)
            scrubbed.append(item_copy)

        print(json.dumps({"trunks": scrubbed}, indent=2))
        
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
    finally:
        # ClientSession aclose for clean exit
        if hasattr(lkapi, "aclose"):
            await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(main())
