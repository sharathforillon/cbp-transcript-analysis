"""
redaction_audit.py — Run PII redaction over the full dataset and produce audit output.

Outputs:
  data/outputs/redaction_audit.json

The audit log contains:
  - Count of each PII type caught
  - Per-field breakdown
  - Recall estimate against seeded PII (for synthetic data)
  - Sample of what was redacted (without the actual PII values)
"""

from __future__ import annotations

import csv
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from src.redaction.pii_redactor import redact_text, redact_dataset

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = REPO_ROOT / "data"


def run_redaction_audit(
    transcript_path: Path,
    csat_path: Path,
    output_path: Path,
) -> dict:
    """
    Run PII redaction over transcript and CSAT CSVs.
    Writes audit JSON and returns the audit summary.
    """
    logger.info("Loading transcript data from %s", transcript_path)
    transcripts = _load_csv(transcript_path)
    logger.info("Loaded %d transcript rows", len(transcripts))

    logger.info("Loading CSAT data from %s", csat_path)
    csat = _load_csv(csat_path)
    logger.info("Loaded %d CSAT rows", len(csat))

    # Redact transcripts
    _, transcript_audit = redact_dataset(transcripts, ["bot_message", "user_message"])

    # Redact CSAT comments (critical — customers often include phone numbers here)
    _, csat_audit = redact_dataset(csat, ["comments"])

    # Combine
    combined_counts: dict[str, int] = {}
    for pii_type, cnt in transcript_audit["pii_by_type"].items():
        combined_counts[pii_type] = combined_counts.get(pii_type, 0) + cnt
    for pii_type, cnt in csat_audit["pii_by_type"].items():
        combined_counts[pii_type] = combined_counts.get(pii_type, 0) + cnt

    # Recall estimate (only meaningful for synthetic data)
    # Seeded: 1 of each per session in user_message fields
    n_sessions_transcript = _estimate_sessions(transcripts)
    n_csat = len(csat)
    seeded_estimates = {
        "emirates_id": n_sessions_transcript,
        "mobile": n_sessions_transcript,
        "card_number": n_sessions_transcript,
        "iban": n_sessions_transcript,
        "account_number": n_sessions_transcript,
        "email": 0,  # not seeded in synthetic data
    }

    recall_by_type = {}
    for pii_type, seeded in seeded_estimates.items():
        if seeded == 0:
            continue
        caught = combined_counts.get(pii_type, 0)
        recall_by_type[pii_type] = {
            "seeded": seeded,
            "caught": caught,
            "recall_pct": round(min(caught / seeded, 1.0) * 100, 1) if seeded > 0 else None,
        }

    audit = {
        "transcript_file": str(transcript_path),
        "csat_file": str(csat_path),
        "transcript_rows": transcript_audit["total_records"],
        "csat_rows": csat_audit["total_records"],
        "transcript_rows_with_pii": transcript_audit["records_with_pii"],
        "csat_rows_with_pii": csat_audit["records_with_pii"],
        "pii_counts_by_type": combined_counts,
        "transcript_pii_by_type": transcript_audit["pii_by_type"],
        "csat_pii_by_type": csat_audit["pii_by_type"],
        "total_pii_items_redacted": sum(combined_counts.values()),
        "recall_against_seeded": recall_by_type,
        "note": (
            "Recall estimates compare caught PII against seeded PII count per session. "
            "PII is not always present in every message (only where seeded); "
            "recall <100% means some seeded PII appeared in fields not targeted for redaction, "
            "or in contexts where the pattern didn't match. "
            "Actual PII values are NOT logged here — only counts and field references."
        ),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    logger.info("Redaction audit written to %s", output_path)

    return audit


def _load_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _estimate_sessions(transcript_rows: list[dict]) -> int:
    """Estimate number of unique sessions from transcript rows."""
    session_ids = set()
    for row in transcript_rows:
        sid = row.get("session_id", "")
        if sid:
            session_ids.add(sid)
    return len(session_ids) if session_ids else len(transcript_rows) // 7


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Run PII redaction audit")
    parser.add_argument("--source", choices=["sample", "full"], default="sample")
    args = parser.parse_args()

    src_dir = DATA_DIR / "synthetic" / args.source
    audit = run_redaction_audit(
        transcript_path=src_dir / "transcripts.csv",
        csat_path=src_dir / "csat.csv",
        output_path=DATA_DIR / "outputs" / "redaction_audit.json",
    )

    print(f"\nRedaction Audit Summary:")
    print(f"  Transcript rows:    {audit['transcript_rows']:,}")
    print(f"  CSAT rows:          {audit['csat_rows']:,}")
    print(f"  Total PII redacted: {audit['total_pii_items_redacted']:,}")
    print(f"\n  PII by type:")
    for pii_type, cnt in sorted(audit["pii_counts_by_type"].items(), key=lambda x: -x[1]):
        print(f"    {pii_type:25s}  {cnt:,}")
    print(f"\n  Recall against seeded PII:")
    for pii_type, r in audit["recall_against_seeded"].items():
        print(f"    {pii_type:25s}  {r['caught']:,} / {r['seeded']:,}  ({r['recall_pct']}%)")
