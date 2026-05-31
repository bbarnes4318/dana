#!/usr/bin/env python
import argparse
import asyncio
import json
import sys
from typing import Any

from ops.training_console import TrainingOperationsConsole


def print_result(res: Any) -> None:
    """Print the result as JSON to stdout."""
    if hasattr(res, "model_dump"):
        print(json.dumps(res.model_dump(mode="json"), indent=2))
    elif isinstance(res, dict):
        print(json.dumps(res, indent=2))
    else:
        print(json.dumps({"result": str(res)}, indent=2))


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Manage Telephony Providers, Campaigns, and Calls.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Provider Config Commands
    provider_parser = subparsers.add_parser("provider", help="Manage provider configurations")
    provider_sub = provider_parser.add_subparsers(dest="subcommand", required=True)

    prov_create = provider_sub.add_parser("create", help="Create a new provider config")
    prov_create.add_argument("--name", required=True, help="Config name")
    prov_create.add_argument("--telnyx-connection-id", help="Telnyx Connection ID")
    prov_create.add_argument("--telnyx-numbers", help="Comma-separated phone numbers")
    prov_create.add_argument("--livekit-url", help="LiveKit Server URL")
    prov_create.add_argument("--livekit-outbound-trunk", help="LiveKit Outbound Trunk ID")

    prov_list = provider_sub.add_parser("list", help="List provider configs")
    prov_list.add_argument("--limit", type=int, default=50, help="Max items to list")

    prov_show = provider_sub.add_parser("show", help="Show provider config details")
    prov_show.add_argument("--id", required=True, help="Provider config ID")

    # 2. Campaign Commands
    campaign_parser = subparsers.add_parser("campaign", help="Manage campaigns")
    campaign_sub = campaign_parser.add_subparsers(dest="subcommand", required=True)

    camp_create = campaign_sub.add_parser("create", help="Create a new outbound campaign")
    camp_create.add_argument("--name", required=True, help="Campaign Name")
    camp_create.add_argument("--caller-id", help="Caller ID number")
    camp_create.add_argument("--transfer-phone", help="Transfer destination phone number")
    camp_create.add_argument("--max-concurrent", type=int, default=1, help="Max concurrent calls")
    camp_create.add_argument("--daily-cap", type=int, default=100, help="Daily cap")
    camp_create.add_argument("--calling-start", default="09:30", help="Calling window start (HH:MM)")
    camp_create.add_argument("--calling-end", default="18:00", help="Calling window end (HH:MM)")
    camp_create.add_argument("--operator", default="system", help="Operator name")

    camp_list = campaign_sub.add_parser("list", help="List campaigns")
    camp_list.add_argument("--status", help="Filter by status")
    camp_list.add_argument("--limit", type=int, default=50, help="Limit")

    camp_show = campaign_sub.add_parser("show", help="Show campaign details")
    camp_show.add_argument("--campaign-id", required=True, help="Campaign ID")

    # Transitions
    for action in ["ready", "start", "pause", "resume", "stop", "complete"]:
        act_parser = campaign_sub.add_parser(action, help=f"Transition campaign to {action}")
        act_parser.add_argument("--campaign-id", required=True, help="Campaign ID")
        act_parser.add_argument("--operator", required=True, help="Operator name")
        act_parser.add_argument("--reason", help="Transition reason")

    camp_summary = campaign_sub.add_parser("summary", help="Get campaign summary statistics")
    camp_summary.add_argument("--campaign-id", required=True, help="Campaign ID")

    camp_update = campaign_sub.add_parser("update", help="Update campaign configuration")
    camp_update.add_argument("--campaign-id", required=True, help="Campaign ID")
    camp_update.add_argument("--max-concurrent", type=int, help="Max concurrent calls")
    camp_update.add_argument("--daily-cap", type=int, help="Daily cap")
    camp_update.add_argument("--operator", default="system", help="Operator name")

    # 3. Calls Commands
    calls_parser = subparsers.add_parser("calls", help="Manage calls")
    calls_sub = calls_parser.add_subparsers(dest="subcommand", required=True)

    calls_live = calls_sub.add_parser("live", help="List live call sessions")
    calls_live.add_argument("--campaign-id", help="Filter by campaign ID")
    calls_live.add_argument("--limit", type=int, default=100, help="Limit")

    calls_attempts = calls_sub.add_parser("attempts", help="List call attempts")
    calls_attempts.add_argument("--campaign-id", help="Filter by campaign ID")
    calls_attempts.add_argument("--limit", type=int, default=100, help="Limit")

    calls_end = calls_sub.add_parser("end", help="End an active call session")
    calls_end.add_argument("--session-id", required=True, help="Live call session ID")
    calls_end.add_argument("--operator", required=True, help="Operator name")
    calls_end.add_argument("--reason", default="Ended via CLI", help="Reason for ending")

    calls_outcome = calls_sub.add_parser("outcome", help="Mark call attempt outcome")
    calls_outcome.add_argument("--attempt-id", required=True, help="Call attempt ID")
    calls_outcome.add_argument("--outcome", required=True, help="Outcome status")
    calls_outcome.add_argument("--operator", required=True, help="Operator name")

    calls_export = calls_sub.add_parser("export-training", help="Export call attempt to training")
    calls_export.add_argument("--attempt-id", required=True, help="Call attempt ID")
    calls_export.add_argument("--operator", required=True, help="Operator name")

    args = parser.parse_args()
    console = TrainingOperationsConsole()

    try:
        if args.command == "provider":
            if args.subcommand == "create":
                telnyx_phone_numbers = [n.strip() for n in args.telnyx_numbers.split(",")] if args.telnyx_numbers else []
                res = await console.create_telephony_provider_config(
                    name=args.name,
                    telnyx_connection_id=args.telnyx_connection_id,
                    telnyx_phone_numbers=telnyx_phone_numbers,
                    livekit_url=args.livekit_url,
                    livekit_sip_outbound_trunk_id=args.livekit_outbound_trunk,
                )
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "list":
                res = await console.list_telephony_provider_configs(limit=args.limit)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "show":
                res = await console.show_telephony_provider_config(args.id)
                print_result(res)
                return 0 if res.success else 1

        elif args.command == "campaign":
            if args.subcommand == "create":
                res = await console.create_telephony_campaign(
                    name=args.name,
                    caller_id=args.caller_id,
                    transfer_phone_number=args.transfer_phone,
                    max_concurrent_calls=args.max_concurrent,
                    daily_call_cap=args.daily_cap,
                    calling_window_start=args.calling_start,
                    calling_window_end=args.calling_end,
                    operator=args.operator,
                )
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "list":
                res = await console.list_telephony_campaigns(status=args.status, limit=args.limit)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "show":
                res = await console.show_telephony_campaign(args.campaign_id)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "ready":
                res = await console.mark_campaign_ready(args.campaign_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "start":
                res = await console.start_telephony_campaign(args.campaign_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "pause":
                res = await console.pause_telephony_campaign(args.campaign_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "resume":
                res = await console.resume_telephony_campaign(args.campaign_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "stop":
                res = await console.stop_telephony_campaign(args.campaign_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "complete":
                res = await console.complete_telephony_campaign(args.campaign_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "summary":
                res = await console.get_telephony_campaign_summary(args.campaign_id)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "update":
                updates = {}
                if args.max_concurrent is not None:
                    updates["max_concurrent_calls"] = args.max_concurrent
                if args.daily_cap is not None:
                    updates["daily_call_cap"] = args.daily_cap
                
                # Import campaign service to run update
                from telephony.campaign_service import TelephonyCampaignService
                service = TelephonyCampaignService(repository=console.repository)
                res = await service.update_campaign(args.campaign_id, updates, operator=args.operator)
                print_result(res)
                return 0 if res.success else 1

        elif args.command == "calls":
            if args.subcommand == "live":
                res = await console.list_live_telephony_calls(campaign_id=args.campaign_id, limit=args.limit)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "attempts":
                res = await console.list_call_attempts(campaign_id=args.campaign_id, limit=args.limit)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "end":
                res = await console.end_live_call(args.session_id, args.operator, args.reason)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "outcome":
                res = await console.mark_call_outcome(args.attempt_id, args.outcome, args.operator)
                print_result(res)
                return 0 if res.success else 1

            elif args.subcommand == "export-training":
                res = await console.export_call_attempt_to_training(args.attempt_id, args.operator)
                print_result(res)
                return 0 if res.success else 1

    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        print(json.dumps({"success": False, "error": str(e)}))
        return 1

    return 0


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
