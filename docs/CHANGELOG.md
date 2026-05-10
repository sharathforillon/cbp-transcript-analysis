# Changelog

All notable changes to the CBP Transcript Analysis pipeline are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] — 2026-05-10 — Initial Build (Synthetic Baseline)

### Added

#### Data Layer
- `src/ingestion/generate_synthetic.py` — Synthetic transcript generator
  - 90-journey Pareto distribution (top 15 journeys carry ~60% traffic)
  - Realistic PII generation: Emirates ID, Luhn-valid cards, IBANs, UAE/Egypt/Pakistan mobiles
  - ACCT_004 encodes Reddy's account-closure failure pattern (merchant hold explanation)
  - Configurable session count (`--sessions`) and output path (`--output`)
- `src/ingestion/template_parser.py` — Kore.ai bot message parser
  - Handles plain text, JSON templates, malformed JSON (graceful fallback)
  - Template types: `welcome_template`, `quick_replies`, `live_agent`, `SYSTEM`
  - `is_transfer_signal()` for downstream escalation detection
- `src/ingestion/sessionizer.py` — Dual-path session reconstructor
  - Path A: native `session_id` grouping with user_id consistency checks
  - Path B: 30-min gap derivation with deterministic MD5-based session IDs
- `src/ingestion/mongo_connector.py` — MongoDB / CSV connector factory
  - `MockConnector` reads from CSV files (for development)
  - `MongoConnector` connects via `pymongo` using `MONGO_URI` env var
  - `--validate` CLI command for schema validation

#### Redaction
- `src/redaction/pii_redactor.py` — PII redactor
  - Patterns: Emirates ID, UAE/Egypt/Pakistan mobiles, Luhn-validated cards (spaced + plain), IBANs (AE/EG/PK), 10–12 digit account numbers, email addresses
  - Deliberately does NOT redact: merchant names, AED/EGP/PKR amounts, banking terms
  - Batch redaction via `redact_dataset()`
- `src/redaction/redaction_audit.py` — Recall audit against seeded synthetic PII

#### LLM Coding
- `src/coding/llm_client.py` — Pluggable LLM client
  - Abstract `LLMClient` base class
  - `AnthropicClient` — Claude claude-sonnet-4-5 via Anthropic SDK
  - `AzureOpenAIClient` — stub with TODO comments and Azure content filter warning
  - `MockLLMClient` — deterministic mock for dry-runs (no API calls)
- `src/coding/cost_estimator.py` — Pre-run cost estimator with YES gate
- `src/coding/classifier.py` — 4-stage classification pipeline
  - Stage 1: Journey classification (all sessions)
  - Stage 2: Transfer reason classification (escalated sessions only)
  - Stage 3: Failure mode deep-coding (stratified sample, min 30 per journey)
  - Stage 4: Remediation assignment (1 call per journey)
- `src/coding/review_queue.py` — Spot-check workflow with per-journey accuracy tracking

#### Analysis
- `src/analysis/leakage_map.py` — Per-journey escalation rates, transfer reason breakdown, slices by geography/language/channel
- `src/analysis/csat_joiner.py` — Direct join (HIGH confidence) + 60-min temporal fuzzy join (MEDIUM/LOW)
- `src/analysis/temporal_trends.py` — Weekly leakage rates with step-change detection (>15pp threshold)
- `src/analysis/agent_division_of_labor.py` — Bot correctly contained / incorrectly escalated / correctly escalated / agent slow metrics
- `src/analysis/knowledge_transfer_finder.py` — Post-handoff agent explanation clustering with feasibility scoring

#### Scoring
- `src/scoring/rank_journeys.py` — 5-dimension composite scoring
  - Dimensions: volume_share (0.25) + leakage_rate (0.25) + remediability (0.20) + cbp_capability_fit (0.15) + csat_signal (0.10) − regulatory_complexity_penalty (0.05)
  - Applies exclusion list and minimum volume thresholds from `config/taxonomies.yaml`

#### Reporting
- `src/reporting/per_journey_doc.py` — python-docx Word doc builder
  - 13-section template: Executive Summary, Source Data Snapshot, Journey Narrative, Operational Insights, Root Cause, Bot Feasibility, Proposed Bot Response, FAQ Library, Business Benefits, Implementation Roadmap, Success Metrics, Appendix (redacted transcripts), Final Recommendation
  - Hardcoded narrative for ACCT_004 (account closure) and DIGI_001 (digital onboarding)
  - Batch generation for top-N journeys

