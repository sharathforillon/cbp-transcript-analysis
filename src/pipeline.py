"""
pipeline.py — Main CBP Transcript Analysis Pipeline orchestrator.

Runs all analysis stages in order:
  1. Ingest (load from Mongo or CSV)
  2. Sessionize (Path A or B)
  3. PII Redaction
  4. Journey Classification (LLM)
  5. Transfer Reason Classification (LLM)
  6. Failure Mode Deep Coding (LLM, stratified sample)
  7. Remediation Assignment (LLM, per journey)
  8. Leakage Map
  9. CSAT Join
  10. Temporal Trends
  11. Division of Labor
  12. Knowledge Transfer
  13. Scoring + MVP Shortlist
  14. Per-Journey Word Docs

Usage:
  python -m src.pipeline --sessions 1000 --dry-run     # Validation run, no LLM calls
  python -m src.pipeline --source full                 # Full pipeline on 10K sessions
  python -m src.pipeline --source sample               # Quick run on 500-session sample
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "outputs"


def run_pipeline(
    source: str = "sample",
    limit: int = None,
    dry_run: bool = False,
    skip_docs: bool = False,
    auto_confirm: bool = False,
    max_classify: int = None,
) -> dict:
    """
    Run the full CBP transcript analysis pipeline.

    Args:
        source: 'sample' (500 sessions) or 'full' (10K sessions)
        limit: Override session count (for validation runs)
        dry_run: If True, skip LLM calls (use MockLLMClient)
        skip_docs: Skip Word document generation
        auto_confirm: Skip cost confirmation prompt
        max_classify: POC cap — randomly sample this many sessions for Stage 1
                      journey classification. Downstream stages (transfer reason,
                      failure mode) automatically scope to the classified subset.
                      Default: None (classify all sessions).

    Returns:
        dict with pipeline run summary.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if dry_run:
        os.environ["MOCK_LLM"] = "true"
        logger.info("DRY RUN mode — no real LLM calls will be made.")

    # ---- IMPORTS ----
    from src.ingestion.mongo_connector import get_connector
    from src.ingestion.sessionizer import sessionize
    from src.redaction.pii_redactor import redact_fields
    from src.coding.llm_client import get_llm_client
    from src.coding.cost_estimator import estimate_and_confirm
    from src.coding.classifier import (
        run_journey_classification,
        run_transfer_reason_classification,
        run_failure_mode_coding,
        run_remediation_assignment,
        save_classifications,
        load_classifications,
    )
    from src.analysis.leakage_map import build_leakage_map
    from src.analysis.csat_joiner import join_csat
    from src.analysis.temporal_trends import build_temporal_trends
    from src.analysis.agent_division_of_labor import build_division_of_labor
    from src.analysis.knowledge_transfer_finder import build_knowledge_transfer_map
    from src.scoring.rank_journeys import score_journeys

    # ---- STEP 1: INGEST ----
    logger.info("=" * 60)
    logger.info("STEP 1: Ingesting transcript data (source=%s)", source)
    connector = get_connector(source=source)
    transcript_rows = list(connector.iter_transcript_rows(limit=limit))
    csat_rows = list(connector.iter_csat_rows())
    logger.info("Loaded %d transcript rows, %d CSAT rows", len(transcript_rows), len(csat_rows))

    # ---- STEP 2: PII REDACTION ----
    logger.info("STEP 2: Applying PII redaction...")
    for row in transcript_rows:
        redact_fields(row, ["bot_message", "user_message"])
    for row in csat_rows:
        redact_fields(row, ["comments"])
    logger.info("PII redaction complete.")

    # ---- STEP 3: SESSIONIZE ----
    logger.info("STEP 3: Sessionizing transcripts...")
    import yaml
    with open(REPO_ROOT / "config" / "schema_mapping.yaml") as f:
        schema_cfg = yaml.safe_load(f)
    session_id_present = schema_cfg.get("transcripts", {}).get("session_id_present", True)

    sessions = sessionize(transcript_rows, session_id_present=session_id_present)
    logger.info("Sessionized into %d sessions (path=%s)", len(sessions), "native" if session_id_present else "derived")

    # Attach geography to sessions from original rows
    geo_by_session: dict[str, str] = {}
    for row in transcript_rows:
        sid = row.get("session_id", "")
        if sid and sid not in geo_by_session:
            # Geography is embedded via _synthetic marker or can be derived from language
            lang = row.get("language", "en")
            if lang == "ar":
                geo_by_session[sid] = "Egypt/UAE"
            elif lang == "ur":
                geo_by_session[sid] = "Pakistan"
            else:
                geo_by_session[sid] = "UAE"

    for session in sessions:
        if not hasattr(session, "geography"):
            object.__setattr__(session, "geography", geo_by_session.get(session.session_id, "Unknown"))

    # ---- STEP 4: COST ESTIMATION ----
    n_transferred_estimate = int(len(sessions) * 0.30)
    if not dry_run and not auto_confirm:
        confirmed = estimate_and_confirm(
            n_total_sessions=len(sessions),
            n_transferred_sessions=n_transferred_estimate,
            auto_confirm=auto_confirm,
            n_classify_sessions=max_classify,
        )
        if not confirmed:
            logger.info("Pipeline cancelled by user at cost confirmation.")
            return {"status": "cancelled"}
    else:
        logger.info("Skipping cost confirmation (dry_run=%s, auto_confirm=%s)", dry_run, auto_confirm)

    # ---- STEP 5-8: LLM CLASSIFICATION ----
    logger.info("STEP 5-8: LLM classification stages...")
    llm_client = get_llm_client()

    journey_results = run_journey_classification(sessions, client=llm_client, max_sessions=max_classify)
    transfer_results = run_transfer_reason_classification(sessions, journey_results, client=llm_client)
    failure_mode_results = run_failure_mode_coding(sessions, journey_results, transfer_results, client=llm_client)
    remediation_results = run_remediation_assignment(journey_results, transfer_results, failure_mode_results, client=llm_client)

    save_classifications(journey_results, transfer_results, failure_mode_results, remediation_results)

    # ---- STEP 9: LEAKAGE MAP ----
    logger.info("STEP 9: Building leakage map...")
    leakage = build_leakage_map(sessions, journey_results, transfer_results)

    # ---- STEP 10: CSAT JOIN ----
    logger.info("STEP 10: Joining CSAT...")
    csat_result = join_csat(sessions, csat_rows)
    # Enrich CSAT join with journey_ids
    for record in csat_result["joined"]:
        sid = record.get("session_id")
        record["journey_id"] = journey_results.get(sid, {}).get("journey_id")

    # ---- STEP 11: TEMPORAL TRENDS ----
    logger.info("STEP 11: Computing temporal trends...")
    trends = build_temporal_trends(sessions, journey_results)

    # ---- STEP 12: DIVISION OF LABOR ----
    logger.info("STEP 12: Computing division of labor...")
    labor = build_division_of_labor(sessions, journey_results, transfer_results)

    # ---- STEP 13: KNOWLEDGE TRANSFER ----
    logger.info("STEP 13: Finding knowledge transfer opportunities...")
    kt = build_knowledge_transfer_map(sessions, journey_results)

    # ---- STEP 14: SCORING ----
    logger.info("STEP 14: Scoring and ranking journeys...")
    # In POC mode (--max-classify N), scale the minimum volume threshold so
    # that journeys with few classified sessions are still ranked.
    poc_min_volume = None
    if max_classify:
        total_sessions_count = len(sessions)
        sample_fraction = max_classify / total_sessions_count if total_sessions_count else 1.0
        poc_min_volume = max(1, int(500 * sample_fraction))  # floor at 1 for very small POC samples
        logger.info("POC mode: scaling min_volume threshold to %d (%.1f%% sample)", poc_min_volume, sample_fraction * 100)
    scores = score_journeys(leakage, csat_result, remediation_results, min_volume_override=poc_min_volume)

    # ---- STEP 15: WORD DOCS ----
    if not skip_docs:
        logger.info("STEP 15: Generating Word documents...")
        try:
            from src.reporting.per_journey_doc import generate_journey_reports
            mvp_shortlist = scores.get("mvp_shortlist", [])
            generated = generate_journey_reports(mvp_shortlist, llm_client=llm_client)
            logger.info("Generated %d Word documents", len(generated))
        except ImportError as e:
            logger.warning("Word doc generation skipped: %s", e)
    else:
        logger.info("STEP 15: Skipping Word doc generation (--skip-docs)")

    # ---- SUMMARY ----
    summary = {
        "status": "complete",
        "sessions_processed": len(sessions),
        "transcript_rows": len(transcript_rows),
        "csat_rows": len(csat_rows),
        "journeys_classified": len(journey_results),
        "sessions_transferred": len(transfer_results),
        "sessions_deep_coded": len(failure_mode_results),
        "journeys_remediation_assigned": len(remediation_results),
        "csat_join_rate_pct": csat_result["summary"]["join_rate_pct"],
        "knowledge_transfer_opportunities": kt["summary"]["total_opportunities"],
        "mvp_shortlist_count": len(scores.get("mvp_shortlist", [])),
        "top_10_mvp": [
            {
                "rank": j.get("mvp_rank"),
                "journey_id": j.get("journey_id"),
                "journey_name": j.get("journey_name"),
                "escalations": j.get("escalated_sessions"),
                "rate": f"{j.get('escalation_rate', 0):.0%}",
                "score": j.get("composite_score"),
                "primary_remediation": j.get("primary_remediation"),
            }
            for j in scores.get("mvp_shortlist", [])[:10]
        ],
    }

    summary_path = OUTPUT_DIR / "pipeline_summary.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE. Summary written to %s", summary_path)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CBP Transcript Analysis Pipeline")
    parser.add_argument("--source", choices=["sample", "full"], default="sample")
    parser.add_argument("--sessions", type=int, default=None, help="Limit sessions (for validation)")
    parser.add_argument("--dry-run", action="store_true", help="Use MockLLMClient, no API calls")
    parser.add_argument("--skip-docs", action="store_true", help="Skip Word document generation")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm cost estimation")
    parser.add_argument(
        "--max-classify", type=int, default=None,
        help="POC mode: cap Stage 1 journey classification at N sessions (e.g. --max-classify 100)"
    )
    args = parser.parse_args()

    summary = run_pipeline(
        source=args.source,
        limit=args.sessions,
        dry_run=args.dry_run,
        skip_docs=args.skip_docs,
        auto_confirm=args.yes,
        max_classify=args.max_classify,
    )

    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        if k != "top_10_mvp":
            print(f"  {k}: {v}")

    if summary.get("top_10_mvp"):
        print("\n  TOP 10 MVP JOURNEYS:")
        for j in summary["top_10_mvp"]:
            print(f"    #{j['rank']:2d} {j['journey_id']:10s} {j['journey_name'][:35]:35s}  {j['escalations']:4d} escalations ({j['rate']})  score={j['score']:.3f}")


if __name__ == "__main__":
    main()
