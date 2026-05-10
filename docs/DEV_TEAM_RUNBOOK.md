# CBP Transcript Analysis — Dev Team Runbook

**Audience**: Engineers connecting this pipeline to the production Mashreq Kore.ai MongoDB.
**Last updated**: 2026-05-10

---

## 1. Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Python | 3.9+ | 3.11 recommended |
| MongoDB | 4.4+ | Cloud Atlas or on-prem |
| Anthropic SDK | 0.25+ | Only if using real LLM |
| python-docx | 0.8.11+ | Word doc generation |
| streamlit | 1.28+ | Dashboard |

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate       # macOS / Linux
venv\Scripts\activate          # Windows

# Install all dependencies
pip install -r requirements.txt
```

---

## 2. Connecting to Real MongoDB

### Step 1: Set Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required variables:

```
MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/<dbname>?retryWrites=true
MONGO_MODE=mongo       # "mock" for CSV files, "mongo" for real DB
```

### Step 2: Validate Schema

Run the schema validator before the full pipeline:

```bash
python -m src.ingestion.mongo_connector --validate
```

This will:
- Connect to MongoDB
- Print a sample raw transcript row
- Print a sample CSAT row
- List all field names found in the first 100 documents
- Flag any [VERIFY] fields from `docs/SCHEMA_CONTRACT.md` that are missing

**Expected output** (if schema matches):
```
✓ CONNECTED to MongoDB
✓ transcripts collection: 350,000+ documents
✓ csat collection: 45,000+ documents
✓ session_id field: PRESENT
✓ user_id field: PRESENT
...
```

**If fields are missing**, edit `config/schema_mapping.yaml` to remap:

```yaml
field_names:
  session_id: "conversationId"      # ← change right-hand side only
  user_id: "userId"
  timestamp: "createdAt"
  sender: "role"
  message: "content"
```

### Step 3: Set Session ID Mode

In `config/schema_mapping.yaml`:

```yaml
session_id_present: true    # true → Path A (native session_id grouping)
                            # false → Path B (30-min gap derivation)
```

Check with Kore.ai team whether `session_id` (or your remapped equivalent) is reliably populated.

---

## 3. Switching to Real LLM

### Option A: Anthropic Claude

```bash
# In .env:
ANTHROPIC_API_KEY=sk-ant-...

# In config/llm_config.yaml:
active_client: anthropic
```

### Option B: Azure OpenAI

Edit `config/llm_config.yaml`:

```yaml
active_client: azure_openai
azure_openai:
  endpoint: "https://<your-resource>.openai.azure.com/"
  api_version: "2024-02-01"
  deployment_name: "gpt-4o"
```

Set in `.env`:

```
AZURE_OPENAI_KEY=...
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
```

> ⚠️ **Azure Content Filter Warning**: Banking transcripts containing words like "block", "fraud", "suspicious", "overdraft" frequently trigger Azure's content safety filters. If you see HTTP 400 errors with `content_filter` in the response, switch to `anthropic` or request Azure to increase your content filter thresholds for this use-case.

### Cost Gate

Before any LLM run, the pipeline prints an estimated cost table:

```
Stage                  Calls    Input tok    Output tok    Est. cost
---------------------  -------  -----------  ------------  ----------
journey_classification 10,000   8,000,000    500,000       $26.25
transfer_reason        2,240    1,120,000    112,000       $4.20
failure_mode_coding    360      324,000      72,000        $1.58
remediation_assignment 90       180,000      90,000        $1.35
                                                           ----------
TOTAL ESTIMATED COST                                       $33.38
```

Type `YES` (uppercase) to proceed. Any other input aborts.

---

## 4. Running the Full Pipeline

### Dry Run (no LLM calls, no cost)

```bash
python -m src.pipeline --source full --dry-run --yes
```

Uses `MockLLMClient` — produces realistic output structure with randomized classifications. Use this to validate the pipeline end-to-end before spending API budget.

### Full Run

```bash
python -m src.pipeline --source full
```

The pipeline will:
1. Ingest from MongoDB (or CSV mock)
2. Redact PII from all transcript rows
3. Sessionize (Path A or B based on config)
4. Print cost estimate → wait for `YES`
5. Run 4-stage LLM classification
6. Run 5 analysis workstreams
7. Score and rank journeys
8. Generate per-journey Word docs for top 10
9. Write `data/outputs/pipeline_summary.json`

### Skip Doc Generation (faster iteration)

```bash
python -m src.pipeline --source full --skip-docs
```

### Resume from Partial Run

If the pipeline fails mid-way (e.g., LLM API timeout after 5,000 calls), classification outputs are saved to:

```
data/outputs/journey_classifications.json
data/outputs/transfer_classifications.json
data/outputs/failure_mode_classifications.json
data/outputs/remediation_assignments.json
```

Restart the pipeline — it will detect existing files and skip already-completed stages.

---

## 5. Spot-Check Workflow

After any LLM run, validate classification accuracy:

```bash
python -m src.coding.review_queue --sample 50
```

This opens an interactive terminal prompt. For each sample:
- You see: the raw transcript excerpt + the LLM's classification
- Type `a` (agree), `d` (disagree), or `s` (skip)

Results are saved to `data/outputs/review_log.json`.

In the dashboard, navigate to **Spot-Check Review Queue** to see per-journey accuracy.

**Target accuracy**: ≥ 85% agreement. If below, inspect the prompt files in `prompts/` and refine.

---

## 6. Output Files

All outputs land in `data/outputs/`:

| File | Contents | Used by |
|------|----------|---------|
| `leakage_map.json` | Per-journey escalation rates, transfer reason breakdown, slices | Dashboard View 1, 2, 3 |
| `csat_joined.json` | Sessions with matched CSAT scores, confidence levels | Scoring, Dashboard |
| `temporal_trends.json` | Weekly leakage rates, step-change alerts | Dashboard View 2 |
| `division_of_labor.json` | Bot contained/escalated/incorrect counts | Dashboard View 3 |
| `knowledge_transfer.json` | Agent explanation clusters with frequency | Dashboard View 4 |
| `scored_journeys.json` | Full scoring matrix for all journeys | Dashboard View 1, 5 |
| `mvp_shortlist.json` | Top 10 + runners-up with composite scores | Dashboard View 1 |
| `pipeline_summary.json` | Run summary: session counts, cost, top 10 | Gate reporting |
| `review_log.json` | Spot-check accuracy log | Dashboard Spot-Check |
| `journey_reports/` | Per-journey `.docx` files | Stakeholder delivery |

---

## 7. Dashboard

```bash
streamlit run src/dashboard/app.py
```

Opens at `http://localhost:8501`. Five views:

