from __future__ import annotations

from voice.repair_policy import RepairPolicy


def test_repair_policy_prefix_selection():
    policy = RepairPolicy()
    
    # Early turns on opening stage
    prefix = policy.select_repair_prefix(stage="opening", turn_count=1)
    assert prefix == "I didn't mean to cut you off."
    
    # Later turns on opening stage
    prefix = policy.select_repair_prefix(stage="opening", turn_count=3)
    assert prefix == "Sorry, go ahead."

    # Objection stage early turn
    prefix = policy.select_repair_prefix(stage="objection", turn_count=2)
    assert prefix == "Sorry, go ahead."

    # Objection stage later turn
    prefix = policy.select_repair_prefix(stage="objection", turn_count=4)
    assert prefix == "Sure, I'll keep it quick."


def test_repair_policy_injection():
    policy = RepairPolicy()
    
    # Verify prefix injection and lowercase of rest
    res = policy.inject_repair(
        text="What options were you looking for?",
        stage="opening",
        turn_count=1,
    )
    assert res == "I didn't mean to cut you off. what options were you looking for?"
    
    # Verify double prefix protection
    res2 = policy.inject_repair(
        text="Sorry, go ahead. I wanted to clarify.",
        stage="objection",
        turn_count=1,
    )
    assert res2 == "Sorry, go ahead. I wanted to clarify."
