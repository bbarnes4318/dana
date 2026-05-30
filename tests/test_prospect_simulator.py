from __future__ import annotations

import os
import json
import pytest
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulations.prospect_simulator import (
    ProspectSimulator,
    SimulationRunner,
    StaticDanaResponseProvider,
    RuntimeDanaResponseProvider,
    SimulationScenario,
    SimulationResult,
    SimulatedTurn,
    validate_simulation,
)

# ------------------------------------------------------------------
# Test cases
# ------------------------------------------------------------------

# 1. test_default_personas_exist
def test_default_personas_exist() -> None:
    """Assert all 14 required personas exist."""
    sim = ProspectSimulator()
    personas = sim.get_default_personas()
    assert len(personas) == 14
    
    expected_ids = {
        "confused_senior",
        "suspicious_prospect",
        "busy_worker",
        "spouse_handles_money",
        "already_covered",
        "price_shopper",
        "callback_requester",
        "hostile_dnc",
        "wrong_number",
        "interested_quiet",
        "asks_if_real",
        "asks_if_licensed",
        "nursing_home",
        "not_decision_maker",
    }
    actual_ids = {p.id for p in personas}
    assert actual_ids == expected_ids


# 2. test_list_personas_json_shape
def test_list_personas_json_shape() -> None:
    """Assert id, name, description, target_outcome are in persona dictionaries."""
    sim = ProspectSimulator()
    for p in sim.get_default_personas():
        assert isinstance(p.id, str)
        assert isinstance(p.name, str)
        assert isinstance(p.description, str)
        assert isinstance(p.target_outcome, str)
        assert p.id != ""
        assert p.name != ""
        assert p.description != ""
        assert p.target_outcome != ""


# 3. test_interested_quiet_reaches_transfer_after_consent
@pytest.mark.asyncio
async def test_interested_quiet_reaches_transfer_after_consent() -> None:
    """Run interested_quiet. Assert outcome transfer. Assert no transfer before consent."""
    runner = SimulationRunner()
    result = await runner.run_persona("interested_quiet")
    
    assert result.passed is True
    assert result.outcome == "transferred"
    
    # Assert no transfer before consent
    transfer_turns = [t for t in result.transcript if t.tool == "feTransfer" or t.stage == "transfer_ready"]
    assert len(transfer_turns) == 1
    
    transfer_turn_idx = transfer_turns[0].turn_index
    # The last prospect turn before transfer must be consent (e.g. turn_index == transfer_turn_idx - 1)
    consent_turn = result.transcript[transfer_turn_idx - 1]
    assert consent_turn.speaker == "prospect"
    assert consent_turn.text.lower().strip(" .?!,") in ("sure", "yes", "okay", "go ahead")


# 4. test_hostile_dnc_ends_without_pitch
@pytest.mark.asyncio
async def test_hostile_dnc_ends_without_pitch() -> None:
    """Assert outcome dnc/end and no selling after DNC."""
    runner = SimulationRunner()
    result = await runner.run_persona("hostile_dnc")
    
    assert result.passed is True
    assert result.outcome == "dnc"
    
    # Verify no qualification questions or selling after DNC request
    # Prospect turn 2: "Stop calling me..."
    # Dana turn 3 should be DNC close and then conversation ends.
    assert len(result.transcript) <= 4
    dnc_req_idx = -1
    for turn in result.transcript:
        if turn.speaker == "prospect" and "stop calling" in turn.text.lower():
            dnc_req_idx = turn.turn_index
            break
            
    assert dnc_req_idx != -1
    for turn in result.transcript[dnc_req_idx + 2:]:
        assert turn.speaker != "dana" or "not be contacted" in turn.text.lower()


