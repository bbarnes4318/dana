#!/usr/bin/env python
import argparse
import asyncio
import json
import sys
from typing import Any

from ops.training_console import TrainingOperationsConsole


def print_result(res: Any) -> None:
    if hasattr(res, "model_dump"):
        print(json.dumps(res.model_dump(mode="json"), indent=2))
    elif isinstance(res, dict):
        print(json.dumps(res, indent=2))
    else:
        print(json.dumps({"result": str(res)}, indent=2))


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Import leads from a CSV or JSON file into a campaign.")
    parser.add_argument("--campaign-id", required=True, help="Target outbound campaign ID")
    parser.add_argument("--file", required=True, help="Path to the leads CSV or JSON file")

    args = parser.parse_args()
    console = TrainingOperationsConsole()

    try:
        res = await console.import_campaign_leads(args.campaign_id, args.file)
        print_result(res)
        return 0 if res.success else 1
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        print(json.dumps({"success": False, "error": str(e)}))
        return 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
