# CBP Transcript Analysis Pipeline

> **Mashreq Bank · Conversational Banking Platform (CBP)**
> Analyse Kore.ai chatbot transcripts to identify the top 10 journeys for the CBP MVP.

---

## Overview

This pipeline processes chatbot conversation logs exported from Kore.ai's MongoDB, runs LLM-powered classification across four stages, produces five analysis workstreams, and surfaces a ranked shortlist of journeys where automating agent knowledge into the bot will deliver the greatest reduction in escalations.

It is built against **synthetic data** (10,000 sessions, 90 journeys) so the dev team can point it at real MongoDB and run it with minimal config changes.

---

## Architecture

```
 Kore.ai MongoDB (or CSV mock)
        │
        ▼
 ┌─────────────────────┐
 │  Ingestion Layer     │  template_parser · sessionizer · mongo_connector
 └──────────┬──────────┘
            │
            ▼
 ┌─────────────────────┐
 │  PII Redaction       │  pii_redactor · redaction_audit
 └──────────┬──────────┘
            │
            ▼
 ┌─────────────────────┐
 │  LLM Classification  │  4 stages: journey · transfer_reason · failure_mode · remediation
 │  (cost gate → YES)   │  Anthropic Claude / Azure OpenAI / MockLLMClient
 └──────────┬──────────┘
            │
      ┌─────┴──────┬──────────────┬─────────────────┬─────────────────┐
      ▼            ▼              ▼                  ▼                 ▼
 Leakage Map  CSAT Joiner  Temporal Trends  Division of Labor  Knowledge Transfer
      │            │              │                  │                 │
      └─────────────────────────┬──────────────────────────────────────┘
                                ▼
                    ┌─────────────────────┐
                    │   5-D Scoring        │  rank_journeys · mvp_shortlist
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
             Per-Journey         Streamlit Dashboard
              Word Docs          (5 views + spot-check)
```

---

## Quick Start

```bash
# 1. Clone and set up
git clone <repo>
cd cbp-transcript-analysis
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — set MONGO_URI and ANTHROPIC_API_KEY

# 3. Dry run (no LLM calls, validates pipeline end-to-end)
python -m src.pipeline --source full --dry-run --yes

# 4. POC run (100 classified sessions, ~$0.65 — recommended for first real LLM run)
python -m src.pipeline --source full --max-classify 100

# 5. Full run (all 10,000 sessions, ~$33 — for production analysis)
python -m src.pipeline --source full

# 5. Launch dashboard
streamlit run src/dashboard/app.py
```

---

## Project Structure

