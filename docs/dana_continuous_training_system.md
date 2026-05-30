# Dana Continuous Training System

## Purpose

Dana should not be “trained” by dumping YouTube videos and thousands of transcripts directly into the live model.

The best system is a closed-loop learning factory:

1. Ingest every useful source.
2. Clean, diarize, redact, and normalize it.
3. Label it by stage, objection, outcome, compliance risk, and sales quality.
4. Extract reusable lessons.
5. Convert approved lessons into versioned prompts, retrieval documents, policies, simulations, and fine-tuning examples.
6. Run regression tests and simulated calls before deployment.
7. Canary new behavior on a small percentage of calls.
8. Promote only changes that improve transfer rate, compliance, latency, and call quality.

The daily goal is not to make Dana memorize calls. The daily goal is to make Dana measurably better while preventing compliance drift.

---

## Current Repo Fit

The existing Dana application already has the right foundation:

- Deterministic call-state runtime.
- Per-turn storage.
- Objection classification.
- RAG context builder.
- Compliance and output validation.
- QA scoring.
- Model/TTS/STT routing.
- Lead outcome and cost metrics.
- Prompt file for Final Expense call flow.

The missing piece is a production continuous-learning pipeline that turns recordings, transcripts, YouTube coaching, QA failures, and outcomes into controlled improvements.

---

## Core Principle

Use different training material for different purposes.

| Source | Best Use | Do Not Use It For |
|---|---|---|
| YouTube strategy videos | Sales principles, objection frameworks, tone, structure, examples | Direct fine-tuning without human review |
| Successful call transcripts | Winning patterns, timing, objections, transfer language, persona simulation | Blind memorization |
| Failed call transcripts | Regression tests, failure detection, anti-patterns | Teaching Dana to repeat bad behavior |
| Compliance notes | Hard guardrails, validators, QA hard-fail rules | Optional RAG only |
| Live Dana calls | Daily QA, prompt patches, simulation seeds, human review queue | Fully automatic self-modification |
| Licensed-agent feedback | Highest-value corrections and transfer-quality scoring | Ignored or averaged away |

---

## Target Architecture

```text
Sources
  ├── YouTube videos / coaching calls
  ├── Historical human-agent transcripts
  ├── Historical AI-agent transcripts
  ├── Recordings / STT output
  ├── CRM outcomes
  ├── Transfer results
  ├── Agent feedback
  └── Compliance updates

Ingestion Layer
  ├── Download / import transcripts
  ├── Audio transcription when transcript missing
  ├── Speaker diarization
  ├── PII redaction
  ├── Timestamp alignment
  └── Deduplication

Labeling Layer
  ├── Call stage labels
  ├── Objection labels
  ├── Sentiment / confusion / hostility labels
  ├── Outcome labels
  ├── Compliance labels
  ├── Transfer-readiness labels
  ├── Agent pickup / bridge quality labels
  └── Sales-manager quality labels

Learning Asset Builder
  ├── RAG documents
  ├── Prompt patches
  ├── Objection response cards
  ├── Compliance rules
  ├── Eval cases
  ├── Simulated prospect personas
  ├── Fine-tuning examples
  └── Daily training report

Evaluation Gate
  ├── Unit tests for state transitions
  ├── Compliance hard-fail tests
  ├── Transcript replay tests
  ├── Prospect simulation tests
  ├── Latency tests
  ├── Transfer consent tests
  └── Canary deployment metrics

Runtime
  ├── Deterministic state machine
  ├── Real-time RAG retrieval
  ├── Short dynamic prompt compiler
  ├── LLM response generation
  ├── Compliance validator
  ├── Spoken output auditor
  ├── Tool execution
  └── Post-call QA
```

---

## What “Training Dana” Should Mean

### 1. Knowledge Training

This is RAG.

Use RAG for:

- Final Expense FAQs.
- Objection answers.
- Compliance rules.
- Carrier-neutral education.
- Call examples.
- Licensed-agent escalation explanations.
- Sales-manager coaching notes.

