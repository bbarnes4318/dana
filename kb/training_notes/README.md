# Training Notes

This directory contains training notes used to improve Dana's knowledge and responses.

## How to Add Training Notes

1. **Create a new Markdown file** in this directory (e.g., `my_notes.md`).
2. **Use headings** to organize topics. Each `##` heading creates a separate chunk in the RAG index.
3. **Be specific** — write notes as if you're coaching a human agent. Dana will use these to inform her responses.
4. **Tag call stages** in headings when relevant (e.g., `## Opening — Warm Greeting Tips`). The ingestion pipeline detects stage names automatically.
5. **Run the index builder** after adding notes:

   ```bash
   python training/build_index.py
   ```

## File Format

- Use `.md` (Markdown) files for best chunking results.
- Keep each section under 500 characters for optimal retrieval.
- Use bullet points for quick-reference items.

## Generated Notes

The `generated/` subdirectory contains auto-generated training notes from call analysis. Do not edit files in `generated/` — they will be overwritten.

## Examples

```markdown
## Objection — "I already have insurance"

Many prospects already have some coverage but it may not be enough for final expenses.
Ask: "That's great that you've planned ahead! Is your current policy specifically
designed to cover your final expenses, or is it more of a general life insurance policy?"

## Budget — Affordability Framing

Always frame the cost in daily terms: "Most of our plans work out to less than
a dollar a day — about the cost of a cup of coffee."
```
