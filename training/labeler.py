"""Deterministic Transcript Labeler for Dana's training system.

Classifies turns for stage, objection type, sentiment, compliance risk, and
training usefulness without LLM or external API calls.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository
from qa.scoring import is_licensed_claim, has_you_qualify_claim


class TranscriptTurnLabel(BaseModel):
    """Deterministic labels for a single transcript turn."""

    call_stage: str
    objection_type: str
    sentiment: str
    compliance_risk: str
    is_good_example_candidate: bool
    is_failure_candidate: bool
    reasons: list[str] = Field(default_factory=list)


class LabeledTranscriptTurn(BaseModel):
    """A transcript turn complete with its labels."""

    speaker: str
    text: str
    turn_index: int
    timestamp: Optional[str] = None
    label: TranscriptTurnLabel


class TranscriptLabelingResult(BaseModel):
    """Result of labeling all turns in a transcript."""

    source_id: Optional[str] = None
    total_turns: int
    labeled_turns: int
    objection_counts: dict[str, int] = Field(default_factory=dict)
    stage_counts: dict[str, int] = Field(default_factory=dict)
    compliance_risk_counts: dict[str, int] = Field(default_factory=dict)
    good_example_candidates: int
    failure_candidates: int
    turns: list[LabeledTranscriptTurn] = Field(default_factory=list)


def classify_stage(text: str, speaker: str) -> str:
    """Classifies a turn's call stage based on deterministic keywords."""
    text_lower = text.lower()

    # DNC
    dnc_keywords = ["stop calling", "do not call", "don't call", "remove me", "take me off your list"]
    if any(k in text_lower for k in dnc_keywords):
        return "dnc"

    # Callback
    callback_keywords = ["call me later", "tomorrow", "later today", "busy", "at work", "driving", "callback", "call back"]
    if any(k in text_lower for k in callback_keywords):
        return "callback"

    # Disqualified
    disq_keywords = ["nursing home", "assisted living", "someone else handles", "not in age range", "not decision maker"]
    if any(k in text_lower for k in disq_keywords):
        return "disqualified"

    # Transfer Ready
    tr_keywords = ["stay right there", "connecting now", "bring them on", "licensed agent will", "connecting you"]
    if any(k in text_lower for k in tr_keywords):
        return "transfer_ready"

    # Transfer Consent
    tc_keywords = ["hold the line", "bring the licensed agent on", "connect", "transfer", "put them on", "go ahead", "yes connect me"]
    if any(k in text_lower for k in tc_keywords):
        return "transfer_consent"

    # Decision Maker
    dm_keywords = ["handle your own financial decisions", "spouse", "daughter", "son", "power of attorney", "poa", "someone handles", "financial decision maker"]
    if any(k in text_lower for k in dm_keywords):
        return "decision_maker"

    # Living Situation
    ls_keywords = ["living independently", "assisted living", "care facility"]
    if any(k in text_lower for k in ls_keywords):
        return "living_situation"

    # Age Range
    age_keywords = ["between forty and eighty-five", "age", "years old", "age range"]
    has_age_word = any(k in text_lower for k in age_keywords)
    has_age_num = any(re.search(rf"\b{age}\b", text_lower) for age in ["40", "85", "65", "72"])
    if has_age_word or has_age_num:
        return "age_range"

    # Interest Check
    ic_keywords = ["still open", "interested", "not interested", "already have", "looking at options", "reviewing options"]
    if any(k in text_lower for k in ic_keywords):
        return "interest_check"

    # Opening
    op_keywords = ["hello", "who is this", "who’s this", "what is this", "this is alex", "this is dana", "getting back with you", "final expense", "burial options"]
    if any(k in text_lower for k in op_keywords):
        return "opening"

    # End
    end_keywords = ["take care", "goodbye", "bye", "have a good day"]
    if any(k in text_lower for k in end_keywords):
        return "end"

    return "unknown"