#### Pipeline Orchestrator
- `src/pipeline.py` — 15-step end-to-end orchestrator
  - CLI flags: `--source` (sample/full), `--sessions`, `--dry-run`, `--skip-docs`, `--yes`
  - Writes `data/outputs/pipeline_summary.json`
  - Idempotent: skips completed stages if output files already exist

#### Dashboard
- `src/dashboard/app.py` — Streamlit 5-view dashboard
  - View 1: Top Priority Journeys (ranked list, colour-coded, drill-down)
  - View 2: Per-Journey Deep Dive (failure mode chart, weekly trend, CSAT signal, Word doc download)
  - View 3: Bot vs. Agent Division of Labor (stacked bar + sortable table)
  - View 4: Agent Knowledge Transfer Opportunities (filterable, expandable)
  - View 5: What-If Scoring (weight sliders, live re-ranking, save profiles)
  - Spot-Check Review Queue (accuracy summary + agree/disagree/skip interface)
  - Synthetic/production banner based on `schema_mapping.yaml`

#### Configuration
- `config/taxonomies.yaml` — 90 journeys across 9 domains, transfer reason codes, remediation types with difficulty scores, exclusion list, scoring weights
- `config/schema_mapping.yaml` — Field name remapping, session path flag, transfer indicator types
- `config/llm_config.yaml` — Model selection, cost rates, Azure content filter warning

#### Prompts
- `prompts/journey_classifier_v1.txt` — Full 90-journey taxonomy, structured JSON output (confidence 1–5)
- `prompts/transfer_reason_classifier_v1.txt` — 9 UAE banking transfer reason codes
- `prompts/failure_mode_classifier_v1.txt` — Structured deep-dive: customer_intent, bot_failure_point, actual_blocker, prevention
- `prompts/remediation_type_classifier_v1.txt` — Per-journey synthesis to remediation recommendations

#### Documentation
- `docs/SCHEMA_CONTRACT.md` — Field-level contract with [VERIFY] callouts for dev team
- `docs/DEV_TEAM_RUNBOOK.md` — End-to-end setup, MongoDB connection, schema validation, common issues
- `docs/KNOWN_LIMITATIONS.md` — Known gaps, design decisions, future work
- `docs/CHANGELOG.md` — This file

#### Tests (72 passing)
- `tests/test_template_parser.py` — 20 tests
- `tests/test_pii_redactor.py` — 27 tests
- `tests/test_sessionizer.py` — 16 tests
- `tests/test_csat_joiner.py` — 10 tests

### Fixed
- Mobile phone regex (`+971 50 123 4567` multi-space format): changed `[-\s]?[0-9]{8,10}` to `(?:[\s\-]?\d){8,10}` to allow per-digit separators
- MockLLMClient detection: changed from checking `prompt` only to `combined = (prompt + " " + system).lower()` — fixes all-sessions-to-one-journey bug
- Template parser test: updated `test_malformed_json_partial_template` assertion to match correct `"[template]"` text output
- Stage 2 (transfer reason) session scoping: added `if session.session_id not in journey_results: continue` guard so Stage 2 only processes sessions classified in Stage 1 (critical for `--max-classify` POC mode)

### Added (post Gate 2)
- `--max-classify N` CLI flag: caps Stage 1 journey classification to N randomly-sampled sessions; Stages 2–4 automatically scope to that subset. Default: None (all sessions). Example: `--max-classify 100` costs ~$0.65 vs ~$33 for full run.
- `max_sessions` parameter on `run_journey_classification()` with random sampling + log message
- `n_classify_sessions` parameter on `compute_stage_estimates()`, `print_estimate()`, `estimate_and_confirm()` — shows POC banner and scales downstream stage call counts correctly

---

## [Unreleased] — Future Work

### Planned
- Arabic transcript support in LLM prompts
- Sentence-transformer-based knowledge transfer clustering (replace word-overlap heuristic)
- `implementation_effort` as 6th scoring dimension
- Embedded charts in Word docs (matplotlib → docx images)
- Streamlit authentication (Streamlit Cloud viewer auth or internal SSO proxy)
- Timestamped output directories for historical tracking
- Airflow DAG for monthly pipeline scheduling
- Parallelised Stage 1 LLM classification (ThreadPoolExecutor)
- Transfer-back detection (bot → agent → bot session patterns)
- Cross-channel session merging

### Pending Validation
- All [VERIFY] items in `docs/SCHEMA_CONTRACT.md` — pending real MongoDB access
- `session_id_present` setting — pending Kore.ai session field confirmation
- `cbp_capability_fit` values — pending Kore.ai platform capability review
- Arabic mobile prefix patterns — pending UAE TRA operator list review
