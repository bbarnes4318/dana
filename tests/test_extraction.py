"""Tests for core.extraction utilities."""

from __future__ import annotations

import pytest

from core.extraction import (
    detect_callback_request,
    detect_dnc_request,
    extract_age,
    extract_name,
    extract_phone_type,
    extract_state,
    extract_yes_no,
)


class TestExtractAge:
    def test_extract_age_from_various_formats(self) -> None:
        assert extract_age("I'm 67 years old") == 67
        assert extract_age("I am 72") == 72
        assert extract_age("age 55") == 55
        assert extract_age("65") == 65
        assert extract_age("sixty seven") == 67
        assert extract_age("seventy-two") == 72
        assert extract_age("fifty") == 50

    def test_extract_age_out_of_range(self) -> None:
        assert extract_age("I'm 10") is None
        assert extract_age("I'm 5") is None

    def test_extract_age_no_match(self) -> None:
        assert extract_age("hello there") is None
        assert extract_age("") is None


class TestExtractStateAbbreviation:
    def test_extract_state_abbreviation(self) -> None:
        assert extract_state("FL") == "FL"
        assert extract_state("I live in TX") == "TX"
        assert extract_state("NY") == "NY"
        assert extract_state("CA") == "CA"


class TestExtractStateFullName:
    def test_extract_state_full_name(self) -> None:
        assert extract_state("Florida") == "FL"
        assert extract_state("I'm in North Carolina") == "NC"
        assert extract_state("texas") == "TX"
        assert extract_state("New York") == "NY"
        assert extract_state("west virginia") == "WV"

    def test_extract_state_no_match(self) -> None:
        assert extract_state("hello") is None
        assert extract_state("") is None


class TestDetectDNCPhrases:
    def test_detect_dnc_phrases(self) -> None:
        assert detect_dnc_request("Please do not call me again") is True
        assert detect_dnc_request("stop calling me") is True
        assert detect_dnc_request("remove me from your list") is True
        assert detect_dnc_request("put me on the do not call list") is True
        assert detect_dnc_request("take me off your list") is True

    def test_no_dnc(self) -> None:
        assert detect_dnc_request("yes I'm interested") is False
        assert detect_dnc_request("tell me more") is False
        assert detect_dnc_request("") is False


class TestExtractYesNo:
    def test_extract_yes_no(self) -> None:
        assert extract_yes_no("yes") is True
        assert extract_yes_no("Yeah sure") is True
        assert extract_yes_no("absolutely") is True
        assert extract_yes_no("yep") is True
        assert extract_yes_no("definitely") is True

        assert extract_yes_no("no") is False
        assert extract_yes_no("nope") is False
        assert extract_yes_no("not interested") is False
        assert extract_yes_no("no thanks") is False

    def test_extract_yes_no_ambiguous(self) -> None:
        assert extract_yes_no("I don't know") is None
        assert extract_yes_no("hmm") is None


class TestExtractPhoneType:
    def test_extract_phone_type(self) -> None:
        assert extract_phone_type("I'm on my cell phone") == "cell"
        assert extract_phone_type("this is my mobile") == "cell"
        assert extract_phone_type("I have an iPhone") == "cell"
        assert extract_phone_type("this is a landline") == "landline"
        assert extract_phone_type("my home phone") == "landline"

    def test_extract_phone_type_no_match(self) -> None:
        assert extract_phone_type("I'm at work") is None
        assert extract_phone_type("") is None


class TestExtractName:
    def test_extract_name(self) -> None:
        assert extract_name("My name is John") == "John"
        assert extract_name("I'm Sarah") == "Sarah"
        assert extract_name("This is Mike") == "Mike"
        assert extract_name("Call me Bob") == "Bob"

    def test_extract_name_no_match(self) -> None:
        assert extract_name("hello there how are you") is None
        assert extract_name("") is None


class TestDetectCallback:
    def test_detect_callback(self) -> None:
        assert detect_callback_request("can you call me back later") is True
        assert detect_callback_request("not a good time") is True
        assert detect_callback_request("call me tomorrow") is True

    def test_no_callback(self) -> None:
        assert detect_callback_request("yes I have time") is False