def classify_objection(text: str, speaker: str) -> str:
    """Classifies prospect objections based on keyword matching."""
    if speaker != "prospect":
        return "none"

    text_lower = text.lower()

    # Hostile
    hostile_words = ["fuck", "shit", "bitch", "asshole", "cunt", "sue", "attorney", "harassment", "harassing", "complaint", "threat", "sue you"]
    if any(w in text_lower for w in hostile_words):
        return "hostile"

    # DNC
    dnc_words = ["stop calling", "do not call", "remove me", "take me off"]
    if any(w in text_lower for w in dnc_words):
        return "dnc"

    # Wrong number
    wn_words = ["wrong number", "not me", "wrong person", "don't know them", "no such person"]
    if any(w in text_lower for w in wn_words):
        return "wrong_number"

    # Not interested
    ni_words = ["not interested", "no thanks", "i'm good", "all set", "don't need it"]
    if any(w in text_lower for w in ni_words):
        return "not_interested"

    # Already insured
    ai_words = ["already have insurance", "got coverage", "have life insurance", "already covered", "have insurance", "already got", "have coverage", "already have coverage"]
    if any(w in text_lower for w in ai_words):
        return "already_insured"

    # Price question
    pq_words = ["how much", "cost", "price", "rate", "premium", "monthly", "dollars"]
    if any(w in text_lower for w in pq_words):
        return "price_question"

    # Asks company
    ac_words = ["what company", "who are you with", "company name", "what's the company"]
    if any(w in text_lower for w in ac_words):
        return "asks_company"

    # Asks how got number
    ahn_words = ["how did you get my number", "where did you get this number", "why are you calling me", "how did you get this number"]
    if any(w in text_lower for w in ahn_words):
        return "asks_how_got_number"

    # Asks if real
    air_words = ["are you real", "real person", "robot", "ai", "bot", "automated"]
    if any(w in text_lower for w in air_words):
        return "asks_if_real"

    # Asks if licensed
    ail_words = ["are you licensed", "licensed agent", "do you have a license"]
    if any(w in text_lower for w in ail_words):
        return "asks_if_licensed"

    # Busy
    busy_words = ["busy", "at work", "driving", "can't talk", "not a good time"]
    if any(w in text_lower for w in busy_words):
        return "busy"

    # Callback request
    cr_words = ["call me later", "call tomorrow", "call back", "later today"]
    if any(w in text_lower for w in cr_words):
        return "callback_request"

    # Spouse
    sp_words = ["spouse", "husband", "wife", "daughter", "son", "family handles", "talk to my spouse"]
    if any(w in text_lower for w in sp_words):
        return "spouse"

    # No money
    nm_words = ["no money", "can't afford", "broke", "fixed income", "too expensive"]
    if any(w in text_lower for w in nm_words):
        return "no_money"

    # Health concern
    hc_words = ["sick", "cancer", "heart attack", "stroke", "health problem", "medication", "hospital"]
    if any(w in text_lower for w in hc_words):
        return "health_concern"

    # Unclear
    un_words = ["huh", "what", "repeat that", "i don't understand", "confused", "say again"]
    if any(w in text_lower for w in un_words):
        return "unclear"

    return "none"


def classify_sentiment(text: str) -> str:
    """Classifies a turn's sentiment based on keywords."""
    text_lower = text.lower()

    # Hostile
    hostile_words = ["fuck", "shit", "bitch", "asshole", "cunt", "sue", "attorney", "harassment", "harassing", "complaint", "threat", "harass"]
    if any(w in text_lower for w in hostile_words):
        return "hostile"

    # Suspicious
    suspicious_words = ["scam", "how did you get my number", "who are you", "why are you calling", "why call", "fake", "real person", "robot", "ai", "bot", "are you real"]
    if any(w in text_lower for w in suspicious_words):
        return "suspicious"

    # Confused
    confused_words = ["huh", "what", "repeat", "don't understand", "confused", "what is this", "say again"]
    if any(w in text_lower for w in confused_words):
        return "confused"

    # Positive
    positive_words = ["yes", "okay", "sure", "go ahead", "interested", "sounds good"]
    if any(w in text_lower for w in positive_words):
        return "positive"

    return "neutral"


