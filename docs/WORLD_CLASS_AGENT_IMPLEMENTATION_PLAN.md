# World-Class Dana: Final Expense Sales Agent — Implementation Plan

## Current Repo Architecture

The existing repo is a **Sovereign Voice Stack** — a low-latency voice AI system with:

| Component | File | Technology |
|-----------|------|------------|
| Agent Orchestrator | `main.py` | LiveKit Agents Framework, VoiceAssistant |
| Speech-to-Text | `stt_service.py` | faster-whisper (large-v3-turbo, local GPU) |
| Voice Activity Detection | `stt_service.py` | Silero VAD v5 |
| Text-to-Speech | `tts_service.py` | Kokoro ONNX (af_bella voice, local GPU) |
| LLM | via OpenAI API | vLLM serving Llama-3.1-8B-Instruct |
| Infrastructure | `Dockerfile`, `docker-compose.yaml`, `entrypoint.sh` | NVIDIA CUDA, Docker |

**Current behavior**: Generic voice assistant with hardcoded system prompt. No sales logic, no state management, no compliance, no knowledge base.

---

## Missing Pieces

| Category | What's Missing |
|----------|---------------|
| **Identity** | No Final Expense sales persona; generic assistant prompt |
| **Call Flow** | No state machine; no qualification pipeline |
| **Data Extraction** | No entity extraction (age, state, phone type) |
| **Objection Handling** | No objection detection or response policy |
| **Compliance** | No output validation, no PII redaction, no DNC enforcement |
| **Knowledge Base** | No RAG, no product knowledge, no script documents |
| **Tools** | No lead save, transfer, callback, DNC actions |
| **Training** | No transcript ingestion, no training note extraction |
| **QA** | No call scoring, no rubric, no feedback loop |
| **Evals** | No scenario simulation, no assertion testing |
| **Storage** | No persistent storage layer |
| **Documentation** | No operator docs, no deployment guide |

---

## Final Target Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    LiveKit Room                          │
│  ┌──────────┐   ┌──────────┐   ┌───────────────────┐   │
│  │ STT      │   │ TTS      │   │ VoiceAssistant    │   │
│  │ (Whisper) │   │ (Kokoro) │   │ (LiveKit)         │   │
│  └────┬─────┘   └────▲─────┘   └────────┬──────────┘   │
│       │               │                  │               │
│       ▼               │                  ▼               │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Agent Runtime                        │   │
│  │  ┌──────────┐ ┌───────────┐ ┌────────────────┐   │   │
│  │  │ State    │ │ Objection │ │ Response       │   │   │
│  │  │ Machine  │ │ Engine    │ │ Builder        │   │   │
│  │  └────┬─────┘ └─────┬─────┘ └────────┬───────┘   │   │
│  │       │              │                │            │   │
│  │  ┌────▼──────────────▼────────────────▼───────┐   │   │
│  │  │           Lead Profile + Extraction         │   │   │
│  │  └────────────────────┬───────────────────────┘   │   │
│  │                       │                            │   │
│  │  ┌────────────────────▼───────────────────────┐   │   │
│  │  │              Safety Layer                   │   │   │
│  │  │  Compliance │ PII Redaction │ Output Valid  │   │   │
│  │  └────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────┘   │
│                          │                               │
│       ┌──────────────────┼──────────────────┐           │
│       ▼                  ▼                  ▼           │
│  ┌─────────┐      ┌───────────┐      ┌──────────┐     │
│  │ RAG     │      │ Tools     │      │ Storage  │     │
│  │ Context │      │ Registry  │      │ Layer    │     │
│  └─────────┘      └───────────┘      └──────────┘     │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │                 QA + Evals                       │   │
│  │  Scoring │ Rubric │ Eval Scenarios │ Fine-tune  │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## File Tree (Final)

