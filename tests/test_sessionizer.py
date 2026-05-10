"""
Unit tests for src/ingestion/sessionizer.py

Tests both Path A (native session_id) and Path B (time-gap derived).
Validates output shape consistency between both paths.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.sessionizer import sessionize, sessionize_native, sessionize_derived


def _make_row(user_id, session_id, timestamp, bot_msg="", user_msg="Hi"):
    return {
        "user_id": user_id,
        "session_id": session_id,
        "timestamp": timestamp,
        "bot_message": bot_msg,
        "user_message": user_msg,
        "channel": "Web/Mobile Client",
        "language": "en",
    }


# ---------------------------------------------------------------------------
# PATH A — NATIVE SESSION_ID
# ---------------------------------------------------------------------------

def test_path_a_basic_grouping():
    rows = [
        _make_row("u1", "s1", "2026-01-01 10:00:00"),
        _make_row("u1", "s1", "2026-01-01 10:01:00"),
        _make_row("u2", "s2", "2026-01-01 11:00:00"),
    ]
    sessions = sessionize_native(rows)
    assert len(sessions) == 2
    ids = {s.session_id for s in sessions}
    assert "s1" in ids and "s2" in ids


def test_path_a_message_count():
    rows = [_make_row("u1", "s1", f"2026-01-01 10:0{i}:00") for i in range(5)]
    sessions = sessionize_native(rows)
    assert len(sessions) == 1
    assert sessions[0].message_count == 5


def test_path_a_timestamp_bounds():
    rows = [
        _make_row("u1", "s1", "2026-01-01 10:00:00"),
        _make_row("u1", "s1", "2026-01-01 10:05:00"),
        _make_row("u1", "s1", "2026-01-01 10:10:00"),
    ]
    sessions = sessionize_native(rows)
    s = sessions[0]
    assert s.session_start.hour == 10 and s.session_start.minute == 0
    assert s.session_end.hour == 10 and s.session_end.minute == 10


def test_path_a_path_label():
    rows = [_make_row("u1", "s1", "2026-01-01 10:00:00")]
    sessions = sessionize_native(rows)
    assert sessions[0].path == "native"
    assert sessions[0].derived is False


def test_path_a_cross_user_warning():
    """Two different user_ids in one session_id → validation warning."""
    rows = [
        _make_row("u1", "s1", "2026-01-01 10:00:00"),
        _make_row("u2", "s1", "2026-01-01 10:01:00"),  # Different user, same session
    ]
    sessions = sessionize_native(rows)
    assert len(sessions) == 1
    assert len(sessions[0].validation_warnings) > 0


def test_path_a_missing_session_id_skipped():
    rows = [
        _make_row("u1", "", "2026-01-01 10:00:00"),   # Missing session_id
        _make_row("u2", "s2", "2026-01-01 11:00:00"),
    ]
    sessions = sessionize_native(rows)
    assert len(sessions) == 1
    assert sessions[0].session_id == "s2"


# ---------------------------------------------------------------------------
# PATH B — TIME-GAP DERIVED
# ---------------------------------------------------------------------------

def _make_row_no_sid(user_id, timestamp, bot_msg="", user_msg="Hi"):
    return {
        "user_id": user_id,
        "timestamp": timestamp,
        "bot_message": bot_msg,
        "user_message": user_msg,
        "channel": "Web/Mobile Client",
        "language": "en",
    }


def test_path_b_single_session_within_gap():
    rows = [
        _make_row_no_sid("u1", "2026-01-01 10:00:00"),
        _make_row_no_sid("u1", "2026-01-01 10:10:00"),
        _make_row_no_sid("u1", "2026-01-01 10:20:00"),
    ]
    sessions = sessionize_derived(rows, gap_minutes=30)
    assert len(sessions) == 1
    assert sessions[0].message_count == 3


def test_path_b_two_sessions_across_gap():
    rows = [
        _make_row_no_sid("u1", "2026-01-01 10:00:00"),
        _make_row_no_sid("u1", "2026-01-01 10:10:00"),
        _make_row_no_sid("u1", "2026-01-01 11:00:00"),  # 50 min gap → new session
        _make_row_no_sid("u1", "2026-01-01 11:10:00"),
    ]
    sessions = sessionize_derived(rows, gap_minutes=30)
    assert len(sessions) == 2
    assert sessions[0].message_count == 2
    assert sessions[1].message_count == 2


def test_path_b_different_users_separate_sessions():
    rows = [
        _make_row_no_sid("u1", "2026-01-01 10:00:00"),
        _make_row_no_sid("u2", "2026-01-01 10:05:00"),
        _make_row_no_sid("u1", "2026-01-01 10:10:00"),
    ]
    sessions = sessionize_derived(rows, gap_minutes=30)
    # u1 → 1 session, u2 → 1 session
    assert len(sessions) == 2


def test_path_b_derived_session_id_format():
    rows = [_make_row_no_sid("u1", "2026-01-01 10:00:00")]
    sessions = sessionize_derived(rows)
    assert sessions[0].session_id.startswith("derived-")
    assert sessions[0].derived is True
    assert sessions[0].path == "derived"


def test_path_b_deterministic_session_id():
    """Same user + same timestamp → same derived session_id (deterministic hash)."""
    rows = [_make_row_no_sid("u1", "2026-01-01 10:00:00")]
    s1 = sessionize_derived(rows)
    s2 = sessionize_derived(rows)
    assert s1[0].session_id == s2[0].session_id


def test_path_b_path_label():
    rows = [_make_row_no_sid("u1", "2026-01-01 10:00:00")]
    sessions = sessionize_derived(rows)
    assert sessions[0].path == "derived"


# ---------------------------------------------------------------------------
# UNIFIED ENTRY POINT — sessionize()
# ---------------------------------------------------------------------------

def test_unified_routes_to_path_a():
    rows = [_make_row("u1", "s1", "2026-01-01 10:00:00")]
    sessions = sessionize(rows, session_id_present=True)
    assert sessions[0].path == "native"


def test_unified_routes_to_path_b():
    rows = [_make_row_no_sid("u1", "2026-01-01 10:00:00")]
    sessions = sessionize(rows, session_id_present=False)
    assert sessions[0].path == "derived"


def test_output_shape_identical_between_paths():
    """Both paths must return ReconstructedSession with the same attribute set."""
    rows_a = [_make_row("u1", "s1", "2026-01-01 10:00:00")]
    rows_b = [_make_row_no_sid("u1", "2026-01-01 10:00:00")]

    sa = sessionize(rows_a, session_id_present=True)[0]
    sb = sessionize(rows_b, session_id_present=False)[0]

    for attr in ["session_id", "user_id", "channel", "language", "messages",
                 "session_start", "session_end", "message_count", "path", "derived"]:
        assert hasattr(sa, attr), f"Path A missing attribute: {attr}"
        assert hasattr(sb, attr), f"Path B missing attribute: {attr}"


def test_empty_input_returns_empty():
    assert sessionize([], session_id_present=True) == []
    assert sessionize([], session_id_present=False) == []
