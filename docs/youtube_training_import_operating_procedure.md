# YouTube Training Import Operating Procedure

This document describes how to safely import local YouTube transcript files or lists of transcript texts into Dana's continuous training pipeline.

## Overview

The YouTube transcript importer processes local transcript files or manifest JSON files and outputs front-matter-annotated documents into `data/imports/youtube_training/`.

> [!WARNING]
> **No Web Scraping**: The YouTube importer does *not* call YouTube APIs, scrape YouTube pages, or run external network calls. All transcript texts must be supplied locally. URLs are preserved as metadata only.

## JSON Manifest Schema

To bulk import a list of transcript files or inline text, create a manifest JSON file:

```json
{
  "videos": [
    {
      "url": "https://www.youtube.com/watch?v=objection_handling",
      "title": "American Beneficiary Objection Training",
      "transcript": "Agent: Hello. Prospect: Is this insurance? Agent: Yes, it is..."
    },
    {
      "url": "https://www.youtube.com/watch?v=closing_strategies",
      "title": "Closing objection guide",
      "transcript_file": "relative/path/to/transcript.txt"
    }
  ]
}
```

## CLI Import Command

To import transcripts using a manifest JSON file:
```bash
python scripts/import_youtube_transcripts.py --manifest path/to/manifest.json
```

To import a single transcript text file with title and URL overrides:
```bash
python scripts/import_youtube_transcripts.py --file path/to/transcript.txt --title "Objection Strategy Video" --source-url "https://www.youtube.com/watch?v=obj1"
```

To automatically launch the intake orchestrator on the imported files, append `--run-intake`:
```bash
python scripts/import_youtube_transcripts.py --manifest manifest.json --run-intake
```

## Security Safeguards
- **Metadata URL storage only**: YouTube URLs are stored in front-matter YAML headings without network lookups.
- **Offline parser**: Runs entirely local. No external API calls to OpenAI or other providers.
- **Review gate queue**: Imported materials are fed into the training queue in `pending` review status only.