| View | Description |
|------|-------------|
| **Top Priority Journeys** | Ranked list, colour-coded by score; click any journey to drill down |
| **Per-Journey Deep Dive** | Failure mode chart, weekly trend, CSAT signal, download Word doc |
| **Bot vs. Agent Division of Labor** | Stacked bar: correctly contained / incorrectly escalated / correctly escalated |
| **Agent Knowledge Transfer** | Recurring agent explanations that the bot could learn |
| **What-If Scoring** | Adjust dimension weights live, see re-ranked list, save profiles |

The banner at the top shows **SYNTHETIC DATA** (red) or **PRODUCTION DATA** (green) based on `config/schema_mapping.yaml → data_source`.

---

## 8. Regenerating Synthetic Data

If you need a fresh synthetic dataset:

```bash
python -m src.ingestion.generate_synthetic --sessions 10000 --output data/synthetic/full
```

This overwrites:
- `data/synthetic/full/transcripts.csv`
- `data/synthetic/full/csat.csv`

---

## 9. Adding a New Journey

1. Open `config/taxonomies.yaml`
2. Under `journeys:`, add a new entry following the existing pattern:

```yaml
- id: "NEW_001"
  name: "New Journey Name"
  domain: "cards"          # cards/loans/accounts/wealth/remi/digital/complaints
  cbp_capability_fit: 0.8  # 0.0 to 1.0
  regulatory_complexity: 0.1
  remediation_difficulty: 0.3
```

3. Add matching keywords to `prompts/journey_classifier_v1.txt` in the taxonomy section.
4. Re-run the pipeline.

---

## 10. Updating Prompt Files

Prompt files live in `prompts/`. Each file has a `SYSTEM:` section (LLM role and output format) and a `USER:` section (the actual classification task with `{{PLACEHOLDER}}` slots).

To update a prompt:
1. Edit the `.txt` file
2. Run `--dry-run` to confirm the mock pipeline still runs cleanly
3. Run on a 100-session sample with real LLM and inspect `review_log.json`
4. If accuracy ≥ 85%, run the full pipeline

---

## 11. Common Issues

### Pipeline fails with `ModuleNotFoundError`
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### `pymongo.errors.ServerSelectionTimeoutError`
- Check `MONGO_URI` in `.env`
- Ensure your IP is whitelisted in MongoDB Atlas Network Access
- Try `mongosh "<MONGO_URI>"` to confirm connectivity

### `anthropic.AuthenticationError`
- Verify `ANTHROPIC_API_KEY` in `.env`
- Check key has not expired at console.anthropic.com

### Dashboard shows "No data files found"
- Run the pipeline at least once: `python -m src.pipeline --source full --dry-run --yes`
- Check `data/outputs/` exists and contains `mvp_shortlist.json`

### All sessions classified to one journey
- This is the MockLLMClient detection bug (fixed). Ensure you are on the latest version.
- Verify `MOCK_LLM` environment variable is not accidentally set to `true` in production.

---

## 12. Handover Checklist

Before handing this over to the Kore.ai / Mashreq data team:

- [ ] Run schema validator against production MongoDB
- [ ] Update all [VERIFY] items in `docs/SCHEMA_CONTRACT.md`
- [ ] Confirm `session_id_present` setting in `schema_mapping.yaml`
- [ ] Confirm LLM provider choice and set API key
- [ ] Run dry-run pipeline end-to-end successfully
- [ ] Run spot-check on 50 sessions, confirm ≥ 85% accuracy
- [ ] Delete `data/synthetic/` from production environment
- [ ] Set `data_source: production` in `schema_mapping.yaml`
- [ ] Confirm dashboard banner shows green PRODUCTION DATA banner