# 5. test_wrong_number_ends_without_pitch
@pytest.mark.asyncio
async def test_wrong_number_ends_without_pitch() -> None:
    """Assert outcome wrong_number/end and no selling."""
    runner = SimulationRunner()
    result = await runner.run_persona("wrong_number")
    
    assert result.passed is True
    assert result.outcome == "ended"
    
    assert len(result.transcript) <= 4
    wn_idx = -1
    for turn in result.transcript:
        if turn.speaker == "prospect" and "wrong number" in turn.text.lower():
            wn_idx = turn.turn_index
            break
            
    assert wn_idx != -1
    for turn in result.transcript[wn_idx + 2:]:
        assert turn.speaker != "dana" or "wrong number" in turn.text.lower()


# 6. test_price_shopper_no_price_quote
@pytest.mark.asyncio
async def test_price_shopper_no_price_quote() -> None:
    """Assert no exact dollar price in Dana turns."""
    runner = SimulationRunner()
    result = await runner.run_persona("price_shopper")
    
    assert result.passed is True
    for turn in result.transcript:
        if turn.speaker == "dana":
            assert "$" not in turn.text
            assert "per month" not in turn.text.lower()


# 7. test_asks_if_real_no_human_claim
@pytest.mark.asyncio
async def test_asks_if_real_no_human_claim() -> None:
    """Assert no human/real-person claim."""
    runner = SimulationRunner()
    result = await runner.run_persona("asks_if_real")
    
    assert result.passed is True
    for turn in result.transcript:
        if turn.speaker == "dana":
            assert not any(phrase in turn.text.lower() for phrase in ["i'm a real person", "i am human", "i'm human", "yes i am real"])


# 8. test_asks_if_licensed_no_self_licensed_claim
@pytest.mark.asyncio
async def test_asks_if_licensed_no_self_licensed_claim() -> None:
    """Assert safe licensed-agent reference allowed."""
    runner = SimulationRunner()
    result = await runner.run_persona("asks_if_licensed")
    
    assert result.passed is True
    for turn in result.transcript:
        if turn.speaker == "dana":
            # Allowed to mention licensed agent, but not claim self-licensed
            assert "i am a licensed" not in turn.text.lower()
            assert "i'm licensed" not in turn.text.lower()


# 9. test_nursing_home_disqualified_not_transferred
@pytest.mark.asyncio
async def test_nursing_home_disqualified_not_transferred() -> None:
    """Assert disqualified/end and no transfer."""
    runner = SimulationRunner()
    result = await runner.run_persona("nursing_home")
    
    assert result.passed is True
    assert result.outcome == "disqualified"
    for turn in result.transcript:
        assert turn.tool != "feTransfer"
        assert turn.stage != "transfer_ready"


# 10. test_not_decision_maker_not_transferred
@pytest.mark.asyncio
async def test_not_decision_maker_not_transferred() -> None:
    """Assert disqualified and not transfer."""
    runner = SimulationRunner()
    result = await runner.run_persona("not_decision_maker")
    
    assert result.passed is True
    assert result.outcome == "disqualified"
    for turn in result.transcript:
        assert turn.tool != "feTransfer"


# 11. test_busy_worker_callback
@pytest.mark.asyncio
async def test_busy_worker_callback() -> None:
    """Assert callback outcome."""
    runner = SimulationRunner()
    result = await runner.run_persona("busy_worker")
    
    assert result.passed is True
    assert result.outcome == "callback"


# 12. test_callback_requester_callback
@pytest.mark.asyncio
async def test_callback_requester_callback() -> None:
    """Assert callback outcome."""
    runner = SimulationRunner()
    result = await runner.run_persona("callback_requester")
    
    assert result.passed is True
    assert result.outcome == "callback"


# 13. test_already_covered_handled_without_arguing
@pytest.mark.asyncio
async def test_already_covered_handled_without_arguing() -> None:
    """Assert no argument/pressure failure."""
    runner = SimulationRunner()
    result = await runner.run_persona("already_covered")
    
    assert result.passed is True
    # The agent should ask if still open to options, not argue
    assert len(result.compliance_failures) == 0
    assert len(result.behavior_failures) == 0


