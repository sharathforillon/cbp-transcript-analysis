"""
sessionizer.py — Dual-path session reconstruction for Kore.ai transcript data.

PATH A (preferred): session_id present in source → group directly.
PATH B (fallback):  session_id absent → derive from user_id + 30-min time gaps.

The active path is controlled by config/schema_mapping.yaml:
  transcripts.session_id_present: true | false

Both paths produce the same downstream Session object shape.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_GAP_MINUTES = 30


# ---------------------------------------------------------------------------
# SESSION DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class ReconstructedSession:
    """A fully reconstructed session, regardless of which path produced it."""
    session_id: str              # Native or derived session ID
    user_id: str
    channel: str
    language: str
    messages: list[dict]         # All message dicts, sorted by timestamp
    session_start: datetime
    session_end: datetime
    message_count: int
    path: str                    # "native" | "derived"
    derived: bool                # True if session_id was computed (not from source)
    validation_warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.session_end - self.session_start).total_seconds()

    @property
    def has_transfer(self) -> bool:
        for msg in self.messages:
            bm = msg.get("bot_message", "")
            if isinstance(bm, str) and ("live_agent" in bm or "SYSTEM" in bm):
                return True
        return False


# ---------------------------------------------------------------------------
# TIMESTAMP PARSING
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> datetime:
    """Parse a timestamp string into a UTC-aware datetime."""
    if not ts_str:
        raise ValueError("Empty timestamp string")
    try:
        dt = datetime.strptime(ts_str.strip(), TIMESTAMP_FORMAT)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        # Try ISO format fallback
        try:
            dt = datetime.fromisoformat(ts_str.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            raise ValueError(f"Cannot parse timestamp: {ts_str!r}")


def _derived_session_id(user_id: str, session_start: datetime) -> str:
    raw = f"{user_id}_{session_start.isoformat()}"
    return "derived-" + hashlib.md5(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# PATH A: NATIVE SESSION_ID
# ---------------------------------------------------------------------------

def sessionize_native(
    rows: list[dict],
    session_id_field: str = "session_id",
    user_id_field: str = "user_id",
    timestamp_field: str = "timestamp",
) -> list[ReconstructedSession]:
    """
    Path A: Group rows by their native session_id field.

    Validates that all messages in a session share the same user_id.
    Logs warnings for any cross-user contamination.
    """
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    skipped = 0

    for row in rows:
        sid = row.get(session_id_field, "").strip()
        if not sid:
            skipped += 1
            continue
        groups[sid].append(row)

    if skipped > 0:
        logger.warning("Path A: %d rows skipped — missing session_id", skipped)

    sessions: list[ReconstructedSession] = []

    for sid, msgs in groups.items():
        warnings = []

        # Sort by timestamp
        try:
            msgs.sort(key=lambda r: _parse_ts(r.get(timestamp_field, "")))
        except ValueError as e:
            logger.warning("Path A: Cannot sort session %s: %s", sid, e)
            warnings.append(f"Timestamp parse error: {e}")

        # Validate user_id consistency
        user_ids = {r.get(user_id_field, "") for r in msgs}
        if len(user_ids) > 1:
            warnings.append(f"Multiple user_ids in session: {user_ids}")
            logger.warning("Path A: Session %s has multiple user_ids: %s", sid, user_ids)

        user_id = msgs[0].get(user_id_field, "")
        channel = _most_common([r.get("channel", "") for r in msgs])
        language = _most_common([r.get("language", "") for r in msgs])

        try:
            start_ts = _parse_ts(msgs[0].get(timestamp_field, ""))
            end_ts = _parse_ts(msgs[-1].get(timestamp_field, ""))
        except ValueError:
            start_ts = end_ts = datetime.now(tz=timezone.utc)
            warnings.append("Could not compute session timestamps")

        sessions.append(ReconstructedSession(
            session_id=sid,
            user_id=user_id,
            channel=channel,
            language=language,
            messages=msgs,
            session_start=start_ts,
            session_end=end_ts,
            message_count=len(msgs),
            path="native",
            derived=False,
            validation_warnings=warnings,
        ))

    logger.info("Path A: reconstructed %d sessions from %d rows", len(sessions), len(rows))
    return sessions


# ---------------------------------------------------------------------------
# PATH B: TIME-GAP DERIVED SESSIONS
# ---------------------------------------------------------------------------

def sessionize_derived(
    rows: list[dict],
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    user_id_field: str = "user_id",
    timestamp_field: str = "timestamp",
) -> list[ReconstructedSession]:
    """
    Path B: Derive sessions from user_id + time gaps.

    For each user, sort messages by timestamp and split wherever the gap
    between consecutive messages exceeds `gap_minutes`.
    """
    from collections import defaultdict

    user_rows: dict[str, list[dict]] = defaultdict(list)
    skipped = 0

    for row in rows:
        uid = row.get(user_id_field, "").strip()
        if not uid:
            skipped += 1
            continue
        user_rows[uid].append(row)

    if skipped > 0:
        logger.warning("Path B: %d rows skipped — missing user_id", skipped)

    sessions: list[ReconstructedSession] = []
    gap_threshold = timedelta(minutes=gap_minutes)

    for user_id, msgs in user_rows.items():
        # Sort by timestamp
        parsed: list[tuple[datetime, dict]] = []
        for row in msgs:
            try:
                ts = _parse_ts(row.get(timestamp_field, ""))
                parsed.append((ts, row))
            except ValueError as e:
                logger.debug("Path B: Skipping row with bad timestamp for user %s: %s", user_id, e)

        if not parsed:
            continue

        parsed.sort(key=lambda x: x[0])

        # Split into sessions on gap
        current_msgs: list[dict] = [parsed[0][1]]
        current_start: datetime = parsed[0][0]
        current_end: datetime = parsed[0][0]

        for i in range(1, len(parsed)):
            ts, row = parsed[i]
            gap = ts - current_end

            if gap > gap_threshold:
                # Flush current session
                sessions.append(_build_derived_session(
                    user_id, current_msgs, current_start, current_end
                ))
                # Start new session
                current_msgs = [row]
                current_start = ts
                current_end = ts
            else:
                current_msgs.append(row)
                current_end = ts

        # Flush last session
        sessions.append(_build_derived_session(
            user_id, current_msgs, current_start, current_end
        ))

    logger.info(
        "Path B: derived %d sessions from %d users, gap=%dmin",
        len(sessions), len(user_rows), gap_minutes
    )
    return sessions


def _build_derived_session(
    user_id: str,
    msgs: list[dict],
    start: datetime,
    end: datetime,
) -> ReconstructedSession:
    sid = _derived_session_id(user_id, start)
    channel = _most_common([r.get("channel", "") for r in msgs])
    language = _most_common([r.get("language", "") for r in msgs])

    return ReconstructedSession(
        session_id=sid,
        user_id=user_id,
        channel=channel,
        language=language,
        messages=msgs,
        session_start=start,
        session_end=end,
        message_count=len(msgs),
        path="derived",
        derived=True,
    )


# ---------------------------------------------------------------------------
# UNIFIED ENTRY POINT
# ---------------------------------------------------------------------------

def sessionize(
    rows: list[dict],
    session_id_present: bool = True,
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    session_id_field: str = "session_id",
    user_id_field: str = "user_id",
    timestamp_field: str = "timestamp",
) -> list[ReconstructedSession]:
    """
    Route to Path A or Path B based on the session_id_present config flag.

    Args:
        rows: List of message row dicts from transcript CSV / Mongo.
        session_id_present: True → Path A (native). False → Path B (derived).
        gap_minutes: Session gap threshold for Path B.

    Returns:
        List of ReconstructedSession objects (same shape from both paths).
    """
    if session_id_present:
        logger.info("Sessionizer: using Path A (native session_id)")
        return sessionize_native(rows, session_id_field, user_id_field, timestamp_field)
    else:
        logger.info("Sessionizer: using Path B (derived from time gaps, gap=%dmin)", gap_minutes)
        return sessionize_derived(rows, gap_minutes, user_id_field, timestamp_field)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _most_common(values: list[str]) -> str:
    """Return the most common non-empty value in a list."""
    from collections import Counter
    counts = Counter(v for v in values if v)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]