def check_compliance_risk(
    speaker: str,
    text: str,
    previous_turns: list[dict] | None = None
) -> tuple[str, list[str]]:
    """Evaluates the compliance risk level of a turn and returns reasons."""
    reasons = []
    text_lower = text.lower()

    if speaker == "prospect":
        # Check if prospect is requesting DNC, wrong number, or is hostile
        if any(w in text_lower for w in ["stop calling", "do not call", "remove me", "take me off"]):
            return "medium", ["Prospect requested DNC"]
        if any(w in text_lower for w in ["wrong number", "not me", "wrong person", "not the person"]):
            return "medium", ["Prospect reported wrong number"]
        profanities = ["fuck", "shit", "bitch", "asshole", "cunt", "sue", "attorney", "harassment", "harassing", "complaint"]
        if any(w in text_lower for w in profanities):
            return "medium", ["Prospect is hostile"]
        return "none", []

    # Speaker is agent (or unknown)
    # Check Critical risk items:
    # 1. agent claims licensed status
    if is_licensed_claim(text):
        reasons.append("Agent claimed to be licensed")

    # 2. agent says "you qualify"
    if has_you_qualify_claim(text):
        reasons.append("Agent claimed 'you qualify'")

    # 3. agent says approved/pre-approved/guaranteed
    approval_phrases = ["pre-approved", "preapproved", "guaranteed approval", "guaranteed acceptance", "guaranteed issue", "you're approved", "you are approved", "i can guarantee", "we guarantee"]
    if any(p in text_lower for p in approval_phrases):
        reasons.append("Agent promised/guaranteed approval")

    # 4. agent quotes actual price/rate/premium
    price_phrases = ["your premium will be", "your rate is", "your monthly cost", "monthly premium", "costs $", "price is"]
    has_price = False
    for phrase in price_phrases:
        if phrase in text_lower:
            has_price = True
    if "$" in text and any(word in text_lower for word in ["premium", "rate", "cost", "dollar", "monthly", "price"]):
        has_price = True

    # Match price patterns like "$50", "50 dollars", "50 a month", etc.
    price_num_pattern = re.compile(r"\$\s?\d+(?:\.\d{2})?|\b\d+\s*(?:dollars?|bucks?|a month|per month|/month)\b", re.IGNORECASE)
    if price_num_pattern.search(text_lower):
        has_price = True
    # Also support general rate/premium + numbers like "rate is 50", etc.
    rate_number_pattern = re.compile(r"\b(?:rate|premium|cost|price|monthly)\b\s*(?:is|going to be|be|around)?\s*\b\d+\b", re.IGNORECASE)
    if rate_number_pattern.search(text_lower):
        has_price = True

    if has_price:
        reasons.append("Agent quoted a price/premium")

    # 5. agent asks for sensitive info
    sensitive_terms = ["social security", "ssn", "date of birth", "dob", "bank account", "credit card", "routing number", "medicare", "payment details", "payment info", "routing", "bank info"]
    if any(term in text_lower for term in sensitive_terms):
        reasons.append("Agent asked for sensitive info")

    # 6. agent continues after DNC or wrong number
    if previous_turns:
        dnc_requested = False
        wrong_number = False
        for t in previous_turns:
            if t.get("speaker") == "prospect":
                p_text = t.get("text", "").lower()
                if any(phrase in p_text for phrase in ["do not call", "stop calling", "remove me", "take me off", "don't call"]):
                    dnc_requested = True
                if any(phrase in p_text for phrase in ["wrong number", "not me", "not the person", "no such person", "don't know who that is", "wrong person"]):
                    wrong_number = True
        if dnc_requested:
            reasons.append("Agent continued speaking after DNC request")
        if wrong_number:
            reasons.append("Agent continued speaking after wrong number indication")

    if reasons:
        return "critical", reasons

    # Check High risk items:
    # 1. agent implies approval
    implies_approval_phrases = ["likely approved", "should be approved", "probably approved", "approval shouldn't be an issue"]
    if any(p in text_lower for p in implies_approval_phrases):
        reasons.append("Agent implied approval")

    # 2. agent implies guaranteed acceptance
    if "guaranteed" in text_lower and not any(p in text_lower for p in approval_phrases):
        reasons.append("Agent mentioned guaranteed coverage or acceptance")

    # 3. agent says no medical questions
    if any(p in text_lower for p in ["no health questions", "no medical questions", "no checkups", "no physical"]):
        reasons.append("Agent claimed no medical/health questions")

    # 4. agent claims human/real person
    human_phrases = ["i'm a real person", "i am a real person", "i'm human", "i am human", "i'm a human", "yes, i am a real person", "yes i am a real person", "yes i am real", "i'm real", "i am real", "i'm not ai", "i'm not a bot", "i am not ai", "i am not a bot"]
    if any(p in text_lower for p in human_phrases):
        reasons.append("Agent claimed to be a human/real person")

    # 5. agent pushes after repeated not interested
    if previous_turns:
        not_interested_count = 0
        for t in previous_turns:
            if t.get("speaker") == "prospect":
                p_text = t.get("text", "").lower()
                if any(p in p_text for p in ["not interested", "no thanks", "i'm good", "all set", "don't need it"]):
                    not_interested_count += 1
        if not_interested_count >= 1:
            reasons.append("Agent continued pushing after disinterest")

    if reasons:
        return "high", reasons

    # Check Medium risk items:
    # 1. agent gives too much product advice
    product_advice = ["i recommend", "should get", "you need a plan like", "best plan for you"]
    if any(p in text_lower for p in product_advice):
        reasons.append("Agent gave specific policy/coverage recommendations")

    # 2. agent over-explains coverage
    if len(text.split()) > 50:
        reasons.append("Agent turn is too long / over-explains")

    # 3. agent asks multiple questions in one turn
    if text.count("?") > 1:
        reasons.append("Agent asked multiple questions in one turn")

    # 4. agent says "this will only take a minute" or similar
    minute_phrases = ["only take a minute", "just take a minute", "only a minute", "one second", "quick question"]
    if any(p in text_lower for p in minute_phrases):
        reasons.append("Agent claimed call will only take a minute")

    if reasons:
        return "medium", reasons

    # Check Low risk items:
    low_risk_phrases = ["government benefit", "state program", "federal program"]
    if any(p in text_lower for p in low_risk_phrases):
        reasons.append("Agent mentioned government program/benefit phrases")
        return "low", reasons

    return "none", []


