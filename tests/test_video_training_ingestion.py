"""Tests for Phase 8 — Video / Training Transcript Ingestion."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from training.ingest_video_transcript import VideoTranscriptIngestor
from training.training_note_schema import TrainingNote
from training.extract_training_lessons import TrainingLessonExtractor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_VTT = """\
WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:05.000
Never say "I'm calling to sell you insurance."

00:00:05.000 --> 00:00:10.000
Instead say "Hi, this is Dana, I'm reaching out because you requested information."

00:00:10.000 --> 00:00:15.000
Instead say "Hi, this is Dana, I'm reaching out because you requested information."
"""

SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:05,000
When the prospect says "I already have insurance,"

2
00:00:05,000 --> 00:00:10,000
don't say "You probably need more."

3
00:00:10,000 --> 00:00:15,000
A good response is "That's great — is your current policy designed for final expenses?"
"""

SAMPLE_TXT = """\
Opening the Call

Never   say   "I'm  calling   to  sell   you   life insurance."
Instead say "Hi, this is Dana calling on behalf of Senior Life Services."

The   good   response   is warm   and   personal.
"""

SAMPLE_TRAINING_PARAGRAPHS = [
    (
        'When you make your opening, never say "I\'m calling to sell you insurance." '
        'Instead say "Hi, this is Dana, reaching out because you requested information." '
        "A good response is warm and personal."
    ),
    (
        'If they say "I can\'t afford it," don\'t say "It\'s really cheap, trust me." '
        'A better response is: "I completely understand — most of our plans work out to about '
        'a dollar a day." Always frame affordability in daily terms.'
    ),
    (
        "From a compliance standpoint, never guarantee approval. "
        'Don\'t say "You\'re definitely going to be approved." '
        'The compliant response is: "Based on what you\'ve shared, it looks like you may qualify." '
        "Misrepresenting approval certainty is a serious compliance risk."
    ),
    (
        "This paragraph has no training markers at all and should be skipped "
        "by the extractor because it mentions nothing interesting."
    ),
]


@pytest.fixture
def ingestor() -> VideoTranscriptIngestor:
    return VideoTranscriptIngestor()


@pytest.fixture
def extractor() -> TrainingLessonExtractor:
    return TrainingLessonExtractor()


# ---------------------------------------------------------------------------
# test_clean_vtt_removes_timestamps
# ---------------------------------------------------------------------------


def test_clean_vtt_removes_timestamps(ingestor: VideoTranscriptIngestor) -> None:
    """VTT cleaner should strip WEBVTT header, metadata, and timestamp lines."""
    cleaned = ingestor.clean_vtt(SAMPLE_VTT)

    assert "WEBVTT" not in cleaned
    assert "00:00:00.000" not in cleaned
    assert "-->" not in cleaned
    assert "Kind:" not in cleaned
    # Actual caption text should remain
    assert "Never say" in cleaned
    assert "Instead say" in cleaned


# ---------------------------------------------------------------------------
# test_clean_srt_removes_sequence_numbers
# ---------------------------------------------------------------------------


def test_clean_srt_removes_sequence_numbers(ingestor: VideoTranscriptIngestor) -> None:
    """SRT cleaner should strip sequence numbers and timestamp lines."""
    cleaned = ingestor.clean_srt(SAMPLE_SRT)

    assert "00:00:00,000" not in cleaned
    assert "-->" not in cleaned
    # Caption text should remain
    assert "prospect says" in cleaned
    assert "good response" in cleaned


# ---------------------------------------------------------------------------
# test_clean_txt_normalizes_whitespace
# ---------------------------------------------------------------------------


def test_clean_txt_normalizes_whitespace(ingestor: VideoTranscriptIngestor) -> None:
    """TXT cleaning should collapse multiple spaces into single spaces."""
    cleaned = ingestor.clean_transcript(SAMPLE_TXT, ".txt")

    # No runs of multiple spaces
    assert "  " not in cleaned
    # Content preserved
    assert "Never say" in cleaned
    assert "Instead say" in cleaned


