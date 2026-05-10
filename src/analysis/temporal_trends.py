"""
temporal_trends.py — Weekly escalation rate trends per journey over 6 months.

Detects step-changes (week-over-week change > 15 percentage points).
Outputs data/outputs/temporal_trends.json.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from src.ingestion.sessionizer import ReconstructedSession
from src.ingestion.template_parser import parse_bot_message, is_transfer_signal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "outputs" / "temporal_trends.json"

STEP_CHANGE_THRESHOLD_PP = 15  # percentage points


def _week_key(dt: datetime) -> str:
    """Return ISO year-week string e.g. '2025-W48'."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def build_temporal_trends(
    sessions: list[ReconstructedSession],
    journey_classifications: dict[str, dict],
    output_path: Optional[Path] = None,
) -> dict:
    """
    Compute weekly leakage rates per journey.

    Returns dict with per-journey weekly timeseries and step-change flags.
    """
    # Accumulate per-journey per-week
    weekly: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"total": 0, "escalated": 0}))

    for session in sessions:
        sid = session.session_id
        jid = journey_classifications.get(sid, {}).get("journey_id", "INTL_005")
        week = _week_key(session.session_start)

        weekly[jid][week]["total"] += 1

        # Check for escalation
        for msg in session.messages:
            parsed = parse_bot_message(msg.get("bot_message", ""))
            if is_transfer_signal(parsed):
                weekly[jid][week]["escalated"] += 1
                break

    # Build sorted timeseries per journey
    result_journeys = []

    for journey_id, weeks in weekly.items():
        sorted_weeks = sorted(weeks.keys())
        timeseries = []

        for week in sorted_weeks:
            total = weeks[week]["total"]
            escalated = weeks[week]["escalated"]
            rate = round(escalated / total * 100, 1) if total > 0 else 0
            timeseries.append({"week": week, "total": total, "escalated": escalated, "rate_pct": rate})

        # Detect step-changes
        step_changes = []
        for i in range(1, len(timeseries)):
            prev = timeseries[i - 1]["rate_pct"]
            curr = timeseries[i]["rate_pct"]
            delta = curr - prev
            if abs(delta) >= STEP_CHANGE_THRESHOLD_PP:
                step_changes.append({
                    "week": timeseries[i]["week"],
                    "from_rate_pct": prev,
                    "to_rate_pct": curr,
                    "delta_pp": round(delta, 1),
                    "direction": "increase" if delta > 0 else "decrease",
                })

        journey_name = ""
        for sid, clf in journey_classifications.items():
            if clf.get("journey_id") == journey_id:
                journey_name = clf.get("journey_name", "")
                break

        result_journeys.append({
            "journey_id": journey_id,
            "journey_name": journey_name,
            "weeks_with_data": len(timeseries),
            "timeseries": timeseries,
            "step_changes": step_changes,
            "has_step_change": len(step_changes) > 0,
        })

    result_journeys.sort(key=lambda x: -x["weeks_with_data"])

    result = {
        "analysis_window": {
            "start": "2025-11-01",
            "end": "2026-04-30",
            "step_change_threshold_pp": STEP_CHANGE_THRESHOLD_PP,
        },
        "journeys": result_journeys,
        "journeys_with_step_change": sum(1 for j in result_journeys if j["has_step_change"]),
    }

    out_path = output_path or OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Temporal trends written to %s (%d journeys)", out_path, len(result_journeys))
    return result