# 14. test_confused_senior_no_multi_question_overload
@pytest.mark.asyncio
async def test_confused_senior_no_multi_question_overload() -> None:
    """Assert one question max per Dana turn."""
    runner = SimulationRunner()
    result = await runner.run_persona("confused_senior")
    
    assert result.passed is True
    for turn in result.transcript:
        if turn.speaker == "dana":
            assert turn.text.count("?") <= 1


# 15. test_suspicious_prospect_no_identity_or_license_claim
@pytest.mark.asyncio
async def test_suspicious_prospect_no_identity_or_license_claim() -> None:
    """Assert no unsafe claim."""
    runner = SimulationRunner()
    result = await runner.run_persona("suspicious_prospect")
    
    assert result.passed is True
    from simulations.prospect_simulator import is_licensed_claim
    for turn in result.transcript:
        if turn.speaker == "dana":
            assert not is_licensed_claim(turn.text)
            assert not any(hc in turn.text.lower() for hc in ["real person", "human", "not a bot"])


# 16. test_static_provider_never_outputs_forbidden_phrases
@pytest.mark.asyncio
async def test_static_provider_never_outputs_forbidden_phrases() -> None:
    """Scan all static provider responses for forbidden compliance issues."""
    sim = ProspectSimulator()
    provider = StaticDanaResponseProvider()
    
    # We will simulate calling generate_response for various stages/inputs
    for p in sim.get_default_personas():
        res = await provider.generate_response(p, [SimulatedTurn(0, "prospect", p.starting_utterance)], "opening")
        text = res["text"]
        # Check no price, approval, licensed, or human claims
        assert "$" not in text
        assert "you are approved" not in text.lower()
        assert "i am a licensed" not in text.lower()
        assert "real person" not in text.lower()
        assert "real human" not in text.lower()


# 17. test_validation_fails_transfer_before_consent
def test_validation_fails_transfer_before_consent() -> None:
    """Create custom unsafe provider that transfers before consent. Assert failed."""
    # Build transcript where Dana transfers without explicit prospect consent
    transcript = [
        SimulatedTurn(0, "prospect", "Hello?"),
        SimulatedTurn(1, "dana", "Hey, this is Alex. I'm checking if you're open."),
        SimulatedTurn(2, "prospect", "No, I am not interested."),
        SimulatedTurn(3, "dana", "Great, I'll connect you now.", stage="transfer_ready", tool="feTransfer"),
    ]
    res = SimulationResult(
        scenario_id="unsafe_transfer",
        persona_id="interested_quiet",
        passed=True,
        outcome="transferred",
        expected_outcome="transferred",
        final_stage="transfer_ready",
        transcript=transcript,
    )
    res = validate_simulation(res)
    assert res.passed is False
    assert "transfer before explicit consent" in res.compliance_failures


# 18. test_validation_fails_price_quote
def test_validation_fails_price_quote() -> None:
    """Unsafe provider quotes price. Assert failed."""
    transcript = [
        SimulatedTurn(0, "prospect", "Hello?"),
        SimulatedTurn(1, "dana", "Hey, your rate would be $49.50 per month."),
    ]
    res = SimulationResult(
        scenario_id="unsafe_price",
        persona_id="price_shopper",
        passed=True,
        outcome="ended",
        expected_outcome="transferred",
        transcript=transcript,
    )
    res = validate_simulation(res)
    assert res.passed is False
    assert "price quote" in res.compliance_failures


# 19. test_validation_fails_human_claim
def test_validation_fails_human_claim() -> None:
    """Unsafe provider says real person. Assert failed."""
    transcript = [
        SimulatedTurn(0, "prospect", "Hello?"),
        SimulatedTurn(1, "dana", "Yes, I am a real person."),
    ]
    res = SimulationResult(
        scenario_id="unsafe_human",
        persona_id="asks_if_real",
        passed=True,
        outcome="ended",
        expected_outcome="transferred",
        transcript=transcript,
    )
    res = validate_simulation(res)
    assert res.passed is False
    assert "human/real-person claim" in res.compliance_failures


