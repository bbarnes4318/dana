"""Ingest and clean video transcript files (.txt, .vtt, .srt, .md).

Reads raw transcript files, strips timestamps / cue IDs / sequence numbers,
removes duplicate consecutive captions, and normalises whitespace.  Returns a
list of cleaned paragraphs ready for lesson extraction.

CLI usage::

    python training/ingest_video_transcript.py <filepath>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".vtt", ".srt", ".md"}


class VideoTranscriptIngestor:
    """Cleans and ingests video transcript files into normalised paragraphs."""

    # ---- format-specific cleaners ----------------------------------------

    @staticmethod
    def clean_vtt(text: str) -> str:
        """Remove WEBVTT header, timestamps, and cue IDs from VTT content.

        Args:
            text: Raw VTT file content.

        Returns:
            Cleaned plain-text with captions only.
        """
        # Remove WEBVTT header block (header line + metadata lines up to first blank line)
        text = re.sub(r"^WEBVTT[^\n]*\n(?:[^\n\r]+\n)*", "", text, flags=re.MULTILINE)
        # Remove optional cue IDs (standalone lines of digits or identifiers before timestamps)
        text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
        # Remove timestamp lines  00:00:00.000 --> 00:00:05.000  (with optional positioning)
        text = re.sub(
            r"^\d{2}:\d{2}[:\.][\d.]+ --> \d{2}:\d{2}[:\.][\d.]+.*$",
            "",
            text,
            flags=re.MULTILINE,
        )
        # Remove NOTE blocks (VTT comments)
        text = re.sub(r"^NOTE\b.*?(?=\n\n|\Z)", "", text, flags=re.MULTILINE | re.DOTALL)
        return text.strip()

    @staticmethod
    def clean_srt(text: str) -> str:
        """Remove sequence numbers and timestamps from SRT content.

        Args:
            text: Raw SRT file content.

        Returns:
            Cleaned plain-text with captions only.
        """
        # Remove sequence numbers (standalone digits at the start of a line)
        text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
        # Remove SRT timestamp lines  00:00:00,000 --> 00:00:05,000
        text = re.sub(
            r"^\d{2}:\d{2}:\d{2}[,\.]\d{3} --> \d{2}:\d{2}:\d{2}[,\.]\d{3}.*$",
            "",
            text,
            flags=re.MULTILINE,
        )
        return text.strip()

    @staticmethod
    def clean_transcript(text: str, fmt: str) -> str:
        """Clean a transcript based on its format, then normalise.

        Args:
            text: Raw file content.
            fmt: File extension including the dot (e.g. ``'.vtt'``).

        Returns:
            Normalised plain-text string.
        """
        fmt = fmt.lower()
        if fmt == ".vtt":
            text = VideoTranscriptIngestor.clean_vtt(text)
        elif fmt == ".srt":
            text = VideoTranscriptIngestor.clean_srt(text)
        # For .txt and .md we keep the raw text as-is before normalising.

        # --- shared normalisation ---
        # Remove duplicate consecutive lines (common in subtitle files)
        lines = text.splitlines()
        deduped: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and (not deduped or stripped != deduped[-1]):
                deduped.append(stripped)
            elif not stripped:
                # Preserve blank lines as paragraph separators
                deduped.append("")
        text = "\n".join(deduped)

        # Collapse runs of 3+ newlines into double-newlines (paragraph breaks)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Normalise internal whitespace on each line
        text = re.sub(r"[ \t]+", " ", text)

        return text.strip()

    # ---- file ingestion --------------------------------------------------

    def ingest_file(self, filepath: str | Path) -> list[str]:
        """Read a transcript file, clean it, and split into paragraphs.

        Args:
            filepath: Path to the transcript file.

        Returns:
            List of non-empty cleaned paragraphs.

        Raises:
            FileNotFoundError: If *filepath* does not exist.
            ValueError: If the file extension is not supported.
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Transcript file not found: {path}")

        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension '{ext}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        raw = path.read_text(encoding="utf-8")
        cleaned = self.clean_transcript(raw, ext)

        # Split into paragraphs on double-newlines
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", cleaned)]
        return [p for p in paragraphs if p]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry-point: ingest a transcript file and print cleaned paragraphs."""
    if len(sys.argv) < 2:
        print("Usage: python training/ingest_video_transcript.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    ingestor = VideoTranscriptIngestor()

    try:
        paragraphs = ingestor.ingest_file(filepath)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"--- Ingested {len(paragraphs)} paragraphs from {filepath} ---\n")
    for i, para in enumerate(paragraphs, 1):
        print(f"[{i}] {para}\n")


if __name__ == "__main__":
    main()
