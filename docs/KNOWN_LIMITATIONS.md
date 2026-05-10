# Known Limitations & Design Decisions

This document records known gaps, simplifications, and future work items for the CBP Transcript Analysis pipeline. Engineers should read this before extending the codebase.

---

## 1. Synthetic Data Limitations

### 1.1 Mock Journey Names
The synthetic dataset uses placeholder names like "Mock Journey CARD_001". When pointed at real MongoDB, journey names will be sourced from the actual Kore.ai intent classification. The journey taxonomy in `config/taxonomies.yaml` must be updated to match real intent labels before running LLM classification.

### 1.2 Single-Language Transcripts
Synthetic transcripts are English-only. Real Mashreq conversations include Arabic, with some code-switching (Arabic + English in same message). The LLM prompts have been written in English and tested only on English text. Arabic transcripts may require:
- Arabic prompt variants
- Per-language classification routing
- Code-switching handling in template parser

### 1.3 MockLLMClient Produces Uniform Output
The `MockLLMClient` assigns plausible-but-randomised classifications without reading the actual transcript content. Scores computed from mock classifications (CSAT signal, remediability) reflect synthetic distributions, not real failure patterns. Gate 2 composite scores from the dry run are directionally useful for pipeline validation but not analytically meaningful.

### 1.4 CSAT Join Rate (100% on Synthetic)
The synthetic CSAT data was generated with a 12% session-to-CSAT match rate, deliberately designed to allow 100% fuzzy join within the 60-minute window. Real production CSAT join rates are typically 8–20% (direct) and 15–35% (fuzzy). Low join rates degrade the `csat_signal` scoring dimension; journeys with fewer than 30 CSAT-matched sessions should be treated with caution.

---

## 2. PII Redaction Limitations

### 2.1 Merchant Names Not Redacted
The redactor deliberately does NOT redact merchant names (e.g., "SPICY GRAND RESTAURANT", "DAMAS JEWELLERY"). This is intentional — merchant names are operationally relevant for the account closure / dispute analysis use case. If the legal team determines merchant names constitute PII in context, add a pattern to `PATTERN_MERCHANT` in `pii_redactor.py`.

### 2.2 Amounts Not Redacted
AED/EGP/PKR currency amounts (e.g., "AED 347.00") are not redacted. These are needed for analysis but could be considered financially sensitive. Legal should review.

### 2.3 Arabic Name Patterns
Arabic personal names (e.g., "Mohammed Al-Rashidi") are not matched by the current regex suite. Adding Arabic name redaction requires either:
- A curated Arabic name list (NER approach)
- An Arabic-aware NER model (spaCy `ar_core_news_sm` or similar)

### 2.4 Luhn Validation False Negatives
The Luhn validator correctly rejects non-card 16-digit numbers (account numbers, reference numbers). However, some legitimate account reference numbers may accidentally pass Luhn validation (probability ~1/10). These would be incorrectly redacted as card numbers. Review `data/outputs/redaction_audit.json` for false positives.

### 2.5 Pakistan Mobile Prefix Coverage
Current Pakistan mobile prefixes covered: 030x, 031x, 032x, 033x, 034x, 035x. Telenor Pakistan uses 034x which is included. If new Pakistani virtual operators launch new prefix ranges, update `PATTERN_MOBILE` in `pii_redactor.py`.

---

## 3. Sessionization Limitations

### 3.1 30-Minute Gap Threshold (Path B)
The 30-minute session boundary (Path B, derived sessions) is a common industry default but has not been validated against Mashreq's actual idle timeout settings in Kore.ai. If Kore.ai uses a different timeout (e.g., 20 minutes or 45 minutes), update `gap_minutes` in `config/schema_mapping.yaml`.

### 3.2 Cross-Channel Sessions
A single customer may start a conversation in the mobile app and continue it on WhatsApp. The current sessionizer treats these as separate sessions (different `channel` values). Cross-channel session merging is not implemented and would require a customer identity resolution layer.

### 3.3 Bot Re-Entry After Agent Handoff
When an agent resolves a case and the customer re-enters the bot in the same Kore.ai session, the current implementation treats this as a continuation of the same session. This means a session can have both a bot-contained and a bot-escalated outcome. The final outcome is taken as the last recorded state.

---

## 4. LLM Classification Limitations

### 4.1 No Ground Truth Labels
This pipeline has no gold-standard labeled dataset. Accuracy is measured by human spot-check (agree/disagree against a sample). A proper evaluation would require labeling 500+ sessions manually. The 85% spot-check threshold is a pragmatic proxy.

### 4.2 Failure Mode Coding: Stratified Sample Only
Only ~360 sessions (stratified, ~3.6% of 10,000) receive deep failure mode coding. Journey-level failure mode summaries are extrapolated from this sample. For journeys with fewer than 30 coded sessions, confidence is LOW.

### 4.3 Prompt Brittleness
The prompts in `prompts/` are English-only and assume a Kore.ai banking context. If the bank adds new journeys, the journey taxonomy section in `journey_classifier_v1.txt` must be updated manually. There is no automated prompt-update mechanism.

### 4.4 Context Window for Long Sessions
The classifier truncates transcripts to the first 4,000 characters when building the LLM prompt. Very long sessions (e.g., 45+ turns) may have their tail truncated. The failure most likely to be missed is a late-session escalation trigger that appears after turn 30. If long sessions are common, increase the truncation limit in `classifier.py`.

