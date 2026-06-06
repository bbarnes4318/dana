"""Tests to guarantee that topic redirect policy responses comply with safety and phrasing limits."""

from __future__ import annotations

import pytest
from core.call_state import CallStage
from safety.topic_redirect_policy import TopicRedirectPolicy


def test_topic_redirect_responses_have_no_forbidden_phrases() -> None:
    policy = TopicRedirectPolicy()

    # List of forbidden phrases/words
    forbidden_terms = [
        "qualify",
        "approved",
        "approval",
        "government",
        "benefit entitlement",
        "guaranteed",
        "licensed agent",
        "price",
        "premium",
        "dollar",
        "$",
        "rates",
    ]

    # Test stage responses
    for stage in CallStage:
        resp = policy.get_redirect_response(stage).lower()
        for term in forbidden_terms:
            assert term not in resp, f"Stage {stage} response '{resp}' contains forbidden term '{term}'"

    # Test default response
    default_resp = policy.default_redirect.lower()
    for term in forbidden_terms:
        assert term not in default_resp, f"Default response '{default_resp}' contains forbidden term '{term}'"


def test_topic_redirect_responses_are_exactly_one_sentence() -> None:
    policy = TopicRedirectPolicy()

    for stage in CallStage:
        resp = policy.get_redirect_response(stage).strip()
        
        # Check that it ends with sentence punctuation (. ? !)
        assert resp[-1] in (".", "?", "!"), f"Stage {stage} response must end with punctuation: {resp}"
        
        # Check that there is exactly one sentence terminator
        terminator_count = resp.count(".") + resp.count("?") + resp.count("!")
        assert terminator_count == 1, f"Stage {stage} response has {terminator_count} sentence terminators (must be exactly 1): {resp}"
        
        # Check length is reasonable (e.g. at least 5 words and less than 30 words)
        words = resp.split()
        assert len(words) >= 5, f"Stage {stage} response is too short: {resp}"
        assert len(words) <= 30, f"Stage {stage} response is too long: {resp}"
