"""
leakage_map.py — Per-journey escalation / leakage analysis.

Outputs data/outputs/leakage_map.json with per-journey escalation rates,
transfer reason breakdowns, and cross-dimensional slices.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from src.ingestion.sessionizer import ReconstructedSession
from src.ingestion.template_parser import parse_bot_message, is_transfer_signal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "outputs" / "leakage_map.json"


def build_leakage_map(
    sessions: list[ReconstructedSession],
    journey_classifications: dict[str, dict],
    transfer_classifications: dict[str, dict],
    output_path: Optional[Path] = None,
) -> dict:
    """
    Build the leakage map from sessions + LLM classifications.

    Returns a dict with per-journey and aggregate statistics.
    """
    # Per-journey accumulators
    journey_stats: dict[str, dict] = defaultdict(lambda: {
        "journey_id": "",
        "journey_name": "",
        "total_sessions": 0,
        "escalated_sessions": 0,
        "transfer_reasons": defaultdict(int),
        "by_geography": defaultdict(lambda: {"total": 0, "escalated": 0}),
        "by_language": defaultdict(lambda: {"total": 0, "escalated": 0}),
        "by_channel": defaultdict(lambda: {"total": 0, "escalated": 0}),
    })

    for session in sessions:
        sid = session.session_id
        clf = journey_classifications.get(sid, {})
        journey_id = clf.get("journey_id", "INTL_005")
        journey_name = clf.get("journey_name", "Unknown")

        # Detect escalation
        is_escalated = sid in transfer_classifications
        if not is_escalated:
            # Also check via template parsing
            for msg in session.messages:
                parsed = parse_bot_message(msg.get("bot_message", ""))
                if is_transfer_signal(parsed):
                    is_escalated = True
                    break

        j = journey_stats[journey_id]
        j["journey_id"] = journey_id
        j["journey_name"] = journey_name
        j["total_sessions"] += 1

        geo = getattr(session, "geography", "") or "unknown"
        lang = session.language or "unknown"
        ch = session.channel or "unknown"

        j["by_geography"][geo]["total"] += 1
        j["by_language"][lang]["total"] += 1
        j["by_channel"][ch]["total"] += 1

        if is_escalated:
            j["escalated_sessions"] += 1
            j["by_geography"][geo]["escalated"] += 1
            j["by_language"][lang]["escalated"] += 1
            j["by_channel"][ch]["escalated"] += 1

            # Transfer reason breakdown
            tr = transfer_classifications.get(sid, {})
            reason = tr.get("transfer_reason_code", "unknown")
            j["transfer_reasons"][reason] += 1

    # Build final output
    journey_list = []
    total_sessions = 0
    total_escalated = 0

    for journey_id, stats in sorted(journey_stats.items(), key=lambda x: -x[1]["escalated_sessions"]):
        n = stats["total_sessions"]
        e = stats["escalated_sessions"]
        total_sessions += n
        total_escalated += e

        journey_list.append({
            "journey_id": journey_id,
            "journey_name": stats["journey_name"],
            "total_sessions": n,
            "escalated_sessions": e,
            "escalation_rate": round(e / n, 4) if n > 0 else 0,
            "transfer_reason_breakdown": dict(stats["transfer_reasons"]),
            "by_geography": {
                geo: {
                    "total": v["total"],
                    "escalated": v["escalated"],
                    "rate": round(v["escalated"] / v["total"], 4) if v["total"] > 0 else 0,
                }
                for geo, v in stats["by_geography"].items()
            },
            "by_language": {
                lang: {
                    "total": v["total"],
                    "escalated": v["escalated"],
                    "rate": round(v["escalated"] / v["total"], 4) if v["total"] > 0 else 0,
                }
                for lang, v in stats["by_language"].items()
            },
            "by_channel": {
                ch: {
                    "total": v["total"],
                    "escalated": v["escalated"],
                    "rate": round(v["escalated"] / v["total"], 4) if v["total"] > 0 else 0,
                }
                for ch, v in stats["by_channel"].items()
            },
        })

    result = {
        "summary": {
            "total_sessions": total_sessions,
            "total_escalated": total_escalated,
            "overall_escalation_rate": round(total_escalated / total_sessions, 4) if total_sessions > 0 else 0,
            "journeys_analyzed": len(journey_list),
        },
        "journeys": journey_list,
    }

    out_path = output_path or OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Leakage map written to %s (%d journeys)", out_path, len(journey_list))
    return result