```
cbp-transcript-analysis/
├── config/
│   ├── taxonomies.yaml          # 90 journeys, scoring weights, exclusion list
│   ├── schema_mapping.yaml      # Field name remapping, session path flag
│   └── llm_config.yaml          # LLM provider, model, cost rates
├── data/
│   ├── synthetic/               # Generated data (.gitignored)
│   ├── raw/                     # Real MongoDB exports (.gitignored)
│   └── outputs/                 # Pipeline outputs (.gitignored)
├── docs/
│   ├── SCHEMA_CONTRACT.md       # MongoDB field contract with [VERIFY] callouts
│   ├── DEV_TEAM_RUNBOOK.md      # Setup guide for engineers
│   ├── KNOWN_LIMITATIONS.md     # Design decisions and future work
│   └── CHANGELOG.md             # Version history
├── prompts/
│   ├── journey_classifier_v1.txt
│   ├── transfer_reason_classifier_v1.txt
│   ├── failure_mode_classifier_v1.txt
│   └── remediation_type_classifier_v1.txt
├── src/
│   ├── ingestion/
│   │   ├── template_parser.py   # Kore.ai bot message parser
│   │   ├── sessionizer.py       # Dual-path session reconstruction
│   │   ├── mongo_connector.py   # MongoDB / CSV factory connector
│   │   └── generate_synthetic.py# Synthetic data generator
│   ├── redaction/
│   │   ├── pii_redactor.py      # Emirates ID, cards, IBANs, mobiles, email
│   │   └── redaction_audit.py   # Recall audit against seeded PII
│   ├── coding/
│   │   ├── llm_client.py        # Anthropic / Azure / Mock client
│   │   ├── cost_estimator.py    # Pre-run cost estimate + YES gate
│   │   ├── classifier.py        # 4-stage classification pipeline
│   │   └── review_queue.py      # Spot-check workflow
│   ├── analysis/
│   │   ├── leakage_map.py
│   │   ├── csat_joiner.py
│   │   ├── temporal_trends.py
│   │   ├── agent_division_of_labor.py
│   │   └── knowledge_transfer_finder.py
│   ├── scoring/
│   │   └── rank_journeys.py     # 5-dimension composite scoring
│   ├── reporting/
│   │   └── per_journey_doc.py   # python-docx Word doc builder (13 sections)
│   ├── dashboard/
│   │   └── app.py               # Streamlit 5-view dashboard
│   └── pipeline.py              # Main orchestrator (15 steps)
├── tests/
│   ├── test_template_parser.py  # 20 tests
│   ├── test_pii_redactor.py     # 27 tests
│   ├── test_sessionizer.py      # 16 tests
│   └── test_csat_joiner.py      # 10 tests  [72 total, all passing]
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Configuration

### 1. Connect to Real MongoDB

Edit `config/schema_mapping.yaml`:

```yaml
field_names:
  session_id: "conversationId"   # ← your actual Kore.ai field name
  user_id: "userId"
  timestamp: "createdAt"
  sender: "role"
  message: "content"

session_id_present: true         # true → Path A (native session_id)
                                 # false → Path B (30-min gap derivation)
data_source: production          # switches dashboard banner to green
```

Set `MONGO_URI` and `MONGO_MODE=mongo` in `.env`.

Run the schema validator:

```bash
python -m src.ingestion.mongo_connector --validate
```

### 2. Choose LLM Provider

In `config/llm_config.yaml`:

```yaml
active_client: anthropic   # or "azure_openai" or "mock"
```

Set the matching API key in `.env`.

> ⚠️ **Azure Warning**: Banking transcript content (fraud, blocked, suspicious) frequently triggers Azure's content filter (HTTP 400). Prefer `anthropic` for initial runs.

### 3. Scoring Weights

Edit `config/taxonomies.yaml` under `scoring_weights:` to tune the composite score formula:

```yaml
scoring_weights:
  volume_share: 0.25
  leakage_rate: 0.25
  remediability: 0.20
  cbp_capability_fit: 0.15
  csat_signal: 0.10
  regulatory_complexity_penalty: 0.05