```
dana/
├── main.py                          # Modified: loads prompts, uses agent runtime
├── stt_service.py                   # UNCHANGED
├── tts_service.py                   # UNCHANGED
├── requirements.txt                 # Updated with new deps
├── Dockerfile                       # UNCHANGED
├── docker-compose.yaml              # UNCHANGED
├── entrypoint.sh                    # UNCHANGED
├── .env.example                     # Updated with new vars
│
├── prompts/
│   ├── final_expense_agent.md       # Main Dana persona prompt
│   ├── voice_style_rules.md         # Voice behavior rules
│   └── compliance_guardrails.md     # Compliance boundaries
│
├── config/
│   ├── agent_config.yaml            # General agent config
│   ├── final_expense_config.yaml    # FE-specific config
│   └── consent_policy.yaml          # Consent/stop policy
│
├── core/
│   ├── __init__.py
│   ├── prompt_loader.py             # Load & compose prompts
│   ├── call_state.py                # CallStage enum + CallState
│   ├── lead_profile.py              # LeadProfile pydantic model
│   ├── state_machine.py             # Qualification state machine
│   ├── extraction.py                # Entity extraction from utterances
│   ├── objection_classifier.py      # Classify user objections
│   ├── objection_response_policy.py # Objection response rules
│   ├── action_policy.py             # Tool firing policy
│   ├── agent_runtime.py             # Main runtime orchestrator
│   ├── response_builder.py          # Build LLM context/instructions
│   └── runtime_events.py            # Event types for runtime
│
├── states/
│   ├── __init__.py
│   ├── opening.py
│   ├── permission.py
│   ├── age.py
│   ├── state_location.py
│   ├── phone_type.py
│   ├── text_capable.py
│   ├── budget.py
│   ├── beneficiary.py
│   ├── interest.py
│   ├── objection.py
│   ├── transfer_ready.py
│   ├── disqualified.py
│   ├── callback.py
│   └── dnc.py
│
├── tools/
│   ├── __init__.py
│   ├── base.py                      # BaseTool + ToolResult
│   ├── save_lead.py
│   ├── transfer_to_agent.py
│   ├── schedule_callback.py
│   ├── mark_dnc.py
│   ├── escalate_to_human.py
│   └── tool_registry.py
│
├── rag/
│   ├── __init__.py
│   ├── document.py                  # Document model
│   ├── chunking.py                  # Markdown chunking
│   ├── embeddings.py                # Embedding generation
│   ├── vector_store.py              # Vector storage
│   ├── retriever.py                 # Context retrieval
│   └── context_builder.py           # Build RAG context block
│
├── kb/
│   ├── __init__.py
│   ├── final_expense_basics.md
│   ├── script/
│   │   └── master_script.md
│   ├── compliance/
│   │   └── final_expense_boundaries.md
│   ├── objections/
│   │   └── final_expense_objections.yaml
│   └── training_notes/
│       ├── README.md
│       └── generated/.gitkeep
│
├── training/
│   ├── __init__.py
│   ├── ingest_markdown.py
│   ├── ingest_jsonl.py
│   ├── build_index.py
│   ├── ingest_video_transcript.py
│   ├── extract_training_lessons.py
│   ├── training_note_schema.py
│   └── examples/
│       └── video_transcript_example.txt
│
├── qa/
│   ├── __init__.py
│   ├── call_record.py
│   ├── scoring.py
│   ├── rubric.py
│   ├── extract_lessons.py
│   ├── generate_eval_case.py
│   └── export_finetune_examples.py
│
├── evals/
│   ├── __init__.py
│   ├── scenario_schema.py
│   ├── scenario_runner.py
│   ├── assertions.py
│   ├── run_all.py
│   └── scenarios/
│       ├── not_interested.yaml
│       ├── already_covered.yaml
│       ├── busy.yaml
│       ├── no_money.yaml
│       ├── asks_price.yaml
│       ├── is_this_government.yaml
│       ├── scam_concern.yaml
│       ├── remove_me.yaml
│       ├── refuses_age.yaml
│       ├── underage.yaml
│       ├── confused_senior.yaml
│       ├── talk_to_spouse.yaml
│       ├── wants_callback.yaml
│       └── wants_agent_now.yaml
│
├── storage/
│   ├── __init__.py
│   ├── base.py
│   ├── jsonl_store.py
│   ├── postgres_store.py
│   ├── repository.py
│   └── schemas.py
│
├── safety/
│   ├── __init__.py
│   ├── compliance_filter.py
│   ├── output_validator.py
│   ├── pii_redaction.py
│   ├── consent_policy.py
│   └── call_stop_policy.py
│
├── migrations/
│   └── 001_initial.sql
│
├── data/
│   ├── .gitkeep
│   ├── calls/.gitkeep
│   └── qa_reports/.gitkeep
│
├── tests/
│   ├── __init__.py
│   ├── test_prompt_loader.py
│   ├── test_state_machine.py
│   ├── test_lead_profile.py
│   ├── test_extraction.py
│   ├── test_objection_classifier.py
│   ├── test_objection_response_policy.py
│   ├── test_tools.py
│   ├── test_action_policy.py
│   ├── test_chunking.py
│   ├── test_retriever.py
│   ├── test_context_builder.py
│   ├── test_agent_runtime.py
│   ├── test_response_builder.py
│   ├── test_video_training_ingestion.py
│   ├── test_qa_scoring.py
│   ├── test_eval_case_generation.py
│   ├── test_finetune_export.py
│   ├── test_scenario_runner.py
│   ├── test_jsonl_store.py
│   ├── test_repository.py
│   ├── test_output_validator.py
│   ├── test_compliance_filter.py
│   └── test_call_stop_policy.py
│
└── docs/
    ├── WORLD_CLASS_AGENT_IMPLEMENTATION_PLAN.md
    ├── WORLD_CLASS_DANA_BUILD_SUMMARY.md
    ├── OPERATOR_PLAYBOOK.md
    ├── TRAINING_PIPELINE.md
    ├── RAG_KNOWLEDGE_BASE.md
    ├── EVALS_AND_QA.md
    ├── COMPLIANCE_GUARDRAILS.md
    └── DEPLOYMENT.md
```

