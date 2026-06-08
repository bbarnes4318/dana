"""Tests verifying that main.py is free of unsafe redirect copy and forbidden compliance phrases."""

from __future__ import annotations

from pathlib import Path


def test_main_py_has_no_unsafe_redirect_copy() -> None:
    project_root = Path(__file__).resolve().parent.parent
    main_path = project_root / "main.py"
    
    assert main_path.exists(), "main.py must exist"
    
    content = main_path.read_text(encoding="utf-8")
    content_lower = content.lower()
    
    # Check that forbidden phrases/words do not appear in any production prompt context in main.py
    # Especially "you qualify", "you are approved", "guaranteed approval", etc.
    assert "you qualify" not in content_lower, "main.py contains unsafe 'you qualify' phrase"
    assert "you're qualified" not in content_lower, "main.py contains unsafe 'you're qualified' phrase"
    assert "you're approved" not in content_lower, "main.py contains unsafe 'you're approved' phrase"
    assert "guaranteed approval" not in content_lower, "main.py contains unsafe 'guaranteed approval' phrase"
    
    # Verify no weather/politics/sports redirect copy is hardcoded in main.py
    assert "weather" not in content_lower, "main.py has hardcoded weather redirect copy"
    assert "politics" not in content_lower, "main.py has hardcoded politics redirect copy"
    assert "sports" not in content_lower, "main.py has hardcoded sports redirect copy"
    assert "joke" not in content_lower, "main.py has hardcoded joke redirect copy"
    
    # Verify no old divergent-topic prompt copy remains in main.py
    assert "divergent topic" not in content_lower, "main.py has old 'divergent topic' copy"
    assert "final expense benefits" not in content_lower, "main.py has old 'final expense benefits' copy"
    assert "how old are you" not in content_lower, "main.py has old 'how old are you' redirect copy"
