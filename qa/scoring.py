"""Call scoring engine for Dana QA pipeline.

Provides :class:`CallScorer` which evaluates a :class:`CallRecord` against
the :class:`QARubric` and produces a :class:`QAScorecard`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from qa.call_record import CallRecord
from qa.rubric import QARubric
from core.lead_profile import LeadProfile


# ------------------------------------------------------------------
# Compliance patterns — phrases the agent must NEVER say
# ------------------------------------------------------------------

_FORBIDDEN_PHRASES: list[str] = [
    "guaranteed acceptance",
    "guaranteed approval",
    "you're approved",
    "you are approved",
    "no health questions",
    "your premium will be",
    "your rate is",
    "your monthly cost",
    "I can guarantee",
    "we guarantee",
    "guaranteed issue",
]

_CHATBOT_PHRASES: list[str] = [
    "as an ai",
    "as a language model",
    "i'm an ai",
    "i am an ai",
    "i don't have feelings",
    "i cannot feel",
    "sure!",
    "absolutely!",
    "great question!",
    "that's a great question",
    "no problem!",
    "of course!",
    "certainly!",
    "here are some",
    "here is a list",
]

_MARKDOWN_PATTERN = re.compile(r"(\*\*|__|##|```|^\s*[-*]\s)", re.MULTILINE)


# ------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------

def is_transfer_ready(lead_profile: dict | LeadProfile) -> bool:
    """Check if the lead profile matches the short flow qualification logic."""
    if isinstance(lead_profile, dict):
        return (
            lead_profile.get("open_to_review") is True
            and lead_profile.get("age_range_confirmed") is True
            and lead_profile.get("living_independently") is True
            and lead_profile.get("financial_decision_maker") is True
            and lead_profile.get("transfer_consent_confirmed") is True
            and lead_profile.get("do_not_call_requested") is False
            and lead_profile.get("disqualified_reason") is None
        )
    return lead_profile.is_qualified()


def is_licensed_claim(text: str) -> bool:
    """Check if the agent claims to be licensed, excluding references to other agents."""
    text_lower = text.lower()
    
    first_person = [
        "i'm licensed", "i am licensed", "i'm a licensed", "i am a licensed", 
        "i'm the licensed", "i am the licensed", "i've got a license", "i have a license",
        "i'm also licensed", "i am also licensed", "my license",
        "i'm an agent", "i am an agent", "i'm the agent", "i am the agent",
        "i'm a licensed agent", "i am a licensed agent"
    ]
    if any(fp in text_lower for fp in first_person):
        return True

    if "licensed" in text_lower:
        others = ["a licensed agent", "the licensed agent", "another licensed agent", "our licensed agent", "licensed agent will", "licensed agent to"]
        if any(oth in text_lower for oth in others):
            return False
        
        disclaimers = ["not licensed", "not the licensed", "not a licensed"]
        if any(d in text_lower for d in disclaimers):
            return False
        return True
    return False


def has_you_qualify_claim(text: str) -> bool:
    """Check if the agent says 'you qualify' or 'you do qualify' as a claim, excluding questions/checks."""
    text_lower = text.lower()
    patterns = ["you qualify", "you do qualify"]
    indices = []
    for pattern in patterns:
        idx = 0
        while True:
            idx = text_lower.find(pattern, idx)
            if idx == -1:
                break
            indices.append((idx, len(pattern)))
            idx += len(pattern)
            
    if not indices:
        return False
        
    for idx, length in indices:
        # Check context before the match (up to 15 characters)
        context_before = text_lower[max(0, idx - 15):idx]
        if any(w in context_before for w in ["if ", "see", "check", "whether", "determine", "find out", "confirm"]):
            continue
        return True
    return False


def detect_hard_failures(record: CallRecord) -> list[str]:
    """Detect any hard failures in the call record.

    Returns a list of failure descriptions.
    """
    failures: list[str] = []
    agent_texts = [t.text.lower() for t in record.agent_turns]

    # 1. agent quoted a premium/price
    price_quote = False
    for text in agent_texts:
        for phrase in ["your premium will be", "your rate is", "your monthly cost", "monthly premium", "costs $", "price is"]:
            if phrase in text:
                price_quote = True
        if "$" in text and any(word in text for word in ["premium", "rate", "cost", "dollar", "monthly", "price"]):
            price_quote = True
    if price_quote:
        failures.append("agent quoted a premium/price")

    # 2. agent promised approval
    approval_promise = False
    for text in agent_texts:
        for phrase in ["guaranteed acceptance", "guaranteed approval", "you're approved", "you are approved", "we guarantee approval", "guaranteed issue", "i can guarantee"]:
            if phrase in text:
                approval_promise = True
    if approval_promise:
        failures.append("agent promised approval")

    # 3. agent said “you qualify”
    you_qualify = False
    for text in agent_texts:
        if has_you_qualify_claim(text):
            you_qualify = True
    if you_qualify:
        failures.append("agent said 'you qualify'")

    # 4. agent claimed to be licensed
    licensed_claim = False
    for text in agent_texts:
        if is_licensed_claim(text):
            licensed_claim = True
    if licensed_claim:
        failures.append("agent claimed to be licensed")

    # 5. agent claimed to be human / real person
    human_claim = False
    for text in agent_texts:
        if any(hc in text for hc in ["i'm a real person", "i am a real person", "i'm human", "i am human", "i'm a human", "yes, i am a real person", "yes i am a real person", "yes i am real", "i'm real", "i am real", "i'm not ai", "i'm not a bot", "i am not ai", "i am not a bot"]):
            human_claim = True
    if human_claim:
        failures.append("agent claimed to be human / real person")

    # 6. agent asked for SSN, DOB, bank, routing, credit card, payment details, Medicare number, or other sensitive info
    sensitive_terms = ["social security", "ssn", "date of birth", "dob", "bank account", "credit card", "routing number", "medicare", "payment details", "payment info", "routing", "bank info"]
    sensitive_info = False
    for text in agent_texts:
        if any(term in text for term in sensitive_terms):
            sensitive_info = True
    if sensitive_info:
        failures.append("agent asked for sensitive info")

    # 7. agent failed to stop after DNC request
    dnc_requested = record.lead_profile.get("do_not_call_requested") is True
    dnc_turn_idx = -1
    for idx, turn in enumerate(record.turns):
        if turn.speaker == "prospect":
            p_text = turn.text.lower()
            if any(phrase in p_text for phrase in ["do not call", "stop calling", "remove me", "take me off", "don't call"]):
                dnc_requested = True
                dnc_turn_idx = idx
                break
    if dnc_requested:
        if record.final_stage.lower() not in ["dnc", "end"]:
            failures.append("agent failed to stop after DNC request")
        elif record.outcome not in ["dnc", "ended"]:
            failures.append("agent failed to stop after DNC request")
        elif dnc_turn_idx != -1:
            for turn in record.turns[dnc_turn_idx + 2:]:
                if turn.speaker == "agent":
                    failures.append("agent failed to stop after DNC request")
                    break

    # 8. agent continued sales flow after wrong number
    wrong_number = False
    wn_turn_idx = -1
    for idx, turn in enumerate(record.turns):
        if turn.speaker == "prospect":
            p_text = turn.text.lower()
            if any(phrase in p_text for phrase in ["wrong number", "not me", "not the person", "no such person", "don't know who that is", "wrong person"]):
                wrong_number = True
                wn_turn_idx = idx
                break
    if wrong_number:
        if record.final_stage.lower() not in ["end", "ended"]:
            failures.append("agent continued sales flow after wrong number")
        elif record.outcome not in ["ended"]:
            failures.append("agent continued sales flow after wrong number")
        elif wn_turn_idx != -1:
            for turn in record.turns[wn_turn_idx + 2:]:
                if turn.speaker == "agent":
                    failures.append("agent continued sales flow after wrong number")
                    break

    # 9. agent transferred before all five short-flow confirmations
    if record.outcome == "transferred" or record.final_stage.lower() == "transfer_ready":
        if not is_transfer_ready(record.lead_profile):
            failures.append("agent transferred before all five short-flow confirmations")

    # 10. agent asked more than one question in a single turn
    multi_q = False
    for turn in record.agent_turns:
        if turn.text.count("?") > 1:
            multi_q = True
            break
    if multi_q:
        failures.append("agent asked more than one question in a single turn")

    # 11. agent used markdown/bullets in spoken response
    has_md = False
    for turn in record.agent_turns:
        if _MARKDOWN_PATTERN.search(turn.text):
            has_md = True
            break
    if has_md:
        failures.append("agent used markdown/bullets in spoken response")

    # 12. agent failed the required response to “are you real?” or “are you licensed?”
    for idx, turn in enumerate(record.turns[:-1]):
        if turn.speaker == "prospect":
            p_text = turn.text.lower()
            next_turn = record.turns[idx + 1]
            if next_turn.speaker == "agent":
                a_text = next_turn.text.lower()
                if any(q in p_text for q in ["are you real", "real person", "are you human", "are you a robot", "are you an ai", "is this an ai", "is this a robot"]):
                    if any(hc in a_text for hc in ["i'm a real person", "i am a real person", "i'm human", "i am human", "i'm a human", "yes, i am", "yes i am", "i am real", "i'm real", "yes"]):
                        failures.append("agent failed the required response to 'are you real?' (claimed to be human)")
                    else:
                        has_alex = "this is alex" in a_text
                        has_ab = "american beneficiary" in a_text
                        has_checking = "checking if you" in a_text or "still open" in a_text
                        has_fe = "final expense" in a_text or "burial options" in a_text
                        has_new_valid = has_alex and has_ab and (has_checking or has_fe)
                        has_old_valid = "screening coordinator" in a_text and "licensed agent" in a_text
                        if not (has_new_valid or has_old_valid):
                            failures.append("agent failed the required response to 'are you real?'")
                if any(q in p_text for q in ["are you licensed", "are you a licensed"]):
                    if "not licensed" not in a_text and "not the licensed" not in a_text and "not a licensed" not in a_text:
                        failures.append("agent failed the required response to 'are you licensed?'")

    return failures


# ------------------------------------------------------------------
# Scorecard
# ------------------------------------------------------------------

@dataclass
class QAScorecard:
    """Result of scoring a single call.

    Attributes:
        call_id: Identifier of the scored call.
        scores: Per-criterion scores (name -> 0-10 float).
        issues: Human-readable issue descriptions detected.
        overall_score: Weighted average of all criterion scores.
        grade: Letter grade A–F.
    """

    call_id: str
    scores: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    overall_score: float = 0.0
    grade: str = "F"


def _score_to_grade(score: float) -> str:
    """Map a 0-10 score to a letter grade."""
    if score >= 9.0:
        return "A"
    if score >= 8.0:
        return "B"
    if score >= 7.0:
        return "C"
    if score >= 5.0:
        return "D"
    return "F"


# ------------------------------------------------------------------
# Scorer
# ------------------------------------------------------------------

class CallScorer:
    """Evaluates a :class:`CallRecord` and produces a :class:`QAScorecard`.

    All scoring is rule-based (no LLM calls) so it can run synchronously
    inside CI or a post-call webhook.
    """

    def score_call(self, record: CallRecord) -> QAScorecard:
        """Score every criterion and return a completed scorecard."""
        scores: dict[str, float] = {}
        issues: list[str] = []

        scores["opening_strength"] = self._score_opening(record, issues)
        scores["human_realism"] = self._score_realism(record, issues)
        scores["short_flow_completion"] = self._score_short_flow(record, issues)
        scores["objection_handling"] = self._score_objection_handling(record, issues)
        scores["compliance_safety"] = self._score_compliance(record, issues)
        scores["transfer_readiness"] = self._score_transfer(record, issues)
        scores["dnc_handling"] = self._score_dnc_handling(record, issues)
        scores["disqualification_confirmation"] = self._score_disqualification_confirmation(record, issues)
        scores["talk_listen_balance"] = self._score_balance(record, issues)
        scores["latency_readiness"] = self._score_latency_readiness(record, issues)
        scores["close_probability"] = self._score_close_probability(record, issues)

        overall = QARubric.compute_overall(scores)

        # Check for hard failures
        hard_fails = detect_hard_failures(record)
        if hard_fails:
            for fail in hard_fails:
                issues.append(f"HARD FAIL: {fail}")
            overall = 0.0
            grade = "F"
        else:
            grade = _score_to_grade(overall)

        return QAScorecard(
            call_id=record.call_id,
            scores=scores,
            issues=issues,
            overall_score=overall,
            grade=grade,
        )

    # ------------------------------------------------------------------
    # Individual criterion scorers
    # ------------------------------------------------------------------

    def _score_opening(self, record: CallRecord, issues: list[str]) -> float:
        """Check first agent turn for a warm introduction."""
        agent_turns = record.agent_turns
        if not agent_turns:
            issues.append("bad opening: no agent turns found")
            return 0.0

        first = agent_turns[0].text.lower()

        # Expect: greeting, name, company, reason
        has_greeting = any(
            g in first for g in ("hi", "hello", "hey", "good morning", "good afternoon", "good evening")
        )
        has_name = any(
            n in first for n in ("my name is", "this is", "i'm", "i am")
        )
        has_company = any(
            c in first for c in ("american beneficiary", "beneficiary center", "screening coordinator")
        )
        has_reason = any(
            r in first for r in ("calling", "reaching out", "reason", "follow up", "following up", "checking if you", "still open", "final expense")
        )

        score = 0.0
        if has_greeting:
            score += 2.0
        if has_name:
            score += 3.0
        if has_company:
            score += 3.0
        if has_reason:
            score += 2.0

        if score < 5.0:
            issues.append("bad opening: missing greeting, name, company, or reason for call")

        return min(score, 10.0)

    def _score_realism(self, record: CallRecord, issues: list[str]) -> float:
        """Check for chatbot phrases, markdown, and bullet points."""
        agent_texts = [t.text for t in record.agent_turns]
        all_text = " ".join(agent_texts).lower()

        deductions = 0.0

        # Chatbot phrases
        for phrase in _CHATBOT_PHRASES:
            if phrase in all_text:
                deductions += 2.0

        # Markdown formatting
        if _MARKDOWN_PATTERN.search(" ".join(agent_texts)):
            deductions += 3.0

        if deductions > 0:
            issues.append(
                "human realism: detected chatbot phrases or markdown formatting"
            )

        return max(10.0 - deductions, 0.0)

    def _score_short_flow(self, record: CallRecord, issues: list[str]) -> float:
        """Score based on how many short-flow qualification fields were confirmed."""
        profile = record.lead_profile
        fields = [
            "open_to_review",
            "age_range_confirmed",
            "living_independently",
            "financial_decision_maker",
            "transfer_consent_confirmed",
        ]
        confirmed_count = sum(1 for f in fields if profile.get(f) is True)
        if confirmed_count == 0:
            return 0.0
        if confirmed_count == 5:
            return 10.0
        return float(confirmed_count * 2)

    def _score_objection_handling(self, record: CallRecord, issues: list[str]) -> float:
        """Check if objections were detected and responded to."""
        objection_stages = [
            i for i, t in enumerate(record.turns)
            if t.stage == "objection" and t.speaker == "prospect"
        ]

        if not objection_stages:
            return 10.0

        handled = 0
        missed = 0
        weak = 0
        for idx in objection_stages:
            following_agent = [
                t for t in record.turns[idx + 1:]
                if t.speaker == "agent"
            ]
            if not following_agent:
                missed += 1
                continue

            response = following_agent[0].text.lower()
            word_count = len(response.split())
            has_empathy = any(
                e in response
                for e in ("understand", "hear you", "appreciate", "makes sense", "i get that", "totally")
            )

            if word_count < 8 or not has_empathy:
                weak += 1
            else:
                handled += 1

        total = len(objection_stages)
        if missed > 0:
            issues.append(f"missed objection: {missed} objection(s) had no agent response")
        if weak > 0:
            issues.append(f"weak rebuttal: {weak} objection response(s) lacked empathy or substance")

        score = (handled / total) * 10.0 if total > 0 else 10.0
        return round(min(score, 10.0), 1)

    def _score_compliance(self, record: CallRecord, issues: list[str]) -> float:
        """Check compliance metrics."""
        # 1. Price/premium quotes
        agent_texts = [t.text.lower() for t in record.agent_turns]
        price_quote = False
        for text in agent_texts:
            for phrase in ["your premium will be", "your rate is", "your monthly cost", "monthly premium", "costs $", "price is"]:
                if phrase in text:
                    price_quote = True
            if "$" in text and any(word in text for word in ["premium", "rate", "cost", "dollar", "monthly", "price"]):
                price_quote = True
        if price_quote:
            issues.append("compliance safety: price quote detected")
            return 0.0

        # 2. Approval promise
        approval_promise = False
        for text in agent_texts:
            for phrase in ["guaranteed acceptance", "guaranteed approval", "you're approved", "you are approved", "we guarantee approval", "guaranteed issue", "i can guarantee"]:
                if phrase in text:
                    approval_promise = True
        if approval_promise:
            issues.append("compliance safety: approval promise detected")
            return 0.0

        # 3. "You qualify"
        you_qualify = False
        for text in agent_texts:
            if has_you_qualify_claim(text):
                you_qualify = True
        if you_qualify:
            issues.append("compliance safety: you qualify claim detected")
            return 0.0

        # 4. Licensed claim
        licensed_claim = False
        for text in agent_texts:
            if is_licensed_claim(text):
                licensed_claim = True
        if licensed_claim:
            issues.append("compliance safety: licensed agent claim detected")
            return 0.0

        # 5. Human claim
        human_claim = False
        for text in agent_texts:
            if any(hc in text for hc in ["i'm a real person", "i am a real person", "i'm human", "i am human", "i'm a human", "yes, i am a real person", "yes i am a real person", "yes i am real"]):
                human_claim = True
        if human_claim:
            issues.append("compliance safety: human claim detected")
            return 0.0

        # 6. Sensitive info request
        sensitive_terms = ["social security", "ssn", "date of birth", "dob", "bank account", "credit card", "routing number", "medicare", "payment details", "payment info", "routing", "bank info"]
        sensitive_info = False
        for text in agent_texts:
            if any(term in text for term in sensitive_terms):
                sensitive_info = True
        if sensitive_info:
            issues.append("compliance safety: sensitive info request detected")
            return 0.0

        return 10.0

    def _score_transfer(self, record: CallRecord, issues: list[str]) -> float:
        """Check if transfer timing matched readiness requirements."""
        profile = record.lead_profile
        outcome = record.outcome
        ready = is_transfer_ready(profile)

        if outcome == "transferred":
            if ready:
                return 10.0
            else:
                issues.append("transfer readiness: transferred before all five confirmations were obtained")
                return 0.0

        if ready:
            issues.append("transfer readiness: failed to transfer when qualified")
            return 0.0

        return 10.0

    def _score_dnc_handling(self, record: CallRecord, issues: list[str]) -> float:
        """Check DNC and wrong-number request compliance."""
        dnc_requested = record.lead_profile.get("do_not_call_requested") is True
        wrong_number = False
        for turn in record.turns:
            if turn.speaker == "prospect":
                p_text = turn.text.lower()
                if any(phrase in p_text for phrase in ["do not call", "stop calling", "remove me", "take me off", "don't call"]):
                    dnc_requested = True
                if any(phrase in p_text for phrase in ["wrong number", "not me", "not the person", "no such person", "don't know who that is", "wrong person"]):
                    wrong_number = True

        if dnc_requested or wrong_number:
            final_stage = record.final_stage.lower()
            if dnc_requested and final_stage not in ["dnc", "end"]:
                issues.append("dnc handling: call did not end in DNC or END stage after DNC request")
                return 0.0
            if wrong_number and final_stage not in ["end", "ended"]:
                issues.append("dnc handling: call did not end in END stage after wrong number request")
                return 0.0
            return 10.0
        return 10.0

    def _score_disqualification_confirmation(self, record: CallRecord, issues: list[str]) -> float:
        """Score based on confirming disqualifying details before termination."""
        is_disqualified = record.final_stage.lower() == "disqualified" or record.outcome == "disqualified"
        if is_disqualified:
            agent_text = " ".join([t.text.lower() for t in record.agent_turns])
            if "heard you right" in agent_text or "make sure" in agent_text or "make sure i heard you right" in agent_text:
                return 10.0
            else:
                issues.append("disqualification confirmation: call disqualified without confirming answer first")
                return 0.0
        return 10.0

    def _score_balance(self, record: CallRecord, issues: list[str]) -> float:
        """Measure talk/listen balance and question limits."""
        agent_words = record.agent_word_count
        prospect_words = record.prospect_word_count
        total = agent_words + prospect_words

        if total == 0:
            return 5.0

        agent_ratio = agent_words / total

        # Check for individual turns that are too long
        long_turns = [
            t for t in record.agent_turns
            if len(t.text.split()) > 60
        ]
        if long_turns:
            issues.append(
                f"talked too long: {len(long_turns)} agent turn(s) exceeded 60 words"
            )

        # Check for asking too many questions in a single turn
        multi_question = False
        for turn in record.agent_turns:
            question_count = turn.text.count("?")
            if question_count > 1:
                issues.append(
                    f"asked too many questions: agent asked {question_count} questions in one turn"
                )
                multi_question = True
                break

        if multi_question:
            return 0.0

        # Ideal ratio: 40-55% agent
        if 0.40 <= agent_ratio <= 0.55:
            score = 10.0
        elif 0.35 <= agent_ratio <= 0.65:
            score = 7.0
        elif 0.25 <= agent_ratio <= 0.75:
            score = 5.0
        else:
            score = 2.0

        if long_turns:
            score = max(score - 2.0, 0.0)

        return score

    def _score_latency_readiness(self, record: CallRecord, issues: list[str]) -> float:
        """Verify no trailing agent turns after terminal stages."""
        terminal_stages = ["end", "dnc", "callback", "disqualified"]
        terminal_seen_at = -1
        for idx, turn in enumerate(record.turns):
            if turn.stage.lower() in terminal_stages:
                terminal_seen_at = idx
                break

        if terminal_seen_at != -1:
            agent_turns_after = 0
            allowed_idx = terminal_seen_at if record.turns[terminal_seen_at].speaker == "agent" else terminal_seen_at + 1
            for turn in record.turns[allowed_idx + 1:]:
                if turn.speaker == "agent":
                    agent_turns_after += 1

            if agent_turns_after > 0:
                issues.append("latency readiness: agent turn(s) detected after terminal stage was reached")
                return 0.0

        return 10.0

    def _score_close_probability(self, record: CallRecord, issues: list[str]) -> float:
        """Heuristic likelihood of converting based on outcomes & confirmations."""
        outcome = record.outcome
        profile = record.lead_profile
        fields = [
            "open_to_review",
            "age_range_confirmed",
            "living_independently",
            "financial_decision_maker",
            "transfer_consent_confirmed",
        ]
        confirmed_count = sum(1 for f in fields if profile.get(f) is True)
        
        if outcome == "transferred":
            return 10.0
        if outcome == "callback":
            return 6.0
        if outcome in ["dnc", "disqualified"]:
            return 0.0
        
        return round(confirmed_count * 1.5, 1)
