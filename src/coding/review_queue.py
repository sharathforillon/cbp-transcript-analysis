"""
review_queue.py — Human spot-check workflow for LLM classification validation.

For each journey, presents 20 random LLM-coded sessions for human review.
Tracks agree/disagree/skip decisions and computes per-journey LLM accuracy.

Storage: data/outputs/review_log.json
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
REVIEW_LOG_PATH = REPO_ROOT / "data" / "outputs" / "review_log.json"
REVIEW_SAMPLES_PER_JOURNEY = 20


# ---------------------------------------------------------------------------
# REVIEW LOG
# ---------------------------------------------------------------------------

def load_review_log(path: Optional[Path] = None) -> dict:
    """Load existing review log, or return empty structure."""
    log_path = path or REVIEW_LOG_PATH
    if log_path.exists():
        with open(log_path) as f:
            return json.load(f)
    return {"reviews": [], "accuracy_by_journey": {}}


def save_review_log(log: dict, path: Optional[Path] = None) -> None:
    log_path = path or REVIEW_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def add_review(
    log: dict,
    session_id: str,
    journey_id: str,
    llm_classification: dict,
    human_decision: str,  # "agree" | "disagree" | "skip"
    reviewer_note: str = "",
) -> dict:
    """Add a single review decision to the log."""
    log["reviews"].append({
        "session_id": session_id,
        "journey_id": journey_id,
        "llm_journey_id": llm_classification.get("journey_id"),
        "llm_transfer_reason": llm_classification.get("transfer_reason_code"),
        "llm_confidence": llm_classification.get("confidence"),
        "llm_reasoning": llm_classification.get("reasoning"),
        "human_decision": human_decision,
        "reviewer_note": reviewer_note,
        "reviewed_at": datetime.now(tz=timezone.utc).isoformat(),
    })
    # Recompute accuracy
    log["accuracy_by_journey"] = compute_accuracy(log)
    return log


def compute_accuracy(log: dict) -> dict[str, dict]:
    """
    Compute per-journey LLM precision from review log.
    precision = agreed / (agreed + disagreed) [skips excluded]
    """
    by_journey: dict[str, dict[str, int]] = {}
    for review in log["reviews"]:
        jid = review["journey_id"]
        if jid not in by_journey:
            by_journey[jid] = {"agree": 0, "disagree": 0, "skip": 0}
        decision = review["human_decision"]
        if decision in by_journey[jid]:
            by_journey[jid][decision] += 1

    accuracy = {}
    for jid, counts in by_journey.items():
        reviewed = counts["agree"] + counts["disagree"]
        precision = round(counts["agree"] / reviewed * 100, 1) if reviewed > 0 else None
        accuracy[jid] = {
            "agree": counts["agree"],
            "disagree": counts["disagree"],
            "skip": counts["skip"],
            "reviewed": reviewed,
            "precision_pct": precision,
            "confidence_tier": _confidence_tier(reviewed),
        }
    return accuracy


def _confidence_tier(n_reviewed: int) -> str:
    if n_reviewed >= 15:
        return "HIGH"
    elif n_reviewed >= 5:
        return "MEDIUM"
    else:
        return "LOW"


# ---------------------------------------------------------------------------
# SAMPLE BUILDER
# ---------------------------------------------------------------------------

def build_review_queue(
    sessions_by_journey: dict[str, list],
    journey_classifications: dict,
    transfer_classifications: dict,
    n_per_journey: int = REVIEW_SAMPLES_PER_JOURNEY,
) -> list[dict]:
    """
    Build a list of review items (one per session) for the spot-check UI.

    Each item contains the session, LLM classifications, and the raw transcript excerpt.
    """
    queue = []
    for journey_id, sessions in sessions_by_journey.items():
        sample = random.sample(sessions, min(n_per_journey, len(sessions)))
        for session in sample:
            sid = session.session_id
            journey_clf = journey_classifications.get(sid, {})
            transfer_clf = transfer_classifications.get(sid, {})

            # Build transcript excerpt (first 500 chars)
            transcript_lines = []
            for msg in session.messages[:6]:
                if msg.get("user_message"):
                    transcript_lines.append(f"USER: {msg['user_message'][:100]}")
                if msg.get("bot_message"):
                    bm = msg["bot_message"]
                    if bm.startswith("{"):
                        try:
                            import json
                            obj = json.loads(bm)
                            bm = obj.get("payload", {}).get("text", bm)[:100]
                        except Exception:
                            bm = bm[:100]
                    transcript_lines.append(f"BOT: {bm[:100]}")

            queue.append({
                "session_id": sid,
                "journey_id": journey_id,
                "user_id": session.user_id,
                "channel": session.channel,
                "language": session.language,
                "transcript_excerpt": "\n".join(transcript_lines),
                "llm_journey_id": journey_clf.get("journey_id"),
                "llm_journey_name": journey_clf.get("journey_name"),
                "llm_journey_confidence": journey_clf.get("confidence"),
                "llm_journey_reasoning": journey_clf.get("reasoning"),
                "llm_transfer_reason": transfer_clf.get("transfer_reason_code"),
                "llm_transfer_confidence": transfer_clf.get("confidence"),
                "llm_transfer_reasoning": transfer_clf.get("reasoning"),
                "reviewed": False,
            })

    random.shuffle(queue)
    return queue
