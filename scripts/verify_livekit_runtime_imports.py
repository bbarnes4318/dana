#!/usr/bin/env python3
"""
Verification Script for LiveKit Runtime Imports
Attempts to import DanaAgent and required LiveKit classes
without the conftest mock harness.
"""

import sys
import os

# Ensure standard import capability
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("=====================================================================")
    print("Verifying LiveKit Runtime Imports (No Mocks)")
    print("=====================================================================")
    
    try:
        print("Importing function_tool, Agent, RunContext from livekit.agents...")
        from livekit.agents import function_tool, Agent, RunContext
        print("Import succeeded.")
        
        print("Importing api from livekit...")
        from livekit import api
        print("Import succeeded.")
        
        print("Importing DanaAgent, SharedComponents from main...")
        from main import DanaAgent, SharedComponents
        print("Import succeeded.")
        
        print("=====================================================================")
        print("VERIFICATION SUCCESS: All runtime imports succeeded.")
        sys.exit(0)
    except Exception as e:
        print("\n=====================================================================")
        print(f"VERIFICATION FAILED: Import failed: {e}")
        print("=====================================================================")
        sys.exit(1)

if __name__ == "__main__":
    main()
