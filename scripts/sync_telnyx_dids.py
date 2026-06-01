import os
import sys
import argparse
import asyncio
import json
import logging
from pathlib import Path

# Add repo root to path for imports
sys.path.append(str(Path(__file__).resolve().parent.parent))

from telephony.telnyx_inventory import TelnyxInventoryConfig, TelnyxDIDInventorySyncService

# Setup logger to output to stderr so stdout stays clean for JSON
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("sync_telnyx_dids_cli")


async def main():
    parser = argparse.ArgumentParser(
        description="Sync owned Telnyx phone numbers into Dana's DID pool."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the sync process without writing to the database."
    )
    parser.add_argument(
        "--daily-cap",
        type=int,
        default=100,
        help="Default daily call cap for imported DIDs."
    )
    parser.add_argument(
        "--hourly-cap",
        type=int,
        default=20,
        help="Default hourly call cap for imported DIDs."
    )
    parser.add_argument(
        "--sync-status",
        type=str,
        default="active",
        choices=["active", "paused"],
        help="Status for newly imported numbers."
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Optional Telnyx API Key override. (Do not leak in shared logs)."
    )

    args = parser.parse_args()

    config = TelnyxInventoryConfig(
        api_key=args.api_key,
        dry_run=args.dry_run,
        default_daily_cap=args.daily_cap,
        default_hourly_cap=args.hourly_cap,
        sync_status=args.sync_status
    )

    logger.info("Initializing Telnyx DID Sync Service...")
    service = TelnyxDIDInventorySyncService()

    logger.info("Starting sync operation (dry_run=%s)...", config.dry_run)
    result = await service.sync(config)

    # Output result as clean JSON on stdout
    print(json.dumps(result.model_dump(), indent=2, default=str))

    if result.success:
        logger.info("Sync completed successfully.")
        sys.exit(0)
    else:
        logger.error("Sync failed with errors: %s", ", ".join(result.errors))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
