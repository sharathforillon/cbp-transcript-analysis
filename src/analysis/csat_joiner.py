"""
csat_joiner.py — Fuzzy join CSAT survey responses to sessions.

JOIN LOGIC:
  Preferred (HIGH confidence):  Direct session_id match (if both have it)
  Fallback (MEDIUM/LOW):        Closest session end time within 60-min window before CSAT

Outputs data/outputs/csat_joined.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.ingestion.sessionizer import ReconstructedSession

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "outputs" / "csat_joined.json"
DEFAULT_WINDOW_MINUTES = 60

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str.strip(), TIMESTAMP_FORMAT)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def join_csat(
    sessions: list[ReconstructedSession],
    csat_rows: list[dict],
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    output_path: Optional[Path] = None,
) -> dict:
    """
    Join CSAT responses to sessions.

    Returns summary dict + writes output JSON.
    """
    window = timedelta(minutes=window_minutes)

    # Index sessions by session_id and by user_id
    session_by_id: dict[str, ReconstructedSession] = {}
    sessions_by_user: dict[str, list[ReconstructedSession]] = {}

    for s in sessions:
        session_by_id[s.session_id] = s
        sessions_by_user.setdefault(s.user_id, []).append(s)

    joined: list[dict] = []
    unjoined: list[dict] = []
    join_confidence_counts = {"high": 0, "medium": 0, "low": 0}

    for csat in csat_rows:
        user_id = csat.get("user_id", "")
        csat_session_id = csat.get("session_id", "")
        csat_ts = _parse_ts(csat.get("date_time", ""))
        score = csat.get("score")
        comment = csat.get("comments", "")

        # Attempt direct join via session_id
        if csat_session_id and csat_session_id in session_by_id:
            session = session_by_id[csat_session_id]
            joined.append({
                "csat_user_id": user_id,
                "csat_session_id": csat_session_id,
                "session_id": session.session_id,
                "journey_id": None,  # Filled by downstream
                "score": score,
                "comments": comment,
                "csat_date_time": csat.get("date_time", ""),
                "session_end": session.session_end.strftime(TIMESTAMP_FORMAT) if session.session_end else None,
                "join_confidence": "high",
                "join_method": "direct_session_id",
                "channel": csat.get("channel", ""),
                "language": csat.get("language", ""),
            })
            join_confidence_counts["high"] += 1
            continue

        # Fuzzy join: find user's sessions ending within window before CSAT
        user_sessions = sessions_by_user.get(user_id, [])
        if not user_sessions or not csat_ts:
            unjoined.append(csat)
            continue

        candidates = []
        for s in user_sessions:
            if s.session_end and s.session_end <= csat_ts and (csat_ts - s.session_end) <= window:
                candidates.append(s)

        if not candidates:
            unjoined.append(csat)
            continue

        # Pick the most recent session end before CSAT
        best = max(candidates, key=lambda s: s.session_end)
        confidence = "medium" if len(candidates) == 1 else "low"
        join_confidence_counts[confidence] += 1

        joined.append({
            "csat_user_id": user_id,
            "csat_session_id": csat_session_id,
            "session_id": best.session_id,
            "journey_id": None,
            "score": score,
            "comments": comment,
            "csat_date_time": csat.get("date_time", ""),
            "session_end": best.session_end.strftime(TIMESTAMP_FORMAT),
            "join_confidence": confidence,
            "join_method": "fuzzy_temporal",
            "n_candidates": len(candidates),
            "channel": csat.get("channel", ""),
            "language": csat.get("language", ""),
        })

    total = len(csat_rows)
    n_joined = len(joined)
    n_unjoined = len(unjoined)

    result = {
        "summary": {
            "total_csat_responses": total,
            "joined": n_joined,
            "unjoined": n_unjoined,
            "join_rate_pct": round(n_joined / total * 100, 1) if total > 0 else 0,
            "confidence_breakdown": join_confidence_counts,
            "window_minutes": window_minutes,
        },
        "joined": joined,
        "unjoined_count": n_unjoined,
    }

    out_path = output_path or OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(
        "CSAT join: %d/%d joined (%.1f%%) [high=%d, medium=%d, low=%d]",
        n_joined, total,
        result["summary"]["join_rate_pct"],
        join_confidence_counts["high"],
        join_confidence_counts["medium"],
        join_confidence_counts["low"],
    )
    return result
