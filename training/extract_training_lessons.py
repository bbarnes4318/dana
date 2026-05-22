"""Extract structured training lessons from cleaned transcript paragraphs.

Uses keyword heuristics to identify sales lessons, objection handling patterns,
compliance warnings, and stage-specific guidance from training transcripts.

CLI usage::

    python training/extract_training_lessons.py <transcript_file>
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from training.ingest_video_transcript import VideoTranscriptIngestor
from training.training_note_schema import TrainingNote

# ---------------------------------------------------------------------------
# Keyword / pattern banks
# ---------------------------------------------------------------------------

# Phrases that signal a sales lesson with good/bad response contrast
_LESSON_MARKERS: list[str] = [
    "never say",
    "always say",
    "don't say",
    "do not say",
    "don't ever",
    "instead say",
    "good response",
    "bad response",
    "better response",
    "worse response",
    "wrong way",
    "right way",
    "should say",
    "should not say",
    "try saying",
    "avoid saying",
    "example of what to say",
    "example of what not to say",
]

# Objection-related phrases
_OBJECTION_MARKERS: list[str] = [
    "objection",
    "push back",
    "pushback",
    "when they say",
    "if they say",
    "when the prospect says",
    "common response",
    "handle the objection",
    "overcome the objection",
    "i already have",
    "not interested",
    "can't afford",
    "too expensive",
    "think about it",
    "call back later",
    "send me information",
]

# Compliance phrases
_COMPLIANCE_MARKERS: list[str] = [
    "compliance",
    "compliant",
    "do not guarantee",
    "never guarantee",
    "cannot promise",
    "don't promise",
    "licensed",
    "regulated",
    "legal requirement",
    "hipaa",
    "do not record",
    "permission to record",
    "disclaimers",
    "disclosure",
    "misrepresent",
]

# Call-stage detection
_STAGE_PATTERNS: dict[str, list[str]] = {
    "opening": ["opening", "introduction", "greeting", "first impression", "warm up"],
    "qualifying": ["qualifying", "qualification", "eligibility", "health questions", "pre-qualify"],
    "presenting": ["presenting", "presentation", "benefits", "explain the plan", "coverage details"],
    "closing": ["closing", "close the sale", "ask for the sale", "commitment", "sign up"],
    "objection_handling": ["objection", "push back", "overcome", "rebuttal", "handle"],
}


def _lower_contains(text: str, markers: Sequence[str]) -> bool:
    """Return True if *text* contains any of the *markers* (case-insensitive)."""
    text_lower = text.lower()
    return any(m in text_lower for m in markers)


def _detect_call_stage(text: str) -> str | None:
    """Detect the call stage a paragraph relates to, if any."""
    text_lower = text.lower()
    for stage, keywords in _STAGE_PATTERNS.items():
        if any(k in text_lower for k in keywords):
            return stage
    return None


def _detect_objection_type(text: str) -> str | None:
    """Detect a specific objection type from the text."""
    objection_map: dict[str, str] = {
        "already have": "already_has_coverage",
        "not interested": "not_interested",
        "can't afford": "affordability",
        "too expensive": "affordability",
        "think about it": "stalling",
        "call back": "stalling",
        "send me information": "information_request",
    }
    text_lower = text.lower()
    for phrase, obj_type in objection_map.items():
        if phrase in text_lower:
            return obj_type
    return None


def _detect_compliance_risk(text: str) -> str | None:
    """Return a short compliance-risk label if the paragraph mentions one."""
    text_lower = text.lower()
    if any(w in text_lower for w in ("guarantee", "promise", "misrepresent")):
        return "misleading_claims"
    if any(w in text_lower for w in ("hipaa", "record", "permission")):
        return "privacy_violation"
    if any(w in text_lower for w in ("licensed", "regulated", "legal")):
        return "licensing_requirement"
    if _lower_contains(text, _COMPLIANCE_MARKERS):
        return "general_compliance"
    return None


def _extract_example(text: str, bad: bool = True) -> str:
    r"""Try to pull a quoted example from the paragraph.

    Looks for text inside quotation marks near bad/good marker words.
    Falls back to a truncated snippet of the paragraph.
    """
    # Find all quoted strings
    quotes = re.findall(r'"([^"]{5,})"', text) or re.findall(r"'([^']{5,})'", text)
    if not quotes:
        # Fallback: first 120 chars of the paragraph
        return text[:120].strip()

    text_lower = text.lower()
    bad_cues = ["bad", "wrong", "never", "don't", "do not", "avoid", "worse"]
    good_cues = ["good", "right", "always", "instead", "better", "should", "try"]

    target_cues = bad_cues if bad else good_cues

    # Try to match a quote near a cue word
    for quote in quotes:
        # Find position of quote in original text
        pos = text.find(quote)
        if pos == -1:
            continue
        # Look at the 80 characters preceding the quote for a cue word
        context = text_lower[max(0, pos - 80) : pos]
        if any(cue in context for cue in target_cues):
            return quote

    # If we can't distinguish, return first quote for bad, last for good
    if bad:
        return quotes[0]
    return quotes[-1] if len(quotes) > 1 else quotes[0]


def _determine_topic(text: str) -> str:
    """Classify the paragraph into a topic category."""
    if _lower_contains(text, _COMPLIANCE_MARKERS):
        return "compliance"
    if _lower_contains(text, _OBJECTION_MARKERS):
        return "objection_handling"
    stage = _detect_call_stage(text)
    if stage:
        return stage
    return "general_sales"


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------


class TrainingLessonExtractor:
    """Extract :class:`TrainingNote` objects from cleaned transcript paragraphs."""

    def extract_from_transcript(
        self,
        paragraphs: list[str],
        source: str,
    ) -> list[TrainingNote]:
        """Scan *paragraphs* for training lessons and return structured notes.

        A paragraph is considered a candidate if it contains at least one
        lesson marker, objection marker, or compliance marker.

        Args:
            paragraphs: Cleaned transcript paragraphs (from ``VideoTranscriptIngestor``).
            source: File path or URL of the source material.

        Returns:
            List of extracted :class:`TrainingNote` objects.
        """
        notes: list[TrainingNote] = []
        now = datetime.now(timezone.utc)

        for para in paragraphs:
            is_lesson = _lower_contains(para, _LESSON_MARKERS)
            is_objection = _lower_contains(para, _OBJECTION_MARKERS)
            is_compliance = _lower_contains(para, _COMPLIANCE_MARKERS)

            if not (is_lesson or is_objection or is_compliance):
                continue

            topic = _determine_topic(para)
            call_stage = _detect_call_stage(para)
            objection_type = _detect_objection_type(para) if is_objection else None
            compliance_risk = _detect_compliance_risk(para) if is_compliance else None

            bad_example = _extract_example(para, bad=True)
            good_example = _extract_example(para, bad=False)

            # Build the sales lesson summary — first sentence or up to 200 chars
            first_sentence_match = re.match(r"(.+?[.!?])\s", para)
            sales_lesson = first_sentence_match.group(1) if first_sentence_match else para[:200]

            note = TrainingNote(
                source=source,
                topic=topic,
                sales_lesson=sales_lesson,
                bad_response_example=bad_example,
                good_response_example=good_example,
                call_stage=call_stage,
                objection_type=objection_type,
                compliance_risk=compliance_risk,
                use_in_live_call=True,
                extracted_at=now,
            )
            notes.append(note)

        return notes

    # ---- persistence -----------------------------------------------------

    @staticmethod
    def save_notes(notes: list[TrainingNote], output_dir: str | Path) -> None:
        """Persist notes as JSONL and generate per-topic Markdown summaries.

        Appends to ``data/training_notes.jsonl`` relative to *output_dir* and
        writes individual Markdown files into
        ``kb/training_notes/generated/`` relative to *output_dir*.

        Args:
            notes: List of training notes to save.
            output_dir: Project root directory.
        """
        output_dir = Path(output_dir)

        # --- JSONL ---
        jsonl_path = output_dir / "data" / "training_notes.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as f:
            for note in notes:
                f.write(note.model_dump_json() + "\n")

        # --- Markdown per topic ---
        md_dir = output_dir / "kb" / "training_notes" / "generated"
        md_dir.mkdir(parents=True, exist_ok=True)

        # Group notes by topic
        by_topic: dict[str, list[TrainingNote]] = {}
        for note in notes:
            by_topic.setdefault(note.topic, []).append(note)

        for topic, topic_notes in by_topic.items():
            md_path = md_dir / f"{topic}.md"
            with md_path.open("a", encoding="utf-8") as f:
                f.write(f"# {topic.replace('_', ' ').title()}\n\n")
                for note in topic_notes:
                    f.write(f"## {note.sales_lesson[:80]}\n\n")
                    f.write(f"**Source:** {note.source}\n\n")
                    if note.call_stage:
                        f.write(f"**Call Stage:** {note.call_stage}\n\n")
                    if note.objection_type:
                        f.write(f"**Objection Type:** {note.objection_type}\n\n")
                    if note.compliance_risk:
                        f.write(f"**Compliance Risk:** {note.compliance_risk}\n\n")
                    f.write(f"**Bad Example:** {note.bad_response_example}\n\n")
                    f.write(f"**Good Example:** {note.good_response_example}\n\n")
                    f.write("---\n\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry-point: extract lessons from a transcript file."""
    if len(sys.argv) < 2:
        print("Usage: python training/extract_training_lessons.py <transcript_file>")
        sys.exit(1)

    filepath = sys.argv[1]

    # Ingest
    ingestor = VideoTranscriptIngestor()
    try:
        paragraphs = ingestor.ingest_file(filepath)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Extract
    extractor = TrainingLessonExtractor()
    notes = extractor.extract_from_transcript(paragraphs, source=filepath)

    if not notes:
        print("No training lessons found in the transcript.")
        sys.exit(0)

    print(f"Extracted {len(notes)} training lesson(s):\n")
    for i, note in enumerate(notes, 1):
        print(f"  [{i}] {note.topic}: {note.sales_lesson[:100]}")

    # Save relative to project root (assume CWD or derive from script location)
    project_root = Path(__file__).resolve().parent.parent
    extractor.save_notes(notes, project_root)
    print(f"\nSaved to {project_root / 'data' / 'training_notes.jsonl'}")
    print(f"Markdown generated in {project_root / 'kb' / 'training_notes' / 'generated' / ''}")


if __name__ == "__main__":
    main()