```

These same weights are exposed as sliders in Dashboard View 5 (What-If Scoring).

---

## Pipeline Steps

| Step | Module | Description |
|------|--------|-------------|
| 1 | `mongo_connector` | Ingest transcripts + CSAT from source |
| 2 | `pii_redactor` | Redact Emirates ID, cards, IBANs, mobiles, email |
| 3 | `sessionizer` | Group rows into sessions (Path A or B) |
| 4 | `cost_estimator` | Print cost estimate; wait for `YES` |
| 5 | `classifier` Stage 1 | Journey classification (all sessions) |
| 6 | `classifier` Stage 2 | Transfer reason (escalated sessions) |
| 7 | `classifier` Stage 3 | Failure mode deep-coding (stratified sample) |
| 8 | `classifier` Stage 4 | Remediation assignment (per journey) |
| 9 | `leakage_map` | Per-journey escalation rates and slices |
| 10 | `csat_joiner` | Direct + fuzzy CSAT join |
| 11 | `temporal_trends` | Weekly rates + step-change detection |
| 12 | `agent_division_of_labor` | Bot vs. agent outcome breakdown |
| 13 | `knowledge_transfer_finder` | Agent explanation clustering |
| 14 | `rank_journeys` | 5-dimension composite scoring → shortlist |
| 15 | `per_journey_doc` | Word docs for top 10 journeys |

---

## LLM Classification Stages

### Stage 1 — Journey Classification
- **Input**: Every session (all 10,000)
- **Prompt**: `prompts/journey_classifier_v1.txt` — 90-journey taxonomy
- **Output**: `journey_id`, `confidence` (1–5), one-sentence `reasoning`

### Stage 2 — Transfer Reason
- **Input**: Only sessions flagged as transferred (~22% = ~2,200)
- **Prompt**: `prompts/transfer_reason_classifier_v1.txt` — 9 UAE banking codes
- **Output**: `transfer_reason_code` (e.g., `bot_has_data_communicates_poorly`, `api_failure`, `auth_fail`)

### Stage 3 — Failure Mode Deep-Coding
- **Input**: Stratified sample (~360 sessions, min 30 per journey)
- **Prompt**: `prompts/failure_mode_classifier_v1.txt`
- **Output**: `customer_intent`, `bot_failure_point`, `actual_blocker`, `what_would_have_prevented_escalation`

### Stage 4 — Remediation Assignment
- **Input**: One call per journey (synthesises Stage 3 outputs)
- **Prompt**: `prompts/remediation_type_classifier_v1.txt`
- **Output**: `recommended_remediations[]`, per-type confidence scores

---

## Scoring Formula

```
composite_score =
    0.25 × volume_share × 10
  + 0.25 × leakage_rate
  + 0.20 × remediability_score
  + 0.15 × cbp_capability_fit
  + 0.10 × csat_signal
  - 0.05 × regulatory_complexity
```

Where:
- `volume_share` — fraction of total sessions (higher = more customer impact)
- `leakage_rate` — % of sessions incorrectly escalated to agents
- `remediability_score` — difficulty of fixing the root cause (lower difficulty = higher score)
- `cbp_capability_fit` — how well current Kore.ai CBP can implement the fix
- `csat_signal` — CSAT deflection potential (low CSAT on escalated sessions = high signal)
- `regulatory_complexity` — penalty for UAE/CBUAE-regulated journeys requiring human sign-off

---

## Dashboard

```bash
streamlit run src/dashboard/app.py
# → http://localhost:8501
```

| View | What you see |
|------|-------------|
| **Top Priority Journeys** | Ranked list coloured green/amber/red. Click any journey to drill down. |
| **Per-Journey Deep Dive** | Failure mode bar chart, weekly trend line, CSAT signal. Download Word doc. |
| **Bot vs. Agent Division of Labor** | Stacked bar: correctly contained / incorrectly escalated / correctly escalated. |
| **Agent Knowledge Transfer** | Recurring post-handoff explanations. Filter by domain, sort by frequency. |
| **What-If Scoring** | Drag sliders to adjust dimension weights. See live re-ranking. Save/compare profiles. |
| **Spot-Check Review Queue** | Accuracy summary by journey. Agree / disagree / skip individual classifications. |

---

## Synthetic Data Baseline Results

Pipeline run on 10,000 sessions (dry-run, MockLLMClient):

| Metric | Value |
|--------|-------|
| Sessions processed | 10,000 |
| Transcript rows | 71,421 |
| CSAT responses | 1,200 |
| Sessions transferred to agent | 2,240 (22.4%) |
| Sessions deep-coded | 360 |
| CSAT join rate | 100% |
| Knowledge transfer opportunities | 52 |
| MVP shortlist journeys | 10 |

**Top 10 MVP Shortlist (synthetic baseline):**

| Rank | Journey | Domain | Sessions | Escalations | Rate | Score |
|------|---------|--------|----------|-------------|------|-------|
| 1 | CARD_001 | Cards | 824 | 206 | 25% | 0.563 |
| 2 | REMI_003 | Remittances | 885 | 199 | 22% | 0.552 |
| 3 | CARD_004 | Cards | 852 | 203 | 24% | 0.537 |
| 4 | ACCT_002 | Accounts | 781 | 178 | 23% | 0.531 |
| 5 | REMI_001 | Remittances | 828 | 192 | 23% | 0.530 |
| 6 | ACCT_003 | Accounts | 824 | 182 | 22% | 0.527 |
| 7 | LOAN_001 | Loans | 869 | 178 | 20% | 0.525 |
| 8 | DIGI_001 | Digital | 841 | 206 | 24% | 0.522 |
| 9 | ACCT_001 | Accounts | 803 | 167 | 21% | 0.515 |
| 10 | CARD_011 | Cards | 835 | 163 | 20% | 0.506 |

> These ranks will shift significantly when run against real transcripts with actual LLM classification.

---

## Running Tests

```bash
# All 72 tests
venv/bin/pytest tests/ -v