### 4.5 Azure Content Filter
Azure OpenAI's content filters flag banking-specific language (fraud, suspicious, blocked card) as potentially harmful. This causes HTTP 400 errors mid-pipeline. The `AzureOpenAIClient` does not currently implement automatic retry with a sanitised prompt. Use Anthropic for initial runs.

---

## 5. Analysis Limitations

### 5.1 Knowledge Transfer: Word-Overlap Clustering
The `knowledge_transfer_finder.py` uses a naive word-overlap heuristic (Jaccard similarity) to cluster agent explanations. This produces coarse clusters and may:
- Merge semantically different explanations that share common banking words
- Split a single explanation theme into multiple small clusters

**Production recommendation**: Replace with `sentence-transformers` (`paraphrase-multilingual-mpnet-base-v2`) for embedding-based clustering with HDBSCAN.

### 5.2 Temporal Trends: Weekly Granularity
Step-change detection uses weekly aggregation (ISO week). For journeys with fewer than 50 sessions per week, weekly rates are noisy. The 15-percentage-point threshold may fire false positives. Consider using a 4-week rolling average for low-volume journeys.

### 5.3 CSAT Fuzzy Join: 60-Minute Window
The 60-minute temporal fuzzy join may match a CSAT response to the wrong session if a customer completes two sessions within an hour. The join selects the closest session in time, which is directionally correct but not guaranteed accurate.

### 5.4 Leakage Map: No Transfer-Back Detection
The pipeline does not detect cases where an agent resolves the issue and transfers the customer back to the bot (bot-agent-bot pattern). These sessions are currently classified as "correctly escalated" even though the agent handled the full resolution. Detecting transfer-back requires parsing the full conversation after the first transfer signal.

---

## 6. Scoring Limitations

### 6.1 Remediability Score Is Fixed
`remediability` is currently sourced from `config/taxonomies.yaml` as a static value per journey (e.g., `add_faq: 0.3`, `api_integration: 0.7`). After LLM remediation classification is working reliably, this should be replaced with the LLM-assigned remediability score from `remediation_assignments.json`.

### 6.2 CBP Capability Fit Is Expert-Assigned
`cbp_capability_fit` values in `config/taxonomies.yaml` were assigned by the analysis team. These should be reviewed by the Kore.ai technical team who know what the current CBP platform can and cannot do.

### 6.3 Composite Score Does Not Account for Implementation Effort
The scoring formula optimises for "what will reduce the most leakage" but does not penalise for implementation effort (e.g., a simple FAQ addition vs. a full API integration sprint). A future version should add an `implementation_effort_penalty` dimension.

---

## 7. Word Doc Generation Limitations

### 7.1 FAQ Library Is Heuristic
The FAQ library in each per-journey Word doc is generated from knowledge transfer clusters, not from real agent transcript excerpts. The questions are inferred, not verbatim. Do not present these to agents as canonical Q&As without human review.

### 7.2 Template Text for Non-Configured Journeys
Journeys not in the hardcoded narrative list (ACCT_004, DIGI_001) receive generic template text in the "Journey Narrative" section. This section should be updated manually or via LLM for each journey before stakeholder delivery.

### 7.3 Charts Are ASCII/Text Only
The Word docs do not embed charts (only tables). For charts, use the Streamlit dashboard (View 2 and 3) or export chart images manually and insert into the `.docx` files.

---

## 8. Infrastructure Limitations

### 8.1 No Database for Output Storage
All pipeline outputs are JSON/CSV files in `data/outputs/`. There is no write-back to MongoDB or a separate analytics database. If the pipeline is run repeatedly (e.g., monthly), outputs are overwritten. Add timestamped output directories if historical comparison is needed.

### 8.2 No Scheduling or Orchestration
The pipeline has no built-in scheduler. It is designed as a one-shot analysis tool. For recurring monthly runs, wrap `src/pipeline.py` in an Airflow/Prefect DAG or a cron job.

### 8.3 No Auth on Dashboard
The Streamlit dashboard has no authentication. In production, deploy behind Mashreq's internal SSO or use Streamlit Cloud's viewer authentication feature.

### 8.4 Single-Process Only
The pipeline runs in a single Python process. For datasets larger than 100,000 sessions, the LLM classification loop may take several hours. Consider parallelising Stage 1 (journey classification) with `concurrent.futures.ThreadPoolExecutor` — the LLM client is thread-safe.

---

## 9. Future Work (Prioritised)

| Priority | Item | Effort |
|----------|------|--------|
| P0 | Validate against real MongoDB schema | 1 day |
| P0 | Add Arabic transcript support to prompts | 2 days |
| P1 | Replace word-overlap with sentence-transformer clustering | 1 day |
| P1 | Add `implementation_effort` scoring dimension | 0.5 days |
| P1 | Embed charts in Word docs | 1 day |
| P2 | Add Streamlit authentication | 0.5 days |
| P2 | Add timestamped output directories for historical tracking | 0.5 days |
| P2 | Parallelise Stage 1 LLM classification | 1 day |
| P3 | Transfer-back detection (bot-agent-bot sessions) | 2 days |
| P3 | Cross-channel session merging | 3 days |
| P3 | Airflow DAG for monthly scheduling | 2 days |
