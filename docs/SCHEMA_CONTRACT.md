# SCHEMA CONTRACT — Kore.ai MongoDB Export
**CBP Transcript Analysis Pipeline**
Version: 1.0 | Date: 2026-05-10
Author: CBP Analytics Pipeline (synthetic build)

---

## PURPOSE

This document defines exactly what the analysis pipeline expects from the Kore.ai MongoDB export.
Every field marked **[VERIFY]** must be checked by the dev team against real Mongo before running the pipeline.
Fields marked **[DERIVED]** are computed by the pipeline — they do not need to be present in the source.

---

## HOW TO USE THIS DOCUMENT

1. Run `db.messages.findOne()` (or equivalent) against real Mongo.
2. Compare each field name below against what you actually see.
3. For any mismatch, update `config/schema_mapping.yaml` — do NOT edit the pipeline code.
4. Mark any [VERIFY] item as confirmed or flagged in your own copy of this doc.

---

## TABLE 1: TRANSCRIPTS COLLECTION

One document per **message turn** (one bot exchange = two rows: user_message + bot_message, or a single row with both).

**[VERIFY] Confirm the Mongo collection name.** The pipeline defaults to `messages`. Edit `schema_mapping.yaml` if different.

| Field | Type | Status | Description |
|-------|------|--------|-------------|
| `bot_name` | string | CONFIRMED (seen in export) | Name of the Kore.ai bot instance. E.g. "SmartAssist Instance". May be null for user-only turns. |
| `user_id` | string | CONFIRMED (seen in export) | Anonymised user identifier. E.g. "u-eee92f25-188d-591c-9713-84d8885...". Used as join key to CSAT. |
| `channel` | string | CONFIRMED (seen in export) | Source channel. E.g. "Web/Mobile Client". Also seen: "IVR", "WhatsApp". |
| `language` | string | CONFIRMED (seen in export) | ISO language code. E.g. "en", "ar", "ur". **[VERIFY] Is this user-declared or auto-detected?** Detection accuracy matters for dialect analysis. |
| `bot_message` | string | CONFIRMED (seen in export) | The bot's response. **WARNING:** May be plain text OR a JSON template payload stringified into this field (see Section 4 below). The pipeline handles both. |
| `user_message` | string | CONFIRMED (seen in export) | The customer's raw input. May be empty for bot-initiated turns. |
| `timestamp` | datetime (UTC) | CONFIRMED (seen in export) | Message timestamp. **[VERIFY] Confirm timezone. Pipeline assumes UTC. If stored as local Gulf time, all temporal analysis will be offset by UTC+4.** |
| `session_id` | string | **[VERIFY] — NOT SEEN IN EXCEL EXPORT** | Native session identifier from Kore.ai. **If present in Mongo but absent from the CSV export, set `transcripts.session_id_present: true` in `schema_mapping.yaml` and re-export with this field included.** If genuinely absent, the pipeline derives sessions from 30-minute time gaps (Path B sessionization). |
| `_id` | ObjectId | [VERIFY] Present in Mongo | Standard Mongo document ID. Used as `message_id` in audit logs. Not required in CSV export. |

### Fields NOT present in export — pipeline derives these via LLM

| [DERIVED] Field | How it's derived |
|-----------------|-----------------|
| `journey_id` | LLM classification on first 5 user messages |
| `journey_label` | Human-readable name from 90-journey taxonomy |
| `intent_confidence` | LLM confidence score 1-5 |
| `transfer_reason_code` | LLM classification on transferred sessions |
| `failure_mode` | LLM deep-coding on stratified sample |
| `remediation_type` | Aggregated across sampled sessions per journey |

---

## TABLE 2: CSAT COLLECTION

One document per CSAT survey response. Separate collection/file from transcripts.

**[VERIFY] Confirm the CSAT collection name / file format in your environment.**

| Field | Type | Status | Description |
|-------|------|--------|-------------|
| `user_id` | string | CONFIRMED | Same anonymised identifier as in transcripts. Primary join key. |
| `channel_id` | string | CONFIRMED | Channel-specific identifier. May differ from `channel` string. |
| `session_id` | string | CONFIRMED in CSAT | **[VERIFY] This field IS present in the CSAT export. Confirm it matches session_ids in the transcripts collection if `session_id` is also present there.** If both match, use direct join (preferred). |
| `channel` | string | CONFIRMED | Same as transcripts. |
| `language` | string | CONFIRMED | Same as transcripts. |
| `score` | integer 1-5 | CONFIRMED | CSAT score. 1 = very dissatisfied, 5 = very satisfied. **[VERIFY] Confirm scale direction. Some Kore.ai configs use 1-5 with 5=best; others use inverse.** |
| `date_time` | datetime (UTC) | CONFIRMED | When the CSAT response was submitted. **[VERIFY] Same timezone caveat as `timestamp` above.** |
| `comments` | string | CONFIRMED | Free-text customer comments. Sparse — roughly 30% response rate in this dataset. **IMPORTANT: Comments frequently contain PII (phone numbers, account numbers). The PII redactor runs on this field before any storage or analysis.** |