---

## Implementation Phases

| Phase | Name | Dependencies | Key Deliverables |
|-------|------|-------------|------------------|
| 1 | Implementation Plan | None | This document |
| 2 | Prompt System | None | Prompts, configs, prompt loader |
| 3 | State Machine | None | Call flow, lead profile, extraction |
| 4 | Objection Engine | Phase 3 | Objection YAML, classifier, policy |
| 5 | Tools / Actions | Phase 3 | Lead save, transfer, DNC tools |
| 6 | RAG Knowledge Base | None | Chunking, embeddings, retrieval |
| 7 | Runtime Integration | Phases 2-6 | Wire everything into main.py |
| 8 | Training Ingestion | Phase 6 | Video/transcript ingestion |
| 9 | QA Feedback Loop | Phase 3, 7 | Call scoring, rubric, eval export |
| 10 | Eval Simulator | Phase 3, 4 | Scenario YAML, runner, assertions |
| 11 | Storage Layer | None | JSONL + optional Postgres |
| 12 | Safety Hardening | Phase 2, 3 | Compliance, PII, consent, stop |
| 13 | Documentation | All | Operator docs, deployment guide |
| 14 | Final Hardening | All | Review, test, fix |

---

## Risks

| Risk | Mitigation |
|------|-----------|
| vLLM model may not follow complex system prompts perfectly | Keep prompts concise; use structured stage instructions |
| Barge-in behavior could conflict with state tracking | Preserve existing barge-in; state updates are async |
| RAG embedding quality with local models | Use TF-IDF fallback if no embedding model available |
| Postgres not available in all environments | JSONL fallback is default; Postgres only if DATABASE_URL set |
| LiveKit agent API changes between versions | Pin livekit-agents version; use documented interfaces |
| Compliance validation adds latency | Keep validation fast (regex-based); async where possible |

---

## Tests

All tests run with `python -m pytest -q` and require no external services.

- **Unit tests**: Every module has corresponding tests
- **Mock strategy**: vLLM calls mocked, LiveKit mocked, file I/O uses tmp_path
- **Eval tests**: `python evals/run_all.py` runs all scenario assertions

---

## Local Run Instructions

```bash
# 1. Clone and setup
git clone <repo> && cd dana
cp .env.example .env
# Edit .env with your LiveKit + vLLM credentials

# 2. Install deps
pip install -r requirements.txt

# 3. Run tests
python -m pytest -q

# 4. Run evals
python evals/run_all.py

# 5. Start agent
python main.py start
```

---

## Training Pipeline Overview

```
User-Provided Materials          Ingestion Pipeline              Output
─────────────────────           ──────────────────              ──────
.txt transcript  ────┐
.vtt subtitle    ────┤──► ingest_video_transcript.py ──► training_notes.jsonl
.srt subtitle    ────┤                                   ├── generated/*.md
.md notes        ────┘                                   └── RAG index update

Call Recordings  ────► qa/scoring.py ──► qa_reports.jsonl
                       qa/rubric.py      ├── improvement lessons
                                         ├── eval YAML generation
                                         └── fine-tune JSONL export
```
