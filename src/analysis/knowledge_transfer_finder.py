"""
knowledge_transfer_finder.py — Surface recurring agent explanations for bot enhancement.

Clusters post-handoff agent messages by semantic similarity within each journey.
The highest-frequency clusters are immediate bot enhancement candidates.

Outputs data/outputs/knowledge_transfer.json.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from src.ingestion.sessionizer import ReconstructedSession
from src.ingestion.template_parser import parse_bot_message, is_transfer_signal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "outputs" / "knowledge_transfer.json"

MIN_CLUSTER_FREQUENCY = 3  # Must appear 3+ times to be surfaced


def extract_post_handoff_messages(session: ReconstructedSession) -> list[str]:
    """
    Extract bot messages that appear AFTER the transfer signal in a session.
    These are the agent's actual responses (relayed through the bot channel).
    """
    found_transfer = False
    post_handoff = []

    for msg in session.messages:
        parsed = parse_bot_message(msg.get("bot_message", ""))
        if not found_transfer:
            if is_transfer_signal(parsed):
                found_transfer = True
        else:
            # After transfer: bot messages are agent messages
            if parsed.text and not is_transfer_signal(parsed):
                post_handoff.append(parsed.text)

    return post_handoff


def _normalize_text(text: str) -> str:
    """Normalize text for clustering: lowercase, remove punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _simple_cluster(
    messages: list[str],
    min_overlap_words: int = 4,
) -> list[dict]:
    """
    Simple word-overlap clustering (no ML dependencies required).

    Groups messages that share at least min_overlap_words common words.
    In production, replace with sentence embeddings (sentence-transformers).
    """
    if not messages:
        return []

    clusters: list[dict] = []

    def _words(text: str) -> set[str]:
        stopwords = {"the", "a", "an", "is", "it", "in", "of", "to", "and", "your", "my", "for", "on", "at", "by", "i", "you", "we"}
        return {w for w in _normalize_text(text).split() if w not in stopwords and len(w) > 2}

    used = [False] * len(messages)

    for i, msg_i in enumerate(messages):
        if used[i]:
            continue
        words_i = _words(msg_i)
        cluster_msgs = [msg_i]
        used[i] = True

        for j, msg_j in enumerate(messages[i+1:], start=i+1):
            if used[j]:
                continue
            words_j = _words(msg_j)
            overlap = len(words_i & words_j)
            if overlap >= min_overlap_words:
                cluster_msgs.append(msg_j)
                used[j] = True

        clusters.append({
            "representative": msg_i,
            "members": cluster_msgs,
            "frequency": len(cluster_msgs),
        })

    clusters.sort(key=lambda c: -c["frequency"])
    return clusters


def _estimate_bot_feasibility(cluster: dict, journey_id: str) -> str:
    """
    Heuristic estimate of whether a bot could give the same explanation.
    Returns: 'yes' | 'no' | 'with_data_access'
    """
    text = cluster["representative"].lower()

    # Pure data retrieval — easy for bot
    if any(kw in text for kw in ["your balance is", "your iban is", "due date", "maturity date", "rate is"]):
        return "yes"

    # Requires backend action
    if any(kw in text for kw in ["i have raised", "i have submitted", "i have escalated", "processed your"]):
        return "with_data_access"

    # Regulatory / complex judgment
    if any(kw in text for kw in ["compliance", "regulatory", "investigation", "legal", "court"]):
        return "no"

    # Explanation that requires data
    if any(kw in text for kw in ["pending", "hold", "merchant", "authorization", "clear"]):
        return "with_data_access"

    return "with_data_access"


def build_knowledge_transfer_map(
    sessions: list[ReconstructedSession],
    journey_classifications: dict[str, dict],
    output_path: Optional[Path] = None,
) -> dict:
    """
    Find recurring agent explanations across the corpus, grouped by journey.

    Returns dict with top knowledge transfer opportunities sorted by frequency × feasibility.
    """
    # Collect post-handoff agent messages per journey
    by_journey: dict[str, list[str]] = defaultdict(list)
    by_journey_sessions: dict[str, list[str]] = defaultdict(list)

    for session in sessions:
        sid = session.session_id
        jid = journey_classifications.get(sid, {}).get("journey_id", "INTL_005")
        post_handoff = extract_post_handoff_messages(session)
        if post_handoff:
            by_journey[jid].extend(post_handoff)
            by_journey_sessions[jid].append(sid)

    # Cluster per journey
    all_opportunities = []

    for jid, messages in by_journey.items():
        if len(messages) < MIN_CLUSTER_FREQUENCY:
            continue

        journey_name = ""
        for sid, clf in journey_classifications.items():
            if clf.get("journey_id") == jid:
                journey_name = clf.get("journey_name", "")
                break

        clusters = _simple_cluster(messages)

        for cluster in clusters:
            if cluster["frequency"] < MIN_CLUSTER_FREQUENCY:
                continue

            feasibility = _estimate_bot_feasibility(cluster, jid)
            feasibility_score = {"yes": 1.0, "with_data_access": 0.7, "no": 0.1}[feasibility]
            automation_impact = round(cluster["frequency"] * feasibility_score, 1)

            all_opportunities.append({
                "journey_id": jid,
                "journey_name": journey_name,
                "representative_message": cluster["representative"][:300],
                "frequency": cluster["frequency"],
                "example_messages": cluster["members"][:3],
                "bot_feasibility": feasibility,
                "automation_impact_score": automation_impact,
            })

    # Sort by automation impact
    all_opportunities.sort(key=lambda x: -x["automation_impact_score"])

    result = {
        "summary": {
            "journeys_with_agent_messages": len(by_journey),
            "total_opportunities": len(all_opportunities),
            "min_frequency_threshold": MIN_CLUSTER_FREQUENCY,
            "note": (
                "Clustering uses word-overlap heuristic. "
                "For production, replace _simple_cluster() with sentence embeddings "
                "(e.g. sentence-transformers/all-MiniLM-L6-v2) for better grouping."
            ),
        },
        "opportunities": all_opportunities,
    }

    out_path = output_path or OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info("Knowledge transfer map written to %s (%d opportunities)", out_path, len(all_opportunities))
    return result