RAG must be fast, semantic, versioned, and stage-aware.

Production requirements:

- Replace TF-IDF/word-frequency embeddings with semantic embeddings.
- Use pgvector with an index, not Python cosine over FLOAT arrays.
- Store every document with metadata: source, date, stage, topic, quality score, compliance status, approved_by, version.
- Use hybrid search: semantic similarity + keyword + stage boost + compliance priority.
- Keep retrieved context short. Dana should not receive giant blocks mid-call.

### 2. Behavior Training

This is prompt policy, state-machine behavior, examples, and limited fine-tuning.

Use behavior training for:

- What Dana says first.
- How short her answers are.
- When she stops talking.
- How she recovers from confusion.
- How she handles “already have insurance.”
- How she asks for transfer consent.
- How she avoids sounding like a chatbot.

Do not rely on RAG alone for this. Behavior must be controlled by the state machine, response builder, objection policy, spoken-output auditor, and tests.

### 3. Safety Training

This is not optional.

Safety rules must stay outside the model as hard checks:

- No price quotes.
- No approval claims.
- No “you qualify.”
- No licensed-agent claim.
- No human claim.
- No sensitive info collection.
- Stop after DNC.
- No transfer without explicit consent.

The live model can generate language, but the app must be able to reject unsafe language before it is spoken.

### 4. Fine-Tuning

Fine-tuning is useful later, not first.

Use fine-tuning only after you have a clean, labeled, approved dataset. The first fine-tune should teach concise call-center language, stage-specific phrasing, and objection handling style—not factual insurance knowledge.

Good fine-tuning examples:

```json
{
  "stage": "interest_check",
  "user": "I already have insurance.",
  "assistant": "Gotcha. A lot of people do. Were you still open to reviewing what options are available, or are you all set?",
  "labels": {
    "compliance_pass": true,
    "one_question": true,
    "stage_correct": true,
    "human_style_score": 9.4
  }
}
```

Bad fine-tuning examples:

- Raw full transcripts.
- Calls with unreviewed compliance problems.
- Examples containing PII.
- Long sales lectures.
- Calls where the agent pushed after a DNC request.
- Calls where the agent transferred without consent.

---

## Daily Learning Loop

Every day, run this cycle.

### 1. Ingest

Pull the previous day’s:

- Call turns.
- Recordings.
- Transcripts.
- Transfer events.
- QA reports.
- Cost records.
- Campaign outcome metrics.
- Licensed-agent feedback.

### 2. Score

Score every call with:

- Existing rule-based QA.
- Compliance hard fails.
- Outcome quality.
- Transfer quality.
- Human realism.
- Objection handling.
- Latency.
- Interruption behavior.
- Hang-up point.
- “Would a human manager approve this?” score.

### 3. Mine Patterns

Automatically detect:

- Top winning agent responses.
- Responses that caused hang-ups.
- Objections that Dana failed to recover.
- Stages where prospects go silent.
- Phrases that sound robotic.
- Questions Dana asked too early.
- Cases where she should have ended faster.
- Cases where she should have bridged faster.

### 4. Generate Candidate Improvements

Generate candidates as separate artifact types:

- Prompt patch.
- Objection-card update.
- RAG document.
- Validator rule.
- State-machine change request.
- Simulation case.
- Fine-tune example.

Never push candidates straight into production.

### 5. Human Approval

A sales/compliance reviewer should approve or reject:

- New winning responses.
- Objection language.
- Script changes.
- Compliance rule changes.
- Fine-tuning examples.

Reviewer decisions become training data too.

### 6. Evaluate

Before deployment, run:

- Regression tests against known bad calls.
- Replay tests against real transcripts.
- Simulated calls with prospect personas.
- DNC tests.
- Consent gating tests.
- Objection tests.
- Latency tests.
- Transfer path tests.

### 7. Canary

