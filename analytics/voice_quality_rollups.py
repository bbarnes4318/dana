"""Voice quality and humanlikeness analytics rollup functions."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from storage.repository import Repository
from analytics.platform_metrics import is_within_range


async def get_voice_quality_metrics(
    repository: Optional[Repository] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None
) -> dict:
    """Calculate average bot-likeness, human realism, repetition, words per turn, and interruption repair scores."""
    repo = repository or Repository()
    
    # Query calls, turns, and QA reports
    calls = await repo.store.query("calls", {})
    turns = await repo.store.query("call_turns", {})
    reports = await repo.store.query("qa_reports", {})
    
    # Filter calls within date range
    filtered_calls = [
        c for c in calls
        if is_within_range(c.get("created_at") or c.get("started_at"), from_date, to_date)
    ]
    filtered_call_ids = {c["call_id"] for c in filtered_calls if "call_id" in c}
    
    # Map reports by call_id
    call_reports = {r["call_id"]: r for r in reports if "call_id" in r}
    
    # Map turns by call_id
    call_turns_map: dict[str, list[dict]] = {}
    for t in turns:
        cid = t.get("call_id")
        if cid in filtered_call_ids:
            call_turns_map.setdefault(cid, []).append(t)
            
    bot_likeness_scores = []
    realism_scores = []
    repetition_scores = []
    interruption_repair_scores = []
    
    total_agent_words = 0
    total_agent_turns = 0
    
    overused_phrases = ["perfect", "gotcha", "understood", "absolutely", "no problem", "great question"]
    repair_phrases = ["sorry, go ahead", "sorry go ahead", "didn't mean to cut you off", "did not mean to cut you off", "keep it quick"]
    
    for c in filtered_calls:
        cid = c.get("call_id")
        report = call_reports.get(cid)
        
        # 1 & 2: Bot-likeness and Human Realism from QA report (or default to 10.0)
        bot_score = 10.0
        real_score = 10.0
        if report and "scores" in report:
            scores = report["scores"]
            if "bot_likeness" in scores:
                bot_score = float(scores["bot_likeness"])
            if "human_realism" in scores:
                real_score = float(scores["human_realism"])
        bot_likeness_scores.append(bot_score)
        realism_scores.append(real_score)
        
        # Get turns for this call
        c_turns = call_turns_map.get(cid, [])
        agent_turns = [t for t in c_turns if t.get("speaker") == "agent"]
        
        # 3. Repetition score calculation
        rep_deductions = 0.0
        phrase_counts = {p: 0 for p in overused_phrases}
        spoken_sentences = {}
        
        for t in agent_turns:
            text = str(t.get("text") or "")
            text_lower = text.lower()
            
            # Words per turn counts
            words = text.split()
            total_agent_words += len(words)
            total_agent_turns += 1
            
            # Overused phrases
            for phrase in overused_phrases:
                count = len(re.findall(r"\b" + re.escape(phrase) + r"\b", text_lower))
                phrase_counts[phrase] += count
                
            # Duplicate sentences of length >= 3 words
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            for s in sentences:
                clean_s = s.strip()
                if not clean_s:
                    continue
                s_words = clean_s.split()
                if len(s_words) >= 3:
                    norm_s = re.sub(r"[^a-z0-9]", "", clean_s.lower())
                    spoken_sentences[norm_s] = spoken_sentences.get(norm_s, 0) + 1
                    
        for phrase, count in phrase_counts.items():
            if count > 2:
                rep_deductions += (count - 2) * 2.0
                
        for norm_s, count in spoken_sentences.items():
            if count > 1:
                rep_deductions += (count - 1) * 3.0
                
        repetition_scores.append(max(10.0 - rep_deductions, 0.0))
        
        # 4. Interruption repair score calculation
        interrupted_turns = [t for t in agent_turns if t.get("interrupted") is True]
        if not interrupted_turns:
            interruption_repair_scores.append(10.0)
        else:
            repair_deductions = 0.0
            for t in interrupted_turns:
                text_lower = str(t.get("text") or "").lower()
                has_repair = any(p in text_lower for p in repair_phrases)
                if not has_repair:
                    repair_deductions += 3.0
            interruption_repair_scores.append(max(10.0 - repair_deductions, 0.0))
            
    avg_bot_likeness = sum(bot_likeness_scores) / len(bot_likeness_scores) if bot_likeness_scores else 0.0
    avg_realism = sum(realism_scores) / len(realism_scores) if realism_scores else 0.0
    avg_repetition = sum(repetition_scores) / len(repetition_scores) if repetition_scores else 0.0
    avg_words_per_turn = total_agent_words / total_agent_turns if total_agent_turns > 0 else 0.0
    avg_repair = sum(interruption_repair_scores) / len(interruption_repair_scores) if interruption_repair_scores else 0.0
    
    return {
        "bot_likeness_score": round(avg_bot_likeness, 2),
        "repetition_score": round(avg_repetition, 2),
        "average_words_per_turn": round(avg_words_per_turn, 2),
        "interruption_repair_score": round(avg_repair, 2),
        "human_realism_score": round(avg_realism, 2)
    }