class TranscriptLabeler:
    """Classifies turns in raw/normalized transcripts into structured training data."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository

    def label_turn(self, turn: dict, previous_turns: list[dict] | None = None) -> LabeledTranscriptTurn:
        """Labels a single transcript turn based on context and text content."""
        speaker = turn.get("speaker", "unknown")
        text = turn.get("text", "")
        turn_index = turn.get("turn_index", 0)
        timestamp = turn.get("timestamp")

        # Classify attributes
        call_stage = classify_stage(text, speaker)
        objection_type = classify_objection(text, speaker)
        sentiment = classify_sentiment(text)
        compliance_risk, reasons = check_compliance_risk(speaker, text, previous_turns)

        # Example candidate determinations
        is_good = False
        is_fail = False

        text_lower = text.lower()

        if speaker == "agent":
            word_count = len(text.split())
            has_few_questions = text.count("?") <= 1
            safe_compliance = compliance_risk in ("none", "low")

            handles_objection = False
            if previous_turns:
                prev = previous_turns[-1]
                if prev.get("speaker") == "prospect":
                    prev_obj = classify_objection(prev.get("text", ""), "prospect")
                    if prev_obj != "none":
                        handles_objection = True

            is_close = any(w in text_lower for w in ["goodbye", "have a good day", "remove", "take care"])
            is_transfer_request = any(w in text_lower for w in ["transfer", "connect", "licensed agent", "hold on"])
            is_callback_setting = any(w in text_lower for w in ["call you back", "talk later", "later today", "tomorrow"])

            if safe_compliance and has_few_questions:
                if word_count <= 40:
                    if handles_objection or is_close or is_transfer_request or is_callback_setting or word_count <= 25:
                        is_good = True

            # Failure checks
            if compliance_risk in ("medium", "high", "critical"):
                is_fail = True
            if text.count("?") > 1:
                is_fail = True
            if word_count > 60:
                is_fail = True

            if previous_turns:
                prev = previous_turns[-1]
                if prev.get("speaker") == "prospect":
                    prev_obj = classify_objection(prev.get("text", ""), "prospect")
                    if prev_obj == "not_interested":
                        if word_count > 30 and not any(w in text_lower for w in ["bye", "goodbye", "take care", "have a good"]):
                            is_fail = True

            if previous_turns:
                has_dnc_or_wn = False
                for t in previous_turns:
                    if t.get("speaker") == "prospect":
                        p_text = t.get("text", "").lower()
                        if any(phrase in p_text for phrase in ["do not call", "stop calling", "remove me", "take me off", "wrong number", "not me"]):
                            has_dnc_or_wn = True
                if has_dnc_or_wn:
                    is_fail = True

        elif speaker == "prospect":
            if sentiment in ("hostile", "confused"):
                if previous_turns and previous_turns[-1].get("speaker") == "agent":
                    is_fail = True

        label = TranscriptTurnLabel(
            call_stage=call_stage,
            objection_type=objection_type,
            sentiment=sentiment,
            compliance_risk=compliance_risk,
            is_good_example_candidate=is_good,
            is_failure_candidate=is_fail,
            reasons=reasons,
        )

        return LabeledTranscriptTurn(
            speaker=speaker,
            text=text,
            turn_index=turn_index,
            timestamp=str(timestamp) if timestamp else None,
            label=label,
        )

    def label_turns(self, turns: list[dict], source_id: str | None = None) -> TranscriptLabelingResult:
        """Labels all turns sequentially, computing totals and candidate metrics."""
        labeled_turns = []
        objection_counts = {}
        stage_counts = {}
        compliance_risk_counts = {}
        good_example_candidates = 0
        failure_candidates = 0

        previous_turns: list[dict] = []
        for turn in turns:
            labeled = self.label_turn(turn, previous_turns)
            labeled_turns.append(labeled)

            # Stage counts
            st = labeled.label.call_stage
            stage_counts[st] = stage_counts.get(st, 0) + 1

            # Objection counts (only for prospect)
            if labeled.speaker == "prospect":
                obj = labeled.label.objection_type
                objection_counts[obj] = objection_counts.get(obj, 0) + 1

            # Compliance counts
            cr = labeled.label.compliance_risk
            compliance_risk_counts[cr] = compliance_risk_counts.get(cr, 0) + 1

            # Candidate checks
            if labeled.label.is_good_example_candidate:
                good_example_candidates += 1
            if labeled.label.is_failure_candidate:
                failure_candidates += 1

            previous_turns.append(turn)

        return TranscriptLabelingResult(
            source_id=source_id,
            total_turns=len(turns),
            labeled_turns=len(labeled_turns),
            objection_counts=objection_counts,
            stage_counts=stage_counts,
            compliance_risk_counts=compliance_risk_counts,
            good_example_candidates=good_example_candidates,
            failure_candidates=failure_candidates,
            turns=labeled_turns,
        )

    async def label_training_source(self, source_id: str) -> TranscriptLabelingResult:
        """Loads a training source from repository, labels its turns, and updates metadata."""
        if self.repository is None:
            raise ValueError("Repository is required to label a training source.")

        source = await self.repository.get_training_source(source_id)
        if not source:
            raise ValueError(f"TrainingSource not found: {source_id}")

        meta = source.get("metadata") or {}
        normalized_turns = meta.get("normalized_turns", [])

        result = self.label_turns(normalized_turns, source_id=source_id)

        meta["labels"] = result.model_dump(mode="json")
        meta["labeling_version"] = "1.0.0"
        meta["labeled_at"] = datetime.now(timezone.utc).isoformat()

        await self.repository.save_training_source(
            id=source["id"],
            source_type=source["source_type"],
            source_uri=source["source_uri"],
            title=source["title"],
            imported_at=source["imported_at"],
            status=source["status"],
            metadata=meta,
        )

        return result
