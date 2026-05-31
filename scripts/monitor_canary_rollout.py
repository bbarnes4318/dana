#!/usr/bin/env python3
"""Dana Canary Monitoring and Control CLI Script.

Provides commands to monitor running canary experiments, perform rollback check gating,
assess promotion readiness, and write detailed reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from storage.repository import Repository
from deployment.monitoring import (
    CanaryMonitor,
    CanaryMonitorConfig,
    CanaryVariantMetrics,
    CanarySafetySignal,
    CanaryMonitoringResult,
)


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Dana Canary Monitoring CLI")
    parser.add_argument("command", choices=["monitor", "rollback-check", "readiness", "report"])
    parser.add_argument("--experiment-id", required=True, help="Deployment Experiment ID")
    parser.add_argument("--from", dest="window_start", help="Start window YYYY-MM-DD")
    parser.add_argument("--to", dest="window_end", help="End window YYYY-MM-DD")
    parser.add_argument("--auto-rollback", action="store_true", help="Automatically trigger rollback if safety signals fail")
    parser.add_argument("--min-candidate-calls", type=int, default=25)
    parser.add_argument("--min-control-calls", type=int, default=25)
    parser.add_argument("--output-dir", default="data/canary")
    parser.add_argument("--json-only", action="store_true")

    args = parser.parse_args()

    repo = Repository()
    monitor = CanaryMonitor(repository=repo)

    config = CanaryMonitorConfig(
        experiment_id=args.experiment_id,
        window_start=args.window_start,
        window_end=args.window_end,
        min_candidate_calls=args.min_candidate_calls,
        min_control_calls=args.min_control_calls,
        auto_rollback=args.auto_rollback,
        output_dir=args.output_dir
    )

    try:
        if args.command == "monitor":
            result = await monitor.monitor_experiment(config)
            sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
            sys.exit(0)

        elif args.command == "rollback-check":
            experiment = await monitor.load_experiment(config.experiment_id)
            data_bundle = await monitor.gather_canary_data(experiment, config)
            split_data = monitor.split_by_variant(experiment, data_bundle)
            control_metrics = monitor.compute_variant_metrics("control", split_data["control"])
            candidate_metrics = monitor.compute_variant_metrics("candidate", split_data["candidate"])
            signals = monitor.detect_safety_signals(control_metrics, candidate_metrics, config)

            rollback_required = any(s.rollback_required for s in signals)
            res_dict = {
                "experiment_id": config.experiment_id,
                "rollback_required": rollback_required,
                "signals": [s.to_dict() for s in signals if s.rollback_required]
            }

            if rollback_required:
                sys.stderr.write(json.dumps(res_dict, indent=2) + "\n")
                sys.exit(1)
            else:
                sys.stdout.write(json.dumps(res_dict, indent=2) + "\n")
                sys.exit(0)

        elif args.command == "readiness":
            experiment = await monitor.load_experiment(config.experiment_id)
            data_bundle = await monitor.gather_canary_data(experiment, config)
            split_data = monitor.split_by_variant(experiment, data_bundle)
            control_metrics = monitor.compute_variant_metrics("control", split_data["control"])
            candidate_metrics = monitor.compute_variant_metrics("candidate", split_data["candidate"])
            signals = monitor.detect_safety_signals(control_metrics, candidate_metrics, config)
            readiness = await monitor.check_promotion_readiness(
                experiment, control_metrics, candidate_metrics, signals, config
            )

            if readiness.ready:
                sys.stdout.write(json.dumps(readiness.to_dict(), indent=2) + "\n")
                sys.exit(0)
            else:
                sys.stderr.write(json.dumps(readiness.to_dict(), indent=2) + "\n")
                sys.exit(1)

        elif args.command == "report":
            # Load experiment to check for latest result
            experiment = await monitor.load_experiment(config.experiment_id)
            metrics = experiment.get("metrics") or {}
            latest_result = metrics.get("latest_monitoring_result")

            if latest_result:
                # Reconstruct CanaryMonitoringResult from stored metrics dict
                c_met = latest_result["control_metrics"]
                control_metrics = CanaryVariantMetrics(
                    variant=c_met.get("variant", "control"),
                    total_calls=c_met.get("total_calls", 0),
                    total_turns=c_met.get("total_turns", 0),
                    qa_reports=c_met.get("qa_reports", 0),
                    tool_events=c_met.get("tool_events", 0),
                    transfers=c_met.get("transfers", 0),
                    successful_transfers=c_met.get("successful_transfers", 0),
                    failed_transfers=c_met.get("failed_transfers", 0),
                    callbacks=c_met.get("callbacks", 0),
                    dnc_requests=c_met.get("dnc_requests", 0),
                    wrong_number_requests=c_met.get("wrong_number_requests", 0),
                    hangups=c_met.get("hangups", 0),
                    average_qa_score=c_met.get("average_qa_score"),
                    average_compliance_score=c_met.get("average_compliance_score"),
                    transfer_rate=c_met.get("transfer_rate"),
                    successful_transfer_rate=c_met.get("successful_transfer_rate"),
                    failed_transfer_rate=c_met.get("failed_transfer_rate"),
                    callback_rate=c_met.get("callback_rate"),
                    hangup_rate=c_met.get("hangup_rate"),
                    compliance_failure_count=c_met.get("compliance_failure_count", 0),
                    critical_failure_count=c_met.get("critical_failure_count", 0),
                    high_failure_count=c_met.get("high_failure_count", 0),
                    medium_failure_count=c_met.get("medium_failure_count", 0),
                    low_failure_count=c_met.get("low_failure_count", 0),
                    transfer_before_consent_count=c_met.get("transfer_before_consent_count", 0),
                    dnc_failure_count=c_met.get("dnc_failure_count", 0),
                    wrong_number_failure_count=c_met.get("wrong_number_failure_count", 0),
                    price_quote_count=c_met.get("price_quote_count", 0),
                    licensed_claim_count=c_met.get("licensed_claim_count", 0),
                    human_claim_count=c_met.get("human_claim_count", 0),
                    you_qualify_count=c_met.get("you_qualify_count", 0),
                    approval_claim_count=c_met.get("approval_claim_count", 0),
                    sensitive_data_request_count=c_met.get("sensitive_data_request_count", 0),
                    tool_failure_count=c_met.get("tool_failure_count", 0),
                    labels=c_met.get("labels"),
                )

                cand_met = latest_result["candidate_metrics"]
                candidate_metrics = CanaryVariantMetrics(
                    variant=cand_met.get("variant", "candidate"),
                    total_calls=cand_met.get("total_calls", 0),
                    total_turns=cand_met.get("total_turns", 0),
                    qa_reports=cand_met.get("qa_reports", 0),
                    tool_events=cand_met.get("tool_events", 0),
                    transfers=cand_met.get("transfers", 0),
                    successful_transfers=cand_met.get("successful_transfers", 0),
                    failed_transfers=cand_met.get("failed_transfers", 0),
                    callbacks=cand_met.get("callbacks", 0),
                    dnc_requests=cand_met.get("dnc_requests", 0),
                    wrong_number_requests=cand_met.get("wrong_number_requests", 0),
                    hangups=cand_met.get("hangups", 0),
                    average_qa_score=cand_met.get("average_qa_score"),
                    average_compliance_score=cand_met.get("average_compliance_score"),
                    transfer_rate=cand_met.get("transfer_rate"),
                    successful_transfer_rate=cand_met.get("successful_transfer_rate"),
                    failed_transfer_rate=cand_met.get("failed_transfer_rate"),
                    callback_rate=cand_met.get("callback_rate"),
                    hangup_rate=cand_met.get("hangup_rate"),
                    compliance_failure_count=cand_met.get("compliance_failure_count", 0),
                    critical_failure_count=cand_met.get("critical_failure_count", 0),
                    high_failure_count=cand_met.get("high_failure_count", 0),
                    medium_failure_count=cand_met.get("medium_failure_count", 0),
                    low_failure_count=cand_met.get("low_failure_count", 0),
                    transfer_before_consent_count=cand_met.get("transfer_before_consent_count", 0),
                    dnc_failure_count=cand_met.get("dnc_failure_count", 0),
                    wrong_number_failure_count=cand_met.get("wrong_number_failure_count", 0),
                    price_quote_count=cand_met.get("price_quote_count", 0),
                    licensed_claim_count=cand_met.get("licensed_claim_count", 0),
                    human_claim_count=cand_met.get("human_claim_count", 0),
                    you_qualify_count=cand_met.get("you_qualify_count", 0),
                    approval_claim_count=cand_met.get("approval_claim_count", 0),
                    sensitive_data_request_count=cand_met.get("sensitive_data_request_count", 0),
                    tool_failure_count=cand_met.get("tool_failure_count", 0),
                    labels=cand_met.get("labels"),
                )

                unk_met = latest_result["unknown_metrics"]
                unknown_metrics = CanaryVariantMetrics(
                    variant=unk_met.get("variant", "unknown"),
                    total_calls=unk_met.get("total_calls", 0),
                    total_turns=unk_met.get("total_turns", 0),
                    qa_reports=unk_met.get("qa_reports", 0),
                    tool_events=unk_met.get("tool_events", 0),
                    transfers=unk_met.get("transfers", 0),
                    successful_transfers=unk_met.get("successful_transfers", 0),
                    failed_transfers=unk_met.get("failed_transfers", 0),
                    callbacks=unk_met.get("callbacks", 0),
                    dnc_requests=unk_met.get("dnc_requests", 0),
                    wrong_number_requests=unk_met.get("wrong_number_requests", 0),
                    hangups=unk_met.get("hangups", 0),
                    average_qa_score=unk_met.get("average_qa_score"),
                    average_compliance_score=unk_met.get("average_compliance_score"),
                    transfer_rate=unk_met.get("transfer_rate"),
                    successful_transfer_rate=unk_met.get("successful_transfer_rate"),
                    failed_transfer_rate=unk_met.get("failed_transfer_rate"),
                    callback_rate=unk_met.get("callback_rate"),
                    hangup_rate=unk_met.get("hangup_rate"),
                    compliance_failure_count=unk_met.get("compliance_failure_count", 0),
                    critical_failure_count=unk_met.get("critical_failure_count", 0),
                    high_failure_count=unk_met.get("high_failure_count", 0),
                    medium_failure_count=unk_met.get("medium_failure_count", 0),
                    low_failure_count=unk_met.get("low_failure_count", 0),
                    transfer_before_consent_count=unk_met.get("transfer_before_consent_count", 0),
                    dnc_failure_count=unk_met.get("dnc_failure_count", 0),
                    wrong_number_failure_count=unk_met.get("wrong_number_failure_count", 0),
                    price_quote_count=unk_met.get("price_quote_count", 0),
                    licensed_claim_count=unk_met.get("licensed_claim_count", 0),
                    human_claim_count=unk_met.get("human_claim_count", 0),
                    you_qualify_count=unk_met.get("you_qualify_count", 0),
                    approval_claim_count=unk_met.get("approval_claim_count", 0),
                    sensitive_data_request_count=unk_met.get("sensitive_data_request_count", 0),
                    tool_failure_count=unk_met.get("tool_failure_count", 0),
                    labels=unk_met.get("labels"),
                )

                signals = [CanarySafetySignal(**s) for s in latest_result["safety_signals"]]

                result = CanaryMonitoringResult(
                    experiment_id=latest_result["experiment_id"],
                    experiment_name=latest_result["experiment_name"],
                    prompt_name=latest_result["prompt_name"],
                    status_before=latest_result["status_before"],
                    status_after=latest_result["status_after"],
                    monitored_at=latest_result["monitored_at"],
                    control_metrics=control_metrics,
                    candidate_metrics=candidate_metrics,
                    unknown_metrics=unknown_metrics,
                    safety_signals=signals,
                    rollback_triggered=latest_result["rollback_triggered"],
                    metrics_updated=latest_result["metrics_updated"],
                    promotion_ready=latest_result["promotion_ready"],
                    promotion_readiness=latest_result["promotion_readiness"],
                    window_start=latest_result.get("window_start"),
                    window_end=latest_result.get("window_end"),
                    rollback_reason=latest_result.get("rollback_reason"),
                    warnings=latest_result.get("warnings"),
                )

                json_p, md_p = monitor.write_monitoring_report(result, config.output_dir)
                result.report_json_path = json_p
                result.report_markdown_path = md_p

                sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
                sys.exit(0)
            else:
                # Run monitor first to produce a fresh report
                result = await monitor.monitor_experiment(config)
                sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
                sys.exit(0)

    except Exception as e:
        sys.stderr.write(json.dumps({"error": str(e)}, indent=2) + "\n")
        sys.exit(1)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
