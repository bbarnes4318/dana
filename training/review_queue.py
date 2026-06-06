"""CLI review queue for suggested training notes.

Supports listing pending notes, approving them with compliance validation, and rejecting them.
"""

from __future__ import annotations

import asyncio
import sys
from typing import List, Optional

from storage.repository import Repository
from safety.compliance_filter import ComplianceFilter
from qa.scoring import is_licensed_claim, has_you_qualify_claim


def check_lesson_compliance(sales_lesson: str, good_example: str) -> List[str]:
    """Check sales lesson and good response example for compliance violations.

    Returns a list of violation messages. If empty, the lesson is compliant.
    """
    violations = []

    # 1. ComplianceFilter checks
    cf = ComplianceFilter()
    for name, text in [("sales_lesson", sales_lesson), ("good_example", good_example)]:
        if not text:
            continue
        res = cf.check(text)
        if not res.is_safe:
            for viol in res.violations:
                violations.append(f"[{name}] {viol}")

        # 2. Licensing claims
        if is_licensed_claim(text):
            violations.append(f"[{name}] AI must not claim to be a licensed agent")

        # 3. You qualify claims
        if has_you_qualify_claim(text):
            violations.append(f"[{name}] AI must not quote qualification or say 'you qualify' without checking context")

        # 4. Premium/price quotes
        text_lower = text.lower()
        price_quote = False
        for phrase in ["your premium will be", "your rate is", "your monthly cost", "monthly premium", "costs $", "price is"]:
            if phrase in text_lower:
                price_quote = True
        if "$" in text_lower and any(word in text_lower for word in ["premium", "rate", "cost", "dollar", "monthly", "price"]):
            price_quote = True
        if price_quote:
            violations.append(f"[{name}] AI must not quote premiums or monthly cost details")

        # 5. Human claims
        if any(hc in text_lower for hc in ["i'm a real person", "i am a real person", "i'm human", "i am human", "i'm a human", "yes, i am a real person", "yes i am a real person", "yes i am real", "i'm real", "i am real", "i'm not ai", "i'm not a bot", "i am not ai", "i am not a bot"]):
            violations.append(f"[{name}] AI must not claim to be human or a real person")

        # 6. Sensitive information requests
        sensitive_terms = ["social security", "ssn", "date of birth", "dob", "bank account", "credit card", "routing number", "medicare", "payment details", "payment info", "routing", "bank info"]
        if any(term in text_lower for term in sensitive_terms):
            violations.append(f"[{name}] AI must not ask for sensitive information like SSN, DOB, or bank info")

    return violations


async def list_pending():
    repo = Repository()
    notes = await repo.query_training_notes({"status": "pending_review"})
    if not notes:
        print("No pending training notes in the review queue.")
        return
    print(f"--- Pending Review Queue ({len(notes)} items) ---")
    for note in notes:
        note_id = note.get("id")
        topic = note.get("topic")
        lesson = note.get("sales_lesson")
        source = note.get("source")
        print(f"ID: {note_id} | Topic: {topic} | Source: {source}")
        print(f"  Lesson: {lesson[:100]}...")
        print(f"  Good Example: {note.get('good_response_example') or note.get('good_example')}")
        print("-" * 40)


async def approve_note(note_id: str):
    repo = Repository()
    note = await repo.get_training_note(note_id)
    if not note:
        print(f"Error: Training note {note_id} not found.", file=sys.stderr)
        sys.exit(1)

    sales_lesson = note.get("sales_lesson", "")
    good_example = note.get("good_response_example") or note.get("good_example") or ""

    violations = check_lesson_compliance(sales_lesson, good_example)
    if violations:
        print(f"Error: Cannot approve note {note_id} due to compliance violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        sys.exit(1)

    note["status"] = "approved"
    note["use_in_live_call"] = True
    await repo.save_training_note(**note)
    print(f"Successfully approved training note {note_id}.")


async def reject_note(note_id: str):
    repo = Repository()
    note = await repo.get_training_note(note_id)
    if not note:
        print(f"Error: Training note {note_id} not found.", file=sys.stderr)
        sys.exit(1)

    note["status"] = "rejected"
    note["use_in_live_call"] = False
    await repo.save_training_note(**note)
    print(f"Successfully rejected training note {note_id}.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m training.review_queue <list|approve|reject> [id]")
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "list":
        asyncio.run(list_pending())
    elif action == "approve":
        if len(sys.argv) < 3:
            print("Error: approve command requires note ID argument.")
            sys.exit(1)
        asyncio.run(approve_note(sys.argv[2]))
    elif action == "reject":
        if len(sys.argv) < 3:
            print("Error: reject command requires note ID argument.")
            sys.exit(1)
        asyncio.run(reject_note(sys.argv[2]))
    else:
        print(f"Unknown command: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