# ---------------------------------------------------------------------------
# test_ingest_file_returns_paragraphs
# ---------------------------------------------------------------------------


def test_ingest_file_returns_paragraphs(
    ingestor: VideoTranscriptIngestor, tmp_path: Path
) -> None:
    """ingest_file should read a file and return a list of non-empty paragraphs."""
    txt_file = tmp_path / "transcript.txt"
    txt_file.write_text(SAMPLE_TXT, encoding="utf-8")

    paragraphs = ingestor.ingest_file(txt_file)

    assert isinstance(paragraphs, list)
    assert len(paragraphs) >= 2  # At least the two main content blocks
    assert all(isinstance(p, str) for p in paragraphs)
    assert all(p.strip() for p in paragraphs)  # No empty paragraphs


def test_ingest_file_vtt(
    ingestor: VideoTranscriptIngestor, tmp_path: Path
) -> None:
    """ingest_file should handle .vtt files correctly."""
    vtt_file = tmp_path / "test.vtt"
    vtt_file.write_text(SAMPLE_VTT, encoding="utf-8")

    paragraphs = ingestor.ingest_file(vtt_file)

    assert len(paragraphs) >= 1
    assert "WEBVTT" not in " ".join(paragraphs)
    assert "-->" not in " ".join(paragraphs)


def test_ingest_file_srt(
    ingestor: VideoTranscriptIngestor, tmp_path: Path
) -> None:
    """ingest_file should handle .srt files correctly."""
    srt_file = tmp_path / "test.srt"
    srt_file.write_text(SAMPLE_SRT, encoding="utf-8")

    paragraphs = ingestor.ingest_file(srt_file)

    assert len(paragraphs) >= 1
    assert "-->" not in " ".join(paragraphs)