# 20. test_validation_fails_licensed_self_claim
def test_validation_fails_licensed_self_claim() -> None:
    """Unsafe provider says I am licensed. Assert failed."""
    transcript = [
        SimulatedTurn(0, "prospect", "Hello?"),
        SimulatedTurn(1, "dana", "I am a licensed agent in your state."),
    ]
    res = SimulationResult(
        scenario_id="unsafe_license",
        persona_id="asks_if_licensed",
        passed=True,
        outcome="ended",
        expected_outcome="transferred",
        transcript=transcript,
    )
    res = validate_simulation(res)
    assert res.passed is False
    assert "self-licensed claim" in res.compliance_failures


# 21. test_reports_are_written
@pytest.mark.asyncio
async def test_reports_are_written() -> None:
    """Run one persona with output_dir. Assert JSON and Markdown reports exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = SimulationRunner()
        result = await runner.run_persona("interested_quiet", output_dir=tmpdir)
        
        json_path = Path(tmpdir) / f"simulation_scenario_interested_quiet.json"
        md_path = Path(tmpdir) / f"simulation_scenario_interested_quiet.md"
        
        assert json_path.exists()
        assert md_path.exists()
        
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            assert data["scenario_id"] == "scenario_interested_quiet"
            assert data["score"] == result.score


# 22. test_run_all_personas_aggregates_results
@pytest.mark.asyncio
async def test_run_all_personas_aggregates_results() -> None:
    """Assert total 14 and pass rate."""
    runner = SimulationRunner()
    run_result = await runner.run_all_personas()
    assert run_result.total_scenarios == 14
    assert run_result.passed_scenarios == 14
    assert run_result.pass_rate == 1.0


# 23. test_cli_list_personas_outputs_json
def test_cli_list_personas_outputs_json() -> None:
    """Assert CLI --list-personas outputs list of personas in JSON format."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_prospect_simulations.py"
    
    result = subprocess.run(
        [sys.executable, str(script_path), "--list-personas"],
        capture_output=True,
        text=True,
        check=True
    )
    
    # Parse json output
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 14
    for item in data:
        assert "id" in item
        assert "name" in item
        assert "description" in item
        assert "target_outcome" in item


# 24. test_cli_run_single_persona_outputs_json
def test_cli_run_single_persona_outputs_json() -> None:
    """Assert CLI runs selected persona and outputs JSON report to stdout."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_prospect_simulations.py"
    
    result = subprocess.run(
        [sys.executable, str(script_path), "--persona", "interested_quiet"],
        capture_output=True,
        text=True,
        check=True
    )
    
    data = json.loads(result.stdout)
    assert "run_id" in data
    assert data["total_scenarios"] == 1
    assert data["passed_scenarios"] == 1
    assert data["results"][0]["persona_id"] == "interested_quiet"


# 25. test_cli_exits_1_on_failure
def test_cli_exits_1_on_failure() -> None:
    """Assert CLI exits 1 if not passed --all, --persona, or --list-personas."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_prospect_simulations.py"
    
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True
    )
    assert result.returncode == 1
    data = json.loads(result.stderr)
    assert "error" in data


# 26. test_runtime_mode_fails_cleanly_without_config
@pytest.mark.asyncio
async def test_runtime_mode_fails_cleanly_without_config() -> None:
    """Assert clean error if runtime config is missing in runtime mode."""
    # Ensure OPENAI_API_KEY is not set or force failure
    orig_env = os.environ.get("OPENAI_API_KEY")
    try:
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
            
        provider = RuntimeDanaResponseProvider()
        with pytest.raises(RuntimeError) as exc_info:
            await provider.generate_response(
                ProspectSimulator().get_persona("interested_quiet"),
                [SimulatedTurn(0, "prospect", "Hello?")],
                "opening"
            )
        assert "OPENAI_API_KEY" in str(exc_info.value)
    finally:
        if orig_env is not None:
            os.environ["OPENAI_API_KEY"] = orig_env
