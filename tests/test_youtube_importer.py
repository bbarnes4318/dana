import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from storage.repository import Repository
from training.youtube_importer import YouTubeTranscriptImporter, YouTubeTranscriptImportConfig


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(data_dir=tmp_path)


@pytest.fixture
def importer(repo: Repository) -> YouTubeTranscriptImporter:
    return YouTubeTranscriptImporter(repository=repo)


# 1. test_import_local_transcript_file
@pytest.mark.asyncio
async def test_import_local_transcript_file(importer: YouTubeTranscriptImporter, tmp_path: Path):
    txt_file = tmp_path / "yt_raw.txt"
    txt_file.write_text("Hello video", encoding="utf-8")
    
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_file=str(txt_file),
        title="Objection Training 1"
    )
    
    res = await importer.import_transcripts(config)
    assert res.imported_count == 1
    assert res.failed_count == 0
    assert Path(res.item_results[0].output_path).exists()
    
    content = Path(res.item_results[0].output_path).read_text(encoding="utf-8")
    assert "source_type: youtube" in content
    assert "Objection Training 1" in content
    assert "Hello video" in content


# 2. test_import_raw_transcript_text
@pytest.mark.asyncio
async def test_import_raw_transcript_text(importer: YouTubeTranscriptImporter, tmp_path: Path):
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="Objection strategy detail content",
        title="Strategy Objections"
    )
    
    res = await importer.import_transcripts(config)
    assert res.imported_count == 1
    assert Path(res.item_results[0].output_path).exists()


# 3. test_import_manifest_with_inline_transcript
@pytest.mark.asyncio
async def test_import_manifest_with_inline_transcript(importer: YouTubeTranscriptImporter, tmp_path: Path):
    manifest_data = {
        "videos": [
            {
                "url": "https://www.youtube.com/watch?v=1",
                "title": "Video 1",
                "transcript": "Hello inline turn text"
            }
        ]
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        manifest_path=str(manifest_file)
    )
    
    res = await importer.import_transcripts(config)
    assert res.imported_count == 1
    assert "Hello inline turn" in Path(res.item_results[0].output_path).read_text(encoding="utf-8")


# 4. test_import_manifest_with_transcript_file
@pytest.mark.asyncio
async def test_import_manifest_with_transcript_file(importer: YouTubeTranscriptImporter, tmp_path: Path):
    txt_file = tmp_path / "video_text.txt"
    txt_file.write_text("Hello from manifest file", encoding="utf-8")
    
    manifest_data = {
        "videos": [
            {
                "url": "https://www.youtube.com/watch?v=2",
                "title": "Video 2",
                "transcript_file": str(txt_file)
            }
        ]
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        manifest_path=str(manifest_file)
    )
    
    res = await importer.import_transcripts(config)
    assert res.imported_count == 1
    assert "Hello from manifest file" in Path(res.item_results[0].output_path).read_text(encoding="utf-8")


# 5. test_empty_transcript_fails
@pytest.mark.asyncio
async def test_empty_transcript_fails(importer: YouTubeTranscriptImporter, tmp_path: Path):
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="",
        title="Empty Video"
    )
    
    res = await importer.import_transcripts(config)
    assert res.failed_count == 1
    assert res.imported_count == 0


# 6. test_url_is_metadata_only_no_network
@pytest.mark.asyncio
async def test_url_is_metadata_only_no_network(importer: YouTubeTranscriptImporter, tmp_path: Path):
    out_dir = tmp_path / "imported"
    url = "https://www.youtube.com/watch?v=mock_network_test"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="Objection strategy",
        title="Metadata Only Test",
        source_url=url
    )
    
    # We patch requests or httpx to fail if any network is called, ensuring 100% offline
    with patch("urllib.request.urlopen") as mock_open, patch("urllib.request.Request") as mock_req:
        res = await importer.import_transcripts(config)
        assert res.imported_count == 1
        assert not mock_open.called
        assert not mock_req.called


# 7. test_sanitizes_filename
def test_sanitizes_filename(importer: YouTubeTranscriptImporter):
    title = "Hello! Video: Objection (2026) - Handling"
    safe = importer.sanitize_title_to_filename(title)
    assert safe == "hello_video_objection_2026_-_handling"


# 8. test_front_matter_written
def test_front_matter_written(importer: YouTubeTranscriptImporter):
    doc = importer.build_transcript_document(
        title="Objection Handling 101",
        transcript_text="Agent: Hello",
        source_url="https://youtube/123",
        metadata={"coach": "John Doe"}
    )
    assert "source_type: youtube" in doc
    assert "title: Objection Handling 101" in doc
    assert "source_url: https://youtube/123" in doc
    assert "coach: John Doe" in doc


# 9. test_run_intake_after_import_optional
@pytest.mark.asyncio
async def test_run_intake_after_import_optional(repo: Repository, tmp_path: Path):
    mock_orch = MagicMock()
    mock_orch.run = AsyncMock()
    
    importer = YouTubeTranscriptImporter(repository=repo, orchestrator=mock_orch)
    
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="Objection Handling Strategy Video details text",
        title="Objection Training 9",
        run_intake_after_import=True
    )
    
    await importer.import_transcripts(config)
    assert mock_orch.run.called


# 10. test_dry_run_does_not_write_file
@pytest.mark.asyncio
async def test_dry_run_does_not_write_file(importer: YouTubeTranscriptImporter, tmp_path: Path):
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="Content",
        title="Dry Run Test",
        dry_run=True
    )
    
    res = await importer.import_transcripts(config)
    assert res.imported_count == 1
    assert not Path(res.item_results[0].output_path).exists()


# 11. test_report_files_written
@pytest.mark.asyncio
async def test_report_files_written(importer: YouTubeTranscriptImporter, tmp_path: Path):
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="Objection text",
        title="Report Test"
    )
    
    res = await importer.import_transcripts(config)
    assert res.report_json_path is not None
    assert res.report_markdown_path is not None
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()


# 12. test_no_external_api_calls
def test_no_external_api_calls():
    with open("training/youtube_importer.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "import requests" not in content
    assert "import httpx" not in content


# 13. test_no_auto_approval
@pytest.mark.asyncio
async def test_no_auto_approval(repo: Repository, tmp_path: Path):
    # If run_intake is called, check that any review items generated are pending
    out_dir = tmp_path / "imported"
    config = YouTubeTranscriptImportConfig(
        output_dir=str(out_dir),
        transcript_text="Agent: Hello, this is Alex. DNC trigger",
        title="DNC Objection Training Video Example",
        run_intake_after_import=True
    )
    
    # We construct a real orchestrator to see it flow to review item
    from training.intake_orchestrator import TrainingIntakeOrchestrator
    orchestrator = TrainingIntakeOrchestrator(repository=repo)
    
    importer = YouTubeTranscriptImporter(repository=repo, orchestrator=orchestrator)
    await importer.import_transcripts(config)
    
    # Check that any human review items are pending, not approved
    items = await repo.query_human_review_items({})
    for item in items:
        assert item["status"] == "pending"
        assert item["reviewer"] is None
