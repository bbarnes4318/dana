"""Platform Scorecard evaluation and formatting for Dana quality gates."""

import json
import os
from typing import Dict, Any, List, Tuple
from config.runtime_env import is_production, allow_mock_tts

# Default Warning and Fail thresholds
DEFAULT_THRESHOLDS = {
    "p50_turn_latency_ms": {"warning": 450.0, "fail": 600.0},
    "p95_turn_latency_ms": {"warning": 850.0, "fail": 1000.0},
    "tts_first_audio_ms": {"warning": 200.0, "fail": 300.0},
    "llm_first_token_ms": {"warning": 250.0, "fail": 400.0},
    "barge_in_stop_audio_ms": {"warning": 200.0, "fail": 300.0},
    "bot_like_phrase_count": {"warning": 1, "fail": 3},
    "repetition_count": {"warning": 1, "fail": 3},
    "cost_per_connected_minute": {"warning": 0.08, "fail": 0.15},
    "humanlikeness_score": {"warning": 85.0, "fail": 70.0},  # Lower is worse
}


class PlatformScorecard:
    """Evaluates benchmark run results against safety and performance thresholds."""

    def __init__(
        self,
        benchmark_data: Dict[str, Any],
        provider_id: str = "dana",
        thresholds: Dict[str, Any] = None,
    ) -> None:
        self.benchmark_data = benchmark_data
        self.provider_id = provider_id
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.report = self._extract_provider_report()
        self.evaluation = self.evaluate()

    def _extract_provider_report(self) -> Dict[str, Any]:
        """Extracts the specific provider report from the benchmark data."""
        provider_reports = self.benchmark_data.get("provider_reports", {})
        
        # If provider_id exists directly as a key
        if self.provider_id in provider_reports:
            return provider_reports[self.provider_id]
            
        # Try finding case-insensitive match or name match
        for key, value in provider_reports.items():
            if key.lower() == self.provider_id.lower() or value.get("provider_name", "").lower() == self.provider_id.lower():
                return value
                
        # Return empty template if not found
        return {
            "provider_id": self.provider_id,
            "provider_name": self.provider_id,
            "scenario_results": {}
        }

    def evaluate(self) -> Dict[str, Any]:
        """Runs the quality gate checks on the provider report.

        Returns:
            Dictionary containing pass status, hard fails, warning, and failure lists.
        """
        hard_fails: List[str] = []
        threshold_fails: List[str] = []
        threshold_warnings: List[str] = []

        scenario_results = self.report.get("scenario_results", {})
        if not scenario_results:
            hard_fails.append(f"No scenario results found for provider '{self.provider_id}'")
            return {
                "passed": False,
                "provider_id": self.provider_id,
                "provider_name": self.report.get("provider_name", self.provider_id),
                "hard_fails": hard_fails,
                "threshold_fails": threshold_fails,
                "threshold_warnings": threshold_warnings,
                "metrics": {}
            }

        # 1. Evaluate environment-level constraints (production MockKokoro)
        if is_production() and allow_mock_tts():
            hard_fails.append("CRITICAL: Mock TTS (MockKokoro) is allowed/active in production mode")

        # 2. Evaluate scenario-level hard fails and gather average metrics
        total_p50 = 0.0
        total_p95 = 0.0
        total_tts = 0.0
        total_llm = 0.0
        total_barge = 0.0
        total_bot = 0
        total_rep = 0
        total_cost_per_min = 0.0
        total_human = 0.0
        num_scenarios = len(scenario_results)

        for scenario_id, metrics in scenario_results.items():
            # Hard Fail checks
            dnc_fails = metrics.get("dnc_failure_count", 0)
            if dnc_fails > 0:
                hard_fails.append(f"Scenario '{scenario_id}': DNC violation detected ({dnc_fails} failures)")

            wn_fails = metrics.get("wrong_number_failure_count", 0)
            if wn_fails > 0:
                hard_fails.append(f"Scenario '{scenario_id}': Wrong-number violation detected ({wn_fails} failures)")

            consent_fails = metrics.get("transfer_without_consent_count", 0)
            if consent_fails > 0:
                hard_fails.append(f"Scenario '{scenario_id}': Transfer without consent detected ({consent_fails} failures)")

            phrase_fails = metrics.get("compliance_hard_fail_count", 0)
            if phrase_fails > 0:
                hard_fails.append(f"Scenario '{scenario_id}': Forbidden compliance phrase violation detected ({phrase_fails} failures)")

            cost_per_min = metrics.get("cost_per_connected_minute", 0.0)
            if cost_per_min is None or cost_per_min <= 0.0:
                hard_fails.append(f"Scenario '{scenario_id}': Missing cost accounting (cost_per_connected_minute <= 0.0)")

            # Accumulate metrics for averaging
            total_p50 += metrics.get("p50_turn_latency_ms", 0.0)
            total_p95 += metrics.get("p95_turn_latency_ms", 0.0)
            total_tts += metrics.get("tts_first_audio_ms", 0.0)
            total_llm += metrics.get("llm_first_token_ms", 0.0)
            total_barge += metrics.get("barge_in_stop_audio_ms", 0.0)
            total_bot += metrics.get("bot_like_phrase_count", 0)
            total_rep += metrics.get("repetition_count", 0)
            total_cost_per_min += cost_per_min
            total_human += metrics.get("humanlikeness_score", 100.0)

        # Average metrics
        avg_metrics = {
            "p50_turn_latency_ms": round(total_p50 / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "p95_turn_latency_ms": round(total_p95 / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "tts_first_audio_ms": round(total_tts / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "llm_first_token_ms": round(total_llm / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "barge_in_stop_audio_ms": round(total_barge / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "bot_like_phrase_count": round(total_bot / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "repetition_count": round(total_rep / num_scenarios, 2) if num_scenarios > 0 else 0.0,
            "cost_per_connected_minute": round(total_cost_per_min / num_scenarios, 4) if num_scenarios > 0 else 0.0,
            "humanlikeness_score": round(total_human / num_scenarios, 2) if num_scenarios > 0 else 0.0,
        }

        # 3. Evaluate average metrics against soft warning/fail thresholds
        for metric_name, value in avg_metrics.items():
            limits = self.thresholds.get(metric_name)
            if not limits:
                continue

            warn_limit = limits.get("warning")
            fail_limit = limits.get("fail")
            clean_name = metric_name.replace("_", " ").title()

            if metric_name == "humanlikeness_score":
                # Lower score is worse
                if value < fail_limit:
                    threshold_fails.append(f"Average {clean_name} too low: {value}% < {fail_limit}% threshold")
                elif value < warn_limit:
                    threshold_warnings.append(f"Average {clean_name} is low: {value}% < {warn_limit}% warning limit")
            else:
                # Higher score is worse
                if value > fail_limit:
                    threshold_fails.append(f"Average {clean_name} too high: {value} > {fail_limit} threshold")
                elif value > warn_limit:
                    threshold_warnings.append(f"Average {clean_name} is high: {value} > {warn_limit} warning limit")

        passed = len(hard_fails) == 0 and len(threshold_fails) == 0

        return {
            "passed": passed,
            "provider_id": self.provider_id,
            "provider_name": self.report.get("provider_name", self.provider_id),
            "hard_fails": hard_fails,
            "threshold_fails": threshold_fails,
            "threshold_warnings": threshold_warnings,
            "metrics": avg_metrics
        }

    def generate_json(self) -> str:
        """Returns JSON representation of scorecard."""
        return json.dumps(self.evaluation, indent=2)

    def generate_markdown(self) -> str:
        """Returns Markdown formatted scorecard."""
        eval_res = self.evaluation
        metrics = eval_res["metrics"]
        
        md_lines = []
        md_lines.append(f"# Dana Platform Quality Scorecard: {eval_res['provider_name']}")
        md_lines.append("")
        
        status_str = "🟢 **PASSED**" if eval_res["passed"] else "🔴 **FAILED**"
        md_lines.append(f"**Overall Status:** {status_str}")
        md_lines.append(f"**Benchmark Run ID:** `{self.benchmark_data.get('run_id', 'unknown')}`")
        md_lines.append(f"**Date:** {self.benchmark_data.get('timestamp', 'unknown')}")
        md_lines.append("")

        if eval_res["hard_fails"]:
            md_lines.append("## ❌ Critical Safety & Compliance Violations")
            for hf in eval_res["hard_fails"]:
                md_lines.append(f"- {hf}")
            md_lines.append("")

        if eval_res["threshold_fails"]:
            md_lines.append("## ⚠️ SLA Performance Failures")
            for tf in eval_res["threshold_fails"]:
                md_lines.append(f"- {tf}")
            md_lines.append("")

        if eval_res["threshold_warnings"]:
            md_lines.append("## ℹ️ Performance Warnings")
            for tw in eval_res["threshold_warnings"]:
                md_lines.append(f"- {tw}")
            md_lines.append("")

        md_lines.append("## 📊 Performance Metrics Breakdown")
        md_lines.append("| Metric | Average Value | Warning Threshold | Fail Threshold | Status |")
        md_lines.append("| :--- | :---: | :---: | :---: | :---: |")

        for metric_name, value in metrics.items():
            limits = self.thresholds.get(metric_name, {})
            warn_limit = limits.get("warning", "N/A")
            fail_limit = limits.get("fail", "N/A")

            # Determine status symbol
            status = "✅ OK"
            if metric_name == "humanlikeness_score":
                if value < fail_limit:
                    status = "❌ FAIL"
                elif value < warn_limit:
                    status = "⚠️ WARN"
            else:
                if value > fail_limit:
                    status = "❌ FAIL"
                elif value > warn_limit:
                    status = "⚠️ WARN"

            # Clean name for table
            clean_name = metric_name.replace("_", " ").title()
            # Special formatting for cost and percentage
            val_str = f"${value:.4f}" if "cost" in metric_name else f"{value}"
            if "score" in metric_name:
                val_str = f"{value}%"
            elif "ms" in metric_name:
                val_str = f"{value} ms"

            warn_str = f"${warn_limit}" if "cost" in metric_name else f"{warn_limit}"
            if "score" in metric_name:
                warn_str = f"{warn_limit}%"
            elif "ms" in metric_name:
                warn_str = f"{warn_limit} ms"

            fail_str = f"${fail_limit}" if "cost" in metric_name else f"{fail_limit}"
            if "score" in metric_name:
                fail_str = f"{fail_limit}%"
            elif "ms" in metric_name:
                fail_str = f"{fail_limit} ms"

            md_lines.append(f"| {clean_name} | {val_str} | {warn_str} | {fail_str} | {status} |")

        md_lines.append("")
        return "\n".join(md_lines)

    def print_summary(self) -> None:
        """Prints a clean terminal summary to stdout/stderr."""
        eval_res = self.evaluation
        print("=" * 60)
        print(f"Dana Platform Quality Summary for: {eval_res['provider_name']}")
        print("-" * 60)
        status = "PASSED" if eval_res["passed"] else "FAILED"
        print(f"Overall Status: {status}")
        
        if eval_res["hard_fails"]:
            print("\nCritical Hard Failures:")
            for hf in eval_res["hard_fails"]:
                print(f"  [CRITICAL] {hf}")
                
        if eval_res["threshold_fails"]:
            print("\nPerformance SLA Breaches:")
            for tf in eval_res["threshold_fails"]:
                print(f"  [FAIL] {tf}")

        if eval_res["threshold_warnings"]:
            print("\nPerformance Warnings:")
            for tw in eval_res["threshold_warnings"]:
                print(f"  [WARN] {tw}")

        print("\nKey Metrics:")
        metrics = eval_res["metrics"]
        print(f"  P50 Turn Latency:          {metrics.get('p50_turn_latency_ms', 0)} ms")
        print(f"  P95 Turn Latency:          {metrics.get('p95_turn_latency_ms', 0)} ms")
        print(f"  TTS First Audio:           {metrics.get('tts_first_audio_ms', 0)} ms")
        print(f"  LLM First Token:           {metrics.get('llm_first_token_ms', 0)} ms")
        print(f"  Humanlikeness Score:       {metrics.get('humanlikeness_score', 0)}%")
        print(f"  Cost Per Connected Minute: ${metrics.get('cost_per_connected_minute', 0):.4f}")
        print("=" * 60)
