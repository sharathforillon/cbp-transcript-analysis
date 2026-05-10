"""
Unit tests for src/analysis/csat_joiner.py

Tests direct join, fuzzy join, ambiguous cases, and unjoined responses.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone
from src.analysis.csat_joiner import join_csat
from src.ingestion.sessionizer import ReconstructedSession


def _make_session(session_id, user_id, end_offset_hours=0):
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    end = base + timedelta(hours=end_offset_hours)
    return ReconstructedSession(
        session_id=session_id,
        user_id=user_id,
        channel="Web/Mobile Client",
        language="en",
        messages=[],
        session_start=base,
        session_end=end,
        message_count=5,
        path="native",
        derived=False,
    )


def _make_csat(user_id, session_id="", date_time_offset_hours=0.5, score=4):
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    dt = base + timedelta(hours=date_time_offset_hours)
    return {
        "user_id": user_id,
        "session_id": session_id,
        "channel": "Web/Mobile Client",
        "language": "en",
        "score": str(score),
        "date_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "comments": "",
    }


# ---------------------------------------------------------------------------
# DIRECT JOIN (HIGH confidence)
# ---------------------------------------------------------------------------

def test_direct_join_high_confidence():
    sessions = [_make_session("s1", "u1", end_offset_hours=0)]
    csat = [_make_csat("u1", session_id="s1", date_time_offset_hours=0.5)]
    result = join_csat(sessions, csat)
    assert result["summary"]["joined"] == 1
    assert result["joined"][0]["join_confidence"] == "high"
    assert result["joined"][0]["join_method"] == "direct_session_id"


# ---------------------------------------------------------------------------
# FUZZY JOIN (MEDIUM confidence)
# ---------------------------------------------------------------------------

def test_fuzzy_join_single_candidate():
    """One session ends within window — medium confidence."""
    sessions = [_make_session("s1", "u1", end_offset_hours=0)]  # ends at 10:00
    csat = [_make_csat("u1", session_id="", date_time_offset_hours=0.5)]  # CSAT at 10:30
    result = join_csat(sessions, csat, window_minutes=60)
    assert result["summary"]["joined"] == 1
    assert result["joined"][0]["join_confidence"] == "medium"
    assert result["joined"][0]["join_method"] == "fuzzy_temporal"


def test_fuzzy_join_multiple_candidates_picks_latest():
    """Multiple sessions in window — picks most recent, low confidence."""
    # Sessions ending at 09:00 and 09:30 — CSAT at 10:00
    s1 = _make_session("s1", "u1", end_offset_hours=-1)   # 09:00
    s2 = _make_session("s2", "u1", end_offset_hours=-0.5)  # 09:30
    sessions = [s1, s2]
    csat = [_make_csat("u1", session_id="", date_time_offset_hours=0)]  # 10:00
    result = join_csat(sessions, csat, window_minutes=120)
    assert result["summary"]["joined"] == 1
    assert result["joined"][0]["join_confidence"] == "low"
    assert result["joined"][0]["session_id"] == "s2"  # most recent


# ---------------------------------------------------------------------------
# UNJOINED CASES
# ---------------------------------------------------------------------------

def test_no_session_in_window():
    """CSAT submitted 2 hours after session end — outside 60-min window."""
    sessions = [_make_session("s1", "u1", end_offset_hours=0)]  # ends 10:00
    csat = [_make_csat("u1", session_id="", date_time_offset_hours=2.5)]  # 12:30 — outside window
    result = join_csat(sessions, csat, window_minutes=60)
    assert result["summary"]["joined"] == 0
    assert result["summary"]["unjoined"] == 1


def test_csat_before_session_end_not_joined():
    """CSAT submitted BEFORE session end — cannot join."""
    sessions = [_make_session("s1", "u1", end_offset_hours=1)]  # ends 11:00
    csat = [_make_csat("u1", session_id="", date_time_offset_hours=0.5)]  # 10:30 — before session end
    result = join_csat(sessions, csat, window_minutes=60)
    assert result["summary"]["joined"] == 0


def test_unknown_user_not_joined():
    sessions = [_make_session("s1", "u1", end_offset_hours=0)]
    csat = [_make_csat("u_other", session_id="", date_time_offset_hours=0.5)]
    result = join_csat(sessions, csat, window_minutes=60)
    assert result["summary"]["joined"] == 0
    assert result["summary"]["unjoined"] == 1


# ---------------------------------------------------------------------------
# MIXED JOIN
# ---------------------------------------------------------------------------

def test_mixed_direct_and_fuzzy():
    s1 = _make_session("s1", "u1", end_offset_hours=0)
    s2 = _make_session("s2", "u2", end_offset_hours=0)
    sessions = [s1, s2]

    csat = [
        _make_csat("u1", session_id="s1"),   # direct join
        _make_csat("u2", session_id=""),     # fuzzy join
    ]
    result = join_csat(sessions, csat, window_minutes=60)
    assert result["summary"]["joined"] == 2
    confidences = {j["join_confidence"] for j in result["joined"]}
    assert "high" in confidences
    assert "medium" in confidences


def test_summary_statistics():
    sessions = [_make_session(f"s{i}", f"u{i}", end_offset_hours=0) for i in range(5)]
    csat = [_make_csat(f"u{i}", session_id=f"s{i}") for i in range(5)]
    result = join_csat(sessions, csat)
    assert result["summary"]["total_csat_responses"] == 5
    assert result["summary"]["join_rate_pct"] == 100.0
    assert result["summary"]["confidence_breakdown"]["high"] == 5


def test_empty_inputs():
    result = join_csat([], [])
    assert result["summary"]["total_csat_responses"] == 0
    assert result["summary"]["joined"] == 0