Deploy to a limited share of live calls first.

Suggested rollout:

- 5% of calls for 2 hours.
- 20% if no compliance failures and transfer rate improves.
- 50% after stable metrics.
- 100% only after passing daily target thresholds.

### 8. Promote / Roll Back

Promote only if:

- Compliance hard fails = 0.
- Transfer-before-consent = 0.
- DNC failures = 0.
- Average QA score improves or stays above threshold.
- Transfer rate improves.
- Hang-up rate does not worsen.
- Latency stays inside threshold.

Rollback immediately if:

- Compliance hard fail occurs.
- DNC handling fails.
- Transfer consent gating fails.
- Robotic responses increase.
- Hang-ups spike.

---

## Data Model Additions

Add these logical records.

### training_sources

```json
{
  "id": "source_123",
  "source_type": "youtube|call_transcript|recording|manager_note|licensed_agent_feedback|compliance_update",
  "source_uri": "string",
  "title": "string",
  "imported_at": "datetime",
  "status": "raw|processed|approved|rejected",
  "metadata": {}
}
```

### training_examples

```json
{
  "id": "example_123",
  "source_id": "source_123",
  "call_id": "optional",
  "stage": "interest_check",
  "user_text": "I already have insurance.",
  "ideal_response": "Gotcha. A lot of people do. Were you still open to reviewing what options are available, or are you all set?",
  "bad_response": "optional",
  "labels": {
    "objection_type": "already_have_insurance",
    "compliance_pass": true,
    "one_question": true,
    "human_style_score": 9.2,
    "sales_quality_score": 8.8
  },
  "approved_by": "manager_id",
  "approved_at": "datetime",
  "use_for": ["prompt", "rag", "eval", "fine_tune"]
}
```

### eval_cases

```json
{
  "id": "eval_123",
  "stage": "transfer_consent",
  "prospect_utterance": "Yeah, okay, put them on.",
  "expected_behavior": "confirm transfer and trigger feTransfer",
  "must_include": ["Stay right there"],
  "must_not_include": ["you qualify", "approved", "guaranteed"],
  "expected_tool": "feTransfer",
  "severity": "critical"
}
```

### prompt_versions

```json
{
  "id": "prompt_v2026_05_30_01",
  "file_path": "prompts/final_expense_alex.md",
  "sha": "git_sha",
  "created_at": "datetime",
  "created_by": "system|human",
  "change_reason": "Reduced long explanation after price objection.",
  "qa_thresholds": {},
  "canary_status": "pending|active|passed|failed|promoted|rolled_back"
}
```

---

## YouTube Training Pipeline

Use YouTube videos this way:

1. Pull transcript or transcribe audio.
2. Chunk by topic, not arbitrary length.
3. Classify each chunk:
   - opening strategy
   - trust building
   - objection handling
   - tonality
   - transfer control
   - compliance risk
   - bad advice / reject
4. Convert useful chunks into manager-reviewable lessons.
5. Human approves the lessons.
6. Approved lessons become:
   - RAG documents
   - prompt guidance
   - eval cases
   - simulation personas
7. Nothing from YouTube goes live without approval.

Output example:

```json
{
  "source": "youtube",
  "topic": "busy objection",
  "sales_lesson": "Do not ask whether they have a minute. Acknowledge and route to a concrete callback choice.",
  "good_example": "No problem. Would later today or tomorrow be better?",
  "bad_example": "Do you have a few minutes?",
  "call_stage": "callback",
  "approved": true
}
```

---

## Historical Transcript Training Pipeline

Use call transcripts this way:

1. Normalize speaker labels.
2. Remove PII.
3. Split into turns.
4. Tag every turn with stage.
5. Link to outcome:
   - no answer
   - answered
   - interested
   - callback
   - transferred
   - sold
   - DNC
   - disqualified
   - hang-up
6. Extract the strongest agent turn for each objection/stage.
7. Extract failure patterns.
8. Generate eval cases from failures.
9. Generate candidate prompt/RAG updates from winners.
10. Human approve before use.