def test_ingest_file_unsupported_extension(
    ingestor: VideoTranscriptIngestor, tmp_path: Path
) -> None:
    """ingest_file should raise ValueError for unsupported extensions."""
    bad_file = tmp_path / "video.mp4"
    bad_file.write_text("not a transcript", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported file extension"):
        ingestor.ingest_file(bad_file)


def test_ingest_file_missing_file(ingestor: VideoTranscriptIngestor) -> None:
    """ingest_file should raise FileNotFoundError for missing files."""
    with pytest.raises(FileNotFoundError):
        ingestor.ingest_file("/nonexistent/transcript.txt")


# ---------------------------------------------------------------------------
# test_extract_lessons_finds_patterns
# ---------------------------------------------------------------------------


def test_extract_lessons_finds_patterns(extractor: TrainingLessonExtractor) -> None:
    """Extractor should find lessons from paragraphs with marker phrases."""
    notes = extractor.extract_from_transcript(
        SAMPLE_TRAINING_PARAGRAPHS,
        source="test_transcript.txt",
    )

    # Should find 3 out of 4 paragraphs (last one has no markers)
    assert len(notes) == 3

    # All notes should have required fields
    for note in notes:
        assert note.source == "test_transcript.txt"
        assert note.topic
        assert note.sales_lesson
        assert note.bad_response_example
        assert note.good_response_example

    # Check topic classification
    topics = [n.topic for n in notes]
    assert "compliance" in topics

    # At least one note should have a compliance risk
    compliance_notes = [n for n in notes if n.compliance_risk is not None]
    assert len(compliance_notes) >= 1


def test_extract_lessons_empty_input(extractor: TrainingLessonExtractor) -> None:
    """Extractor should return empty list for paragraphs with no markers."""
    notes = extractor.extract_from_transcript(
        ["This paragraph has no training content at all."],
        source="empty.txt",
    )
    assert notes == []


# ---------------------------------------------------------------------------
# test_save_notes_creates_files
# ---------------------------------------------------------------------------


def test_save_notes_creates_files(
    extractor: TrainingLessonExtractor, tmp_path: Path
) -> None:
    """save_notes should create JSONL and Markdown files in the output directory."""
    notes = extractor.extract_from_transcript(
        SAMPLE_TRAINING_PARAGRAPHS,
        source="test_transcript.txt",
    )
    assert len(notes) > 0

    # Save to tmp_path as project root
    extractor.save_notes(notes, tmp_path)

    # Check JSONL file
    jsonl_path = tmp_path / "data" / "training_notes.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(notes)

    # Each line should be valid JSON
    for line in lines:
        data = json.loads(line)
        assert "source" in data
        assert "topic" in data
        assert "sales_lesson" in data
        assert "id" in data

    # Check Markdown directory
    md_dir = tmp_path / "kb" / "training_notes" / "generated"
    assert md_dir.exists()
    md_files = list(md_dir.glob("*.md"))
    assert len(md_files) >= 1


def test_save_notes_appends(
    extractor: TrainingLessonExtractor, tmp_path: Path
) -> None:
    """save_notes should append to existing JSONL, not overwrite."""
    notes = extractor.extract_from_transcript(
        SAMPLE_TRAINING_PARAGRAPHS[:2],
        source="first_batch.txt",
    )

    extractor.save_notes(notes, tmp_path)
    extractor.save_notes(notes, tmp_path)

    jsonl_path = tmp_path / "data" / "training_notes.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(notes) * 2


# ---------------------------------------------------------------------------
# test_training_note_schema_validation
# ---------------------------------------------------------------------------


def test_training_note_schema_validation() -> None:
    """TrainingNote should validate all fields correctly."""
    note = TrainingNote(
        source="test_video.mp4",
        topic="objection_handling",
        sales_lesson="Always acknowledge the prospect's concern before responding.",
        bad_response_example="You're wrong about that.",
        good_response_example="I understand your concern, let me address that.",
        call_stage="objection_handling",
        objection_type="not_interested",
        compliance_risk=None,
        use_in_live_call=True,
    )

    assert note.source == "test_video.mp4"
    assert note.topic == "objection_handling"
    assert note.call_stage == "objection_handling"
    assert note.objection_type == "not_interested"
    assert note.compliance_risk is None
    assert note.use_in_live_call is True
    assert isinstance(note.extracted_at, datetime)
    assert note.id  # Should be a non-empty UUID string


def test_training_note_defaults() -> None:
    """TrainingNote should have sensible defaults for optional fields."""
    note = TrainingNote(
        source="video.txt",
        topic="general",
        sales_lesson="Be friendly.",
        bad_response_example="Bad.",
        good_response_example="Good.",
    )

    assert note.call_stage is None
    assert note.objection_type is None
    assert note.compliance_risk is None
    assert note.use_in_live_call is True
    assert note.extracted_at is not None
    assert note.id is not None


def test_training_note_json_roundtrip() -> None:
    """TrainingNote should serialise/deserialise cleanly via JSON."""
    note = TrainingNote(
        source="source.vtt",
        topic="compliance",
        sales_lesson="Always disclose recording.",
        bad_response_example="Don't worry about it.",
        good_response_example="This call may be recorded for quality purposes.",
        compliance_risk="privacy_violation",
    )

    json_str = note.model_dump_json()
    restored = TrainingNote.model_validate_json(json_str)

    assert restored.source == note.source
    assert restored.topic == note.topic
    assert restored.sales_lesson == note.sales_lesson
    assert restored.compliance_risk == note.compliance_risk
    assert restored.id == note.id


def test_training_note_requires_fields() -> None:
    """TrainingNote should reject missing required fields."""
    with pytest.raises(Exception):
        TrainingNote()  # type: ignore[call-arg]

    with pytest.raises(Exception):
        TrainingNote(source="x")  # type: ignore[call-arg]
