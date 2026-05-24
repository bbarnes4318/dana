"""Export high-quality calls as fine-tune training examples.

Filters scored calls by minimum quality threshold, then converts each
approved call into the ``{messages: [{role, content}, ...]}`` format
expected by fine-tuning APIs (e.g. OpenAI JSONL format).

CLI usage::

    python qa/export_finetune_examples.py --min-score 7.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qa.call_record import CallRecord
from qa.scoring import QAScorecard


class FinetuneExporter:
    """Exports high-quality call records as fine-tune training examples."""

    def export_approved(
        self,
        records: list[CallRecord],
        scorecards: list[QAScorecard],
        min_score: float = 7.0,
    ) -> list[dict]:
        """Filter and convert approved calls to fine-tune JSONL format.

        Parameters:
            records: All call records to consider.
            scorecards: Corresponding QA scorecards (same order as records).
            min_score: Minimum overall_score to include. Defaults to 7.0.

        Returns:
            List of dicts, each with a ``messages`` key containing the
            conversation in ``[{role, content}, ...]`` format.
        """
        if len(records) != len(scorecards):
            raise ValueError(
                f"records ({len(records)}) and scorecards ({len(scorecards)}) "
                f"must be the same length"
            )

        examples: list[dict] = []

        for record, scorecard in zip(records, scorecards):
            if scorecard.overall_score < min_score:
                continue

            messages = self._record_to_messages(record)
            if messages:
                examples.append({"messages": messages})

        return examples

    @staticmethod
    def _record_to_messages(record: CallRecord) -> list[dict]:
        """Convert a CallRecord's turns into chat message format.

        Maps ``agent`` -> ``assistant`` and ``prospect`` -> ``user``.
        Prepends a system message with context.
        """
        messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    "You are Dana, a friendly and professional final expense "
                    "insurance qualification agent. Your job is to warmly greet "
                    "the prospect, collect qualification information (age, state, "
                    "phone type, budget, interest), handle objections empathetically, "
                    "and transfer qualified prospects to a licensed agent. Never "
                    "quote premiums, guarantee acceptance, or make coverage promises."
                ),
            }
        ]

        for turn in record.turns:
            role = "assistant" if turn.speaker == "agent" else "user"
            messages.append({"role": role, "content": turn.text})

        return messages


def _load_records_from_jsonl(path: Path) -> list[CallRecord]:
    """Load CallRecord objects from a JSONL file."""
    records: list[CallRecord] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(CallRecord.model_validate_json(line))
    return records


def _load_scorecards_from_jsonl(path: Path) -> list[QAScorecard]:
    """Load QAScorecard objects from a JSONL file."""
    scorecards: list[QAScorecard] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                data = json.loads(line)
                scorecards.append(
                    QAScorecard(
                        call_id=data["call_id"],
                        scores=data.get("scores", {}),
                        issues=data.get("issues", []),
                        overall_score=data.get("overall_score", 0.0),
                        grade=data.get("grade", "F"),
                    )
                )
    return scorecards


def main() -> None:
    """CLI entry-point for exporting fine-tune examples."""
    parser = argparse.ArgumentParser(
        description="Export high-quality calls as fine-tune JSONL."
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=7.0,
        help="Minimum overall QA score to include (default: 7.0).",
    )
    parser.add_argument(
        "--records",
        type=str,
        default="data/calls/records.jsonl",
        help="Path to call records JSONL file.",
    )
    parser.add_argument(
        "--scorecards",
        type=str,
        default="data/qa_reports/scorecards.jsonl",
        help="Path to QA scorecards JSONL file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/finetune_examples.jsonl",
        help="Output path for fine-tune JSONL.",
    )
    args = parser.parse_args()

    records_path = Path(args.records)
    scorecards_path = Path(args.scorecards)
    output_path = Path(args.output)

    if not records_path.exists():
        print(f"Error: records file not found: {records_path}", file=sys.stderr)
        sys.exit(1)
    if not scorecards_path.exists():
        print(f"Error: scorecards file not found: {scorecards_path}", file=sys.stderr)
        sys.exit(1)

    records = _load_records_from_jsonl(records_path)
    scorecards = _load_scorecards_from_jsonl(scorecards_path)

    exporter = FinetuneExporter()
    examples = exporter.export_approved(records, scorecards, min_score=args.min_score)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for example in examples:
            fh.write(json.dumps(example, ensure_ascii=False) + "\n")

    print(
        f"Exported {len(examples)} fine-tune example(s) "
        f"(min_score={args.min_score}) to {output_path}"
    )


if __name__ == "__main__":
    main()
