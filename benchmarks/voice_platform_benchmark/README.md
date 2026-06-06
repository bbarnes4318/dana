# Outbound Voice Platform Benchmark Harness

This benchmark harness evaluates and compares outbound AI voice platforms (including Dana and key industry reference competitors) across latency, compliance, cost, humanlikeness, and outcomes.

## Getting Started

To run the complete benchmark suite and view the leaderboard output:

```bash
python -m benchmarks.voice_platform_benchmark.leaderboard
```

By default, this command will:
1. Load provider reference profiles from `benchmarks/voice_platform_benchmark/providers.yaml`.
2. Load benchmark scenarios from `benchmarks/voice_platform_benchmark/scenarios.yaml`.
3. Simulate calls for all scenarios and providers, outputting latency records compatible with `LatencyRecorder`.
4. Score calls across compliance, outcomes, cost, and humanlikeness.
5. Apply hard compliance gates (setting any compliance violation to an overall grade of **F**).
6. Save a JSON summary to `data/benchmarks/leaderboard.json` and a Markdown report to `data/benchmarks/leaderboard.md`.
7. Print a formatted summary table to `stdout`.

## Directory Structure

```text
benchmarks/
  voice_platform_benchmark/
    __init__.py
    providers.yaml           # Provider latency and billing reference profiles
    scenarios.yaml           # The 14 benchmark scenarios (DNC, price shopper, etc.)
    metrics_schema.py        # Pydantic schemas for metrics and reports
    score_latency.py         # SLO targets verification and scoring
    score_cost.py            # Financial/cost analysis
    score_humanlikeness.py   # Repetition and bot phrase checking
    score_compliance.py      # Hard gates: DNC, wrong-number, consent, claims
    score_outcomes.py        # Validates actual outcomes against expected
    run_synthetic_call.py    # Generates synthetic/simulated conversations
    run_transcript_replay.py  # Replays transcripts and executes scoring modules
    leaderboard.py           # CLI orchestrator
    README.md                # This documentation
```

## SLO Targets

The default target SLOs are configured under `metrics_schema.py` and scored in `score_latency.py`:
- **P50 Turn Latency**: < 450ms
- **P95 Turn Latency**: < 850ms
- **LLM First Token**: < 250ms
- **TTS First Audio**: < 200ms
- **Barge-in Stop Audio**: < 200ms

## Compliance Hard Gates

Any of the following violations immediately sets the provider's overall score to `0.0` and grade to **F** for that scenario:
- **DNC Failure**: Continues pitching/selling after do-not-call request.
- **Wrong Number Failure**: Continues pitching/selling after wrong-number warning.
- **Transfer without Consent**: Initiates transfer tools or transfer language without prior explicit affirmative consent.
- **Compliance Hard Fail**: AI quotes specific dollar amounts, claims personal licensed status, claims government affiliation, or guarantees approval.
