"""Tests for core.lead_profile.LeadProfile."""

from __future__ import annotations

import pytest

from core.lead_profile import LeadProfile


class TestDefaultValues:
    def test_default_values(self) -> None:
        lp = LeadProfile()
        assert lp.first_name is None
        assert lp.last_name is None
        assert lp.age is None
        assert lp.state is None
        assert lp.phone_type is None
        assert lp.can_receive_text is None
        assert lp.budget_confirmed is None
        assert lp.has_existing_coverage is None
        assert lp.beneficiary_or_family_reason is None
        assert lp.interest_level is None
        assert lp.disqualified_reason is None
        assert lp.callback_requested is None
        assert lp.do_not_call_requested is False
        assert lp.transfer_ready is False
        assert lp.notes == []
        assert isinstance(lp.call_id, str)
        assert len(lp.call_id) > 0


class TestIsQualified:
    def test_is_qualified(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            age_range_confirmed=True,
            living_independently=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=True,
        )
        assert lp.is_qualified() is True

    def test_not_qualified_missing_transfer_consent_confirmed(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            age_range_confirmed=True,
            living_independently=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=False,
        )
        assert lp.is_qualified() is False

    def test_not_qualified_missing_age(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            living_independently=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=True,
        )
        assert lp.is_qualified() is False

    def test_not_qualified_missing_living_situation(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            age_range_confirmed=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=True,
        )
        assert lp.is_qualified() is False

    def test_not_qualified_missing_decision_maker(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            age_range_confirmed=True,
            living_independently=True,
            transfer_consent_confirmed=True,
        )
        assert lp.is_qualified() is False


class TestCompletenessScore:
    def test_completeness_score(self) -> None:
        lp = LeadProfile()
        assert lp.completeness_score() == 0.0

    def test_completeness_score_partial(self) -> None:
        lp = LeadProfile(open_to_review=True, age_range_confirmed=True)
        score = lp.completeness_score()
        assert 0.0 < score < 1.0

    def test_completeness_score_full(self) -> None:
        lp = LeadProfile(
            lead_id="123",
            lead_phone_e164="+15551234567",
            campaign_id="abc",
            open_to_review=True,
            age_range_confirmed=True,
            living_independently=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=True,
        )
        assert lp.completeness_score() == 1.0


class TestDNCBlocksQualification:
    def test_dnc_blocks_qualification(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            age_range_confirmed=True,
            living_independently=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=True,
            do_not_call_requested=True,
        )
        assert lp.is_qualified() is False

    def test_disqualified_reason_blocks_qualification(self) -> None:
        lp = LeadProfile(
            open_to_review=True,
            age_range_confirmed=True,
            living_independently=True,
            financial_decision_maker=True,
            transfer_consent_confirmed=True,
            disqualified_reason="Age outside range",
        )
        assert lp.is_qualified() is False


class TestToSummaryDict:
    def test_to_summary_dict_keys(self) -> None:
        lp = LeadProfile(first_name="Jane", open_to_review=True, age_range_confirmed=True)
        summary = lp.to_summary_dict()
        assert summary["name"] == "Jane"
        assert summary["open_to_review"] is True
        assert summary["age_range_confirmed"] is True
        assert "call_id" in summary
        assert "is_qualified" in summary
        assert "completeness" in summary

