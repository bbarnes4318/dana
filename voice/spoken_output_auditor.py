"""Spoken Output Auditor for final validation of TTS output.

Defines the SpokenOutputAuditor class that asserts formatting and compliance rules
to ensure the text is safe and natural for spoken delivery.
"""

from __future__ import annotations

import re
from typing import List

from voice.dialogue_style import STAGE_BREVITY_RULES


class SpokenOutputAuditor:
    """Audits final TTS strings against formatting, length, and compliance rules."""

    def audit(self, text: str, stage: str) -> List[str]:
        """Audit the response and return a list of violations (empty list means compliant)."""
        violations = []
        text_lower = text.lower()
        
        # 1. No Markdown
        markdown_patterns = [r'\*+', r'#+', r'`', r'^[-*+]\s+']
        for p in markdown_patterns:
            if re.search(p, text, flags=re.MULTILINE):
                violations.append("Markdown syntax detected")
                break
                
        # 2. No bullet points
        if "\n-" in text or "\n*" in text or text.startswith("- ") or text.startswith("* "):
            violations.append("Bullet points detected")
            
        # 3. No more than one question mark
        if text.count("?") > 1:
            violations.append(f"Too many questions: found {text.count('?')} question marks")
            
        # 4. No corporate/awkward assistant phrases
        corporate_banned = [
            "as an ai", "virtual agent", "virtual assistant", "computer program", 
            "chatbot", "artificial intelligence", "synthetic voice"
        ]
        for term in corporate_banned:
            if term in text_lower:
                violations.append(f"AI/chatbot disclosure phrase detected: '{term}'")
                
        # 5. No licensing claims (claim to be licensed)
        licensing_banned = [
            "i am licensed", "i'm licensed", "i am a licensed benefit", "i am a licensed agent"
        ]
        for term in licensing_banned:
            if term in text_lower:
                violations.append(f"Agent licensing claim detected: '{term}'")
                
        # 6. No "you qualify" promises
        qualification_banned = [
            "you qualify", "you're qualified", "you are qualified", "guaranteed to qualify"
        ]
        for term in qualification_banned:
            if term in text_lower:
                violations.append(f"Unauthorized qualification claim detected: '{term}'")
                
        # 7. No price quotes or dollar values
        if "$" in text or "dollars" in text_lower:
            violations.append("Price quote/dollar value detected")
            
        # 8. No approval claims
        approval_banned = [
            "you are approved", "you're approved", "guaranteed approval", "fully approved"
        ]
        for term in approval_banned:
            if term in text_lower:
                violations.append(f"Unauthorized approval promise detected: '{term}'")
                
        # 9. No sensitive info requests
        sensitive_banned = [
            "social security", "ssn", "routing number", "credit card", "bank account", "bank card"
        ]
        for term in sensitive_banned:
            if term in text_lower:
                violations.append(f"Sensitive information request detected: '{term}'")
                
        # 10. Max words per stage
        max_words = STAGE_BREVITY_RULES.get(stage.lower(), 30)
        word_count = len(text.split())
        if word_count > max_words:
            violations.append(f"Brevity violation: stage '{stage}' has word count {word_count} (limit is {max_words})")
            
        return violations