The most valuable data is not the transcript itself. The most valuable data is the contrast between:

- what was said,
- what happened next,
- whether the transfer occurred,
- whether the licensed agent converted it,
- and whether compliance stayed clean.

---

## Simulation System

Dana needs a prospect simulator trained from real call behavior.

Create personas such as:

- confused senior
- suspicious prospect
- busy worker
- spouse handles money
- already covered
- price shopper
- callback requester
- hostile DNC
- wrong number
- interested but quiet
- rambling storyteller
- asks if Dana is real
- asks if Dana is licensed
- asks for price

Each simulator run should produce:

- full call transcript
- final stage
- tools triggered
- compliance result
- QA score
- expected vs actual behavior
- failure notes

This lets Dana improve without risking live calls.

---

## Metrics That Matter

Track daily:

- Answered calls.
- Human answered rate.
- Open-to-review rate.
- Age-confirmed rate.
- Living-independently confirmed rate.
- Decision-maker confirmed rate.
- Transfer-consent rate.
- Transfer attempt rate.
- Successful bridge rate.
- Callback rate.
- DNC rate.
- Disqualification rate.
- Hang-up by stage.
- Average QA score.
- Compliance hard fails.
- Transfer-before-consent count.
- DNC failure count.
- Median latency by component.
- First audio latency.
- Barge-in interruption success.
- Cost per answered call.
- Cost per transfer.
- Cost per sale when sale data is available.

The north-star metric should be:

```text
compliant licensed-agent conversations per 100 human answers
```

Secondary metric:

```text
cost per compliant licensed-agent conversation
```

Final business metric:

```text
cost per issued policy / cost per sale
```

---

## Immediate Build Priorities

### Priority 1 — Training data warehouse

Add durable tables for:

- training_sources
- training_examples
- eval_cases
- prompt_versions
- deployment_experiments
- human_review_items
- call_outcome_labels

### Priority 2 — Better RAG

Replace lightweight retrieval with:

- semantic embeddings
- pgvector
- HNSW/IVFFlat index
- metadata filters
- hybrid search
- reranking
- stage-aware retrieval

### Priority 3 — Daily QA miner

Create a job that:

- reads yesterday’s calls
- scores them
- identifies failure clusters
- proposes training notes
- proposes eval cases
- queues human review

### Priority 4 — Eval runner

Create a deterministic eval suite that tests Dana before every prompt/model change.

Must include:

- DNC
- wrong number
- price question
- are you licensed
- are you real
- already insured
- not interested
- busy
- transfer consent
- silence after consent
- assisted living
- not decision maker

### Priority 5 — Prompt versioning and canary release

Every prompt/policy change must be versioned and measured.

### Priority 6 — Fine-tuning dataset builder

Only after the above exists, create fine-tuning JSONL from approved examples.

---

## Hard Rules

1. Do not allow direct self-training into production.
2. Do not fine-tune on raw transcripts.
3. Do not include PII in training examples.
4. Do not use failed calls as positive examples.
5. Do not rely on the model to enforce compliance.
6. Do not use YouTube advice without human approval.
7. Do not deploy prompt changes without regression tests.
8. Do not optimize only for transfer rate; optimize for compliant, sale-producing transfers.
9. Do not let Dana ask extra questions just because the model thinks it is helpful.
10. Do not let RAG override the state machine or compliance policy.

---

## Best Final System

The best Dana system is:

- deterministic where compliance and call flow matter,
- generative where natural language matters,
- retrieval-based where knowledge matters,
- fine-tuned only where style and consistent response behavior matter,
- evaluated before every release,
- canaried before full rollout,
- and improved every day from actual outcomes.

The goal is not a smarter chatbot.

The goal is a fast, human-sounding, compliance-safe transfer machine that gets qualified final-expense prospects onto the phone with licensed agents better than a human SDR can.