# Individual modules
venv/bin/pytest tests/test_template_parser.py -v    # 20 tests
venv/bin/pytest tests/test_pii_redactor.py -v       # 27 tests
venv/bin/pytest tests/test_sessionizer.py -v        # 16 tests
venv/bin/pytest tests/test_csat_joiner.py -v        # 10 tests
```

---

## PII Redaction

The redactor covers:

| PII Type | Example | Replacement |
|----------|---------|-------------|
| Emirates ID | `784-1990-1234567-1` | `[EMIRATES_ID]` |
| UAE mobile | `+971 50 123 4567` | `[MOBILE]` |
| Credit/debit card | `4532 1234 5678 9010` | `[CARD]` |
| IBAN | `AE07 0331 2345 6789 0123 456` | `[IBAN]` |
| Account number | `1234567890` (10–12 digits) | `[ACCOUNT]` |
| Email | `customer@email.com` | `[EMAIL]` |

**Not redacted**: merchant names, AED/EGP/PKR amounts, banking product names.

---

## Key Design Decisions

1. **Dual-path sessionization** — Path A (native `session_id`) and Path B (30-min gap derivation) are both implemented. Switch via `config/schema_mapping.yaml`. See `docs/KNOWN_LIMITATIONS.md §3`.

2. **Luhn validation for card numbers** — Avoids false positives on 16-digit reference numbers. Only valid Luhn checksums are redacted as cards.

3. **MockLLMClient for cost-free validation** — Use `--dry-run` or `MOCK_LLM=true` for pipeline integration testing before spending API budget.

4. **All classification is LLM-based, no keyword rules** — Ensures the pipeline generalises across Arabic/English and future journey additions without hardcoded patterns.

5. **Spot-check, not ground-truth labels** — Accuracy is measured by human agreement on a random sample. Target: ≥ 85% agreement per journey.

---

## Known Limitations

See `docs/KNOWN_LIMITATIONS.md` for the full list. Key items:

- Arabic transcripts not yet supported in prompts
- Knowledge transfer uses word-overlap heuristic (not sentence-transformers)
- Azure OpenAI content filters frequently block banking content
- `cbp_capability_fit` values are expert-assigned, not LLM-computed
- No authentication on Streamlit dashboard

---

## Handover Checklist for Dev Team

- [ ] Run `python -m src.ingestion.mongo_connector --validate`
- [ ] Update all [VERIFY] items in `docs/SCHEMA_CONTRACT.md`
- [ ] Set `session_id_present` correctly in `schema_mapping.yaml`
- [ ] Choose LLM provider and set API key in `.env`
- [ ] Run `--dry-run` end-to-end successfully
- [ ] Run spot-check on 50 sessions (target ≥ 85% accuracy)
- [ ] Delete `data/synthetic/` from production environment
- [ ] Set `data_source: production` in `schema_mapping.yaml`
- [ ] Confirm dashboard shows green PRODUCTION DATA banner

---

## License

Internal use only — Mashreq Bank CBP Team.
Not for external distribution.

---

*Built for the Mashreq CBP Analysis Sprint · May 2026*