---

## JOIN LOGIC (CSAT ↔ TRANSCRIPTS)

The join is non-trivial because one user may have many sessions over 6 months, and CSAT is submitted some time after the session ends.

### Preferred Path (Direct Join — requires `session_id` in both collections)

```
MATCH ON: transcripts.session_id == csat.session_id
```

Use this path if `session_id` is present and consistent in both collections. Tag joined rows `join_confidence: HIGH`.

### Fallback Path (Fuzzy Temporal Join — when `session_id` absent from transcripts)

```
For each CSAT response:
  1. Filter transcripts to same user_id
  2. Compute session end timestamps
  3. Find sessions that ended BEFORE csat.date_time AND within 60 minutes before it
  4. If exactly one candidate: join, tag join_confidence: MEDIUM
  5. If multiple candidates: join to the most recent, tag join_confidence: LOW
  6. If no candidate in window: log as unjoined
```

**[VERIFY] The 60-minute window is configurable in `config/taxonomies.yaml` under `csat_join.window_minutes`.** Adjust if real-world CSAT submission patterns differ.

---

## SECTION 4: KORE.AI BOT MESSAGE FORMAT QUIRKS

The `bot_message` field is polymorphic. It may contain:

**Plain text:**
```
"Your current balance is AED 12,450.00."
```

**JSON template payload (stringified):**
```json
{"type":"template","payload":{"template_type":"welcome_template","text":"Welcome to Mashreq Bank..."}}
{"type":"template","payload":{"template_type":"quick_replies","text":"How can I help?","quick_replies":["Check Balance","Card Services","Speak to Agent"]}}
{"type":"template","payload":{"template_type":"live_agent","text":"Connecting you to an agent..."}}
{"type":"template","payload":{"template_type":"SYSTEM","text":"You are being transferred to the banking team."}}
```

**[VERIFY] Confirm all template_types used in your deployment.** The pipeline handles: `welcome_template`, `quick_replies`, `live_agent`, `SYSTEM`. Any new types will be logged as warnings and treated as plain text — they will NOT crash the pipeline.

**Critical for transfer detection:** `live_agent` and `SYSTEM` template types are the primary signals that a human handoff occurred. If your deployment uses different template_type names for transfers, update `config/schema_mapping.yaml` under `transfer_indicators`.

---

## AGENT DISPOSITION — NOT DELIVERED BY KORE.AI

**[KNOWN LIMITATION]** Kore.ai does not export agent disposition codes. There is no field like `agent_resolution_status` or `agent_outcome` in the transcript data.

Agent behavior must be inferred from the post-handoff portion of the transcript (messages that appear after a `live_agent` or `SYSTEM` transfer event). This inference is performed by LLM classification.

**[VERIFY] Does your Kore.ai deployment log post-handoff agent messages in the same `messages` collection?** If agent messages are stored in a separate system (e.g. Salesforce Service Cloud, Genesys), a separate data pull is required. Flag this to the dev team.

---

## FIELD NAME REMAPPING

If real Mongo uses different field names (e.g. `userId` instead of `user_id`), update `config/schema_mapping.yaml`. The pipeline reads field names from that config — no code changes needed.

Default mappings (no change needed if Mongo matches these exactly):

```yaml
transcripts:
  bot_name: bot_name
  user_id: user_id
  channel: channel
  language: language
  bot_message: bot_message
  user_message: user_message
  timestamp: timestamp
  session_id: session_id  # [VERIFY] may not exist

csat:
  user_id: user_id
  channel_id: channel_id
  session_id: session_id
  channel: channel
  language: language
  score: score
  date_time: date_time
  comments: comments
```

---

## [VERIFY] CHECKLIST FOR DEV TEAM

Run through this before starting the pipeline on real data:

- [ ] Collection name for transcripts matches `schema_mapping.yaml`
- [ ] Collection name for CSAT matches `schema_mapping.yaml`
- [ ] `timestamp` is stored as UTC (not Gulf local time)
- [ ] `date_time` in CSAT is stored as UTC
- [ ] `session_id` presence confirmed in transcripts collection (or confirmed absent)
- [ ] `session_id` in CSAT matches `session_id` in transcripts (if both present)
- [ ] `language` field is bot-detected or user-declared (document which)
- [ ] `bot_message` template_types in use — confirm `live_agent` and `SYSTEM` are the transfer signals
- [ ] Agent post-handoff messages are in the same collection (or separate system)
- [ ] `score` scale confirmed: 1=worst, 5=best
- [ ] CSAT response rate is roughly consistent with this build's 12% assumption
- [ ] PII policy confirmed: pipeline applies redaction before any output or LLM call

---

*This document was generated as part of the synthetic-data build. All [VERIFY] items represent assumptions made during development that MUST be validated before production use.*
