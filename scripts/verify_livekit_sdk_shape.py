#!/usr/bin/env python3
"""
Verification Script for LiveKit SDK Shape
Attempts to import the real installed LiveKit SDK packages (without conftest mocks)
and verifies their exact structure, classes, and descriptor fields.
"""

import sys
import importlib.metadata

def main():
    print("=====================================================================")
    print("Verifying LiveKit SDK Shape (Real Installed Packages)")
    print("=====================================================================")

    failed = False

    def print_check(name: str, passed: bool, info: str = ""):
        nonlocal failed
        status = "[ PASS ]" if passed else "[ FAIL ]"
        print(f"{status} {name:<45} {info}")
        if not passed:
            failed = True

    # 1. Package Versions
    packages = ["livekit", "livekit-agents", "livekit-api"]
    for pkg in packages:
        try:
            version = importlib.metadata.version(pkg)
            print_check(f"Package '{pkg}' installed", True, f"version: {version}")
        except importlib.metadata.PackageNotFoundError:
            print_check(f"Package '{pkg}' installed", False, "Not found via importlib.metadata")
            
    # 2. Try importing livekit modules
    try:
        import livekit
        print_check("Import livekit", True)
    except ImportError as e:
        print_check("Import livekit", False, str(e))
        print("\nERROR: Cannot import 'livekit' package. Please install requirements first.")
        sys.exit(1)

    try:
        from livekit import agents
        print_check("Import livekit.agents", True)
    except ImportError as e:
        print_check("Import livekit.agents", False, str(e))

    try:
        from livekit import api
        print_check("Import livekit.api", True)
    except ImportError as e:
        print_check("Import livekit.api", False, str(e))

    # 3. Check function_tool decorator
    try:
        from livekit.agents import function_tool
        print_check("function_tool decorator exists", True)
    except ImportError as e:
        print_check("function_tool decorator exists", False, str(e))

    # 4. Check Agent class
    try:
        from livekit.agents import Agent
        print_check("Agent class exists", True)
    except ImportError as e:
        print_check("Agent class exists", False, str(e))

    # 5. Check CreateSIPParticipantRequest
    has_request = False
    req_class = None
    try:
        from livekit.api import CreateSIPParticipantRequest
        print_check("CreateSIPParticipantRequest exists", True)
        has_request = True
        req_class = CreateSIPParticipantRequest
    except ImportError as e:
        print_check("CreateSIPParticipantRequest exists", False, str(e))

    # 6. Introspect CreateSIPParticipantRequest fields
    if has_request and req_class is not None:
        try:
            has_descriptor = hasattr(req_class, "DESCRIPTOR")
            print_check("CreateSIPParticipantRequest has DESCRIPTOR", has_descriptor)
            
            if has_descriptor:
                has_fields = hasattr(req_class.DESCRIPTOR, "fields_by_name")
                print_check("CreateSIPParticipantRequest.DESCRIPTOR has fields_by_name", has_fields)
                
                if has_fields:
                    fields = list(req_class.DESCRIPTOR.fields_by_name.keys())
                    
                    # Check presence of specific required fields
                    for f in ["sip_trunk_id", "sip_call_to", "room_name", "participant_identity", "participant_metadata"]:
                        present = f in fields
                        print_check(f"  Field '{f}' present", present)
                else:
                    failed = True
            else:
                failed = True
        except Exception as e:
            print_check("Introspect CreateSIPParticipantRequest descriptor", False, str(e))
    else:
        print_check("Introspect CreateSIPParticipantRequest descriptor", False, "Skipped (class not found)")

    # 7. Check LiveKitAPI.sip methods
    try:
        from livekit.api import LiveKitAPI
        lkapi = LiveKitAPI("http://localhost:7880", "devkey", "secret")
        
        has_sip = hasattr(lkapi, "sip")
        print_check("LiveKitAPI.sip client exists", has_sip)
        
        if has_sip:
            has_create_participant = hasattr(lkapi.sip, "create_sip_participant")
            print_check("create_sip_participant exists on sip client", has_create_participant)
            
            has_create_trunk = hasattr(lkapi.sip, "create_sip_outbound_trunk")
            print_check("create_sip_outbound_trunk exists on sip client", has_create_trunk)
        else:
            print_check("create_sip_participant exists", False, "Sip client missing")
            print_check("create_sip_outbound_trunk exists", False, "Sip client missing")
            
    except Exception as e:
        print_check("Verify LiveKitAPI.sip methods", False, str(e))

    print("=====================================================================")
    if failed:
        print("VERIFICATION FAILED: One or more required checks failed.")
        sys.exit(1)
    else:
        print("VERIFICATION SUCCESS: All checks passed.")
        sys.exit(0)

if __name__ == "__main__":
    main()
