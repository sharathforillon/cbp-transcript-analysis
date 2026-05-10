"""
agent_division_of_labor.py — Per-journey bot vs. agent performance breakdown.

Outputs data/outputs/division_of_labor.json.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.ingestion.sessionizer import ReconstructedSession
from src.ingestion.template_parser import parse_bot_message, is_transfer_signal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "outputs" / "division_of_labor.json"

AGENT_SLOW_THRESHOLD_SECONDS = 300  # 5 minutes


def build_division_of_labor(
    sessions: list[ReconstructedSession],
    journey_classifications: dict[str, dict],
    transfer_classifications: dict[str, dict],
    output_path: Optional[Path] = None,
) -> dict:
    """
    Compute per-journey bot vs. agent division of labor metrics.
    """
    stats: dict[str, dict] = defaultdict(lambda: {
        "journey_id": "",
        "journey_name": "",
        "total_sessions": 0,
        "bot_correctly_contained": 0,
        "bot_correctly_escalated": 0,   # policy_routed
        "bot_incorrectly_escalated": 0, # could have been contained
        "agent_slow_to_resolve": 0,
        "escalation_rate": 0.0,
    })

    for session in sessions:
        sid = session.session_id
        clf = journey_classifications.get(sid, {})
        journey_id = clf.get("journey_id", "INTL_005")
        journey_name = clf.get("journey_name", "Unknown")

        s = stats[journey_id]
        s["journey_id"] = journey_id
        s["journey_name"] = journey_name
        s["total_sessions"] += 1

        # Is session escalated?
        is_escalated = False
        transfer_ts: Optional[datetime] = None
        agent_response_ts: Optional[datetime] = None

        for i, msg in enumerate(session.messages):
            parsed = parse_bot_message(msg.get("bot_message", ""))
            if is_transfer_signal(parsed):
                is_escalated = True
                ts_str = msg.get("timestamp", "")
                try:
                    transfer_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    pass

                # Look for agent response (next bot/user message after transfer)
                for subsequent in session.messages[i+1:]:
                    resp_ts_str = subsequent.get("timestamp", "")
                    try:
                        agent_response_ts = datetime.strptime(resp_ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                    if agent_response_ts:
                        break
                break

        if not is_escalated:
            s["bot_correctly_contained"] += 1
        else:
            # Determine if escalation was correct
            tr = transfer_classifications.get(sid, {})
            transfer_reason = tr.get("transfer_reason_code", "")

            if transfer_reason == "policy_routed":
                s["bot_correctly_escalated"] += 1
            else:
                s["bot_incorrectly_escalated"] += 1

            # Check if agent was slow to respond
            if transfer_ts and agent_response_ts:
                response_time = (agent_response_ts - transfer_ts).total_seconds()
                if response_time > AGENT_SLOW_THRESHOLD_SECONDS:
                    s["agent_slow_to_resolve"] += 1

    # Compute rates
    journey_list = []
    for jid, s in sorted(stats.items(), key=lambda x: -x[1]["total_sessions"]):
        n = s["total_sessions"]
        escalated = s["bot_correctly_escalated"] + s["bot_incorrectly_escalated"]
        s["escalation_rate"] = round(escalated / n, 4) if n > 0 else 0
        s["incorrect_escalation_rate"] = round(s["bot_incorrectly_escalated"] / n, 4) if n > 0 else 0
        journey_list.append(dict(s))

    result = {
        "summary": {
            "total_sessions": sum(s["total_sessions"] for s in stats.values()),
            "total_correctly_contained": sum(s["bot_correctly_contained"] for s in stats.values()),
            "total_correctly_escalated": sum(s["bot_correctly_escalated"] for s in stats.values()),
            "total_incorrectly_escalated": sum(s["bot_incorrectly_escalated"] for s in stats.values()),
            "total_agent_slow": sum(s["agent_slow_to_resolve"] for s in stats.values()),
        },
        "journeys": journey_list,
    }

    out_path = output_path or OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Division of labor written to %s", out_path)
    return result
