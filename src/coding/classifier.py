"""
classifier.py — LLM-based session classification pipeline.

Orchestrates four classification stages:
  1. Journey classification (all sessions)
  2. Transfer reason classification (transferred sessions only)
  3. Failure mode deep coding (stratified sample per journey)
  4. Remediation type assignment (per journey)

All prompts loaded from prompts/ directory.
Results cached in data/outputs/classifications.json.
"""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Optional

from src.coding.llm_client import LLMClient, get_llm_client
from src.ingestion.sessionizer import ReconstructedSession
from src.ingestion.template_parser import parse_bot_message, is_transfer_signal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
OUTPUT_DIR = REPO_ROOT / "data" / "outputs"


# ---------------------------------------------------------------------------
# PROMPT LOADING
# ---------------------------------------------------------------------------

def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _split_system_user(prompt_text: str) -> tuple[str, str]:
    """
    Split a prompt file into SYSTEM and USER sections.
    Prompt files use SYSTEM: / USER: headers.
    """
    lines = prompt_text.split("\n")
    system_lines, user_lines = [], []
    current = None

    for line in lines:
        if line.strip() == "SYSTEM:":
            current = "system"
        elif line.strip() == "USER:":
            current = "user"
        elif current == "system":
            system_lines.append(line)
        elif current == "user":
            user_lines.append(line)

    return "\n".join(system_lines).strip(), "\n".join(user_lines).strip()


# ---------------------------------------------------------------------------
# SESSION TEXT EXTRACTION
# ---------------------------------------------------------------------------

def extract_user_messages(session: ReconstructedSession, n: int = 5) -> str:
    """Extract the first N user messages from a session as a formatted string."""
    user_msgs = []
    for msg in session.messages:
        text = msg.get("user_message", "").strip()
        if text:
            user_msgs.append(text)
        if len(user_msgs) >= n:
            break
    return "\n".join(f"[Turn {i+1}] {m}" for i, m in enumerate(user_msgs))


def extract_full_transcript(session: ReconstructedSession) -> str:
    """Format full session transcript as a readable string."""
    lines = []
    for msg in session.messages:
        user_msg = msg.get("user_message", "").strip()
        bot_msg_raw = msg.get("bot_message", "").strip()
        ts = msg.get("timestamp", "")

        if bot_msg_raw:
            parsed = parse_bot_message(bot_msg_raw)
            bot_text = parsed.text
            template_info = f" [{parsed.template_type}]" if parsed.template_type else ""
            lines.append(f"[{ts}] BOT{template_info}: {bot_text}")

        if user_msg:
            lines.append(f"[{ts}] USER: {user_msg}")

    return "\n".join(lines)


def detect_transfer(session: ReconstructedSession) -> tuple[bool, str]:
    """
    Detect if a session was transferred to a human agent.
    Returns (is_transferred, transfer_signal_description).
    """
    for msg in session.messages:
        bot_raw = msg.get("bot_message", "")
        parsed = parse_bot_message(bot_raw)
        if is_transfer_signal(parsed):
            return True, f"template_type={parsed.template_type or 'keyword_match'} at {msg.get('timestamp', '')}"
    return False, ""


# ---------------------------------------------------------------------------
# STAGE 1: JOURNEY CLASSIFICATION
# ---------------------------------------------------------------------------

def classify_journey(
    session: ReconstructedSession,
    client: LLMClient,
    prompt_template: str,
    system_prompt: str,
) -> dict:
    """Classify a single session's primary journey."""
    excerpt = extract_user_messages(session, n=5)
    user_prompt = prompt_template.replace("{transcript_excerpt}", excerpt)
    user_prompt = user_prompt.replace("{n_messages}", str(min(5, len(session.messages))))

    result = client.classify(prompt=user_prompt, system=system_prompt)
    result["session_id"] = session.session_id
    result["user_id"] = session.user_id
    return result


def run_journey_classification(
    sessions: list[ReconstructedSession],
    client: Optional[LLMClient] = None,
    max_sessions: Optional[int] = None,
) -> dict[str, dict]:
    """
    Stage 1: Classify sessions.

    Args:
        sessions: All reconstructed sessions.
        client: LLM client to use.
        max_sessions: If set, randomly sample this many sessions before
                      classifying (POC mode — reduces API cost). Downstream
                      stages (transfer reason, failure mode) will only run
                      on sessions that appear in the returned dict.

    Returns:
        dict of session_id → classification result.
    """
    client = client or get_llm_client()
    prompt_text = _load_prompt("journey_classifier_v1.txt")
    system_prompt, user_template = _split_system_user(prompt_text)

    if max_sessions and len(sessions) > max_sessions:
        sessions_to_classify = random.sample(sessions, max_sessions)
        logger.info(
            "Stage 1 (POC mode): sampling %d of %d sessions for journey classification",
            max_sessions, len(sessions),
        )
    else:
        sessions_to_classify = sessions

    results = {}
    logger.info("Stage 1: Classifying %d sessions...", len(sessions_to_classify))

    for i, session in enumerate(sessions_to_classify):
        if i % 100 == 0:
            logger.info("  Journey classification: %d/%d", i, len(sessions_to_classify))
        result = classify_journey(session, client, user_template, system_prompt)
        results[session.session_id] = result

    logger.info("Stage 1 complete: %d sessions classified", len(results))
    return results


# ---------------------------------------------------------------------------
# STAGE 2: TRANSFER REASON CLASSIFICATION
# ---------------------------------------------------------------------------

def classify_transfer_reason(
    session: ReconstructedSession,
    transfer_signal: str,
    journey_name: str,
    client: LLMClient,
    prompt_template: str,
    system_prompt: str,
) -> dict:
    """Classify the transfer reason for a single transferred session."""
    transcript = extract_full_transcript(session)
    user_prompt = (
        prompt_template
        .replace("{full_transcript}", transcript)
        .replace("{transfer_signal}", transfer_signal)
        .replace("{journey_name}", journey_name)
    )
    result = client.classify(prompt=user_prompt, system=system_prompt)
    result["session_id"] = session.session_id
    return result


def run_transfer_reason_classification(
    sessions: list[ReconstructedSession],
    journey_results: dict[str, dict],
    client: Optional[LLMClient] = None,
) -> dict[str, dict]:
    """
    Stage 2: Classify transfer reasons for all transferred sessions.
    Returns dict of session_id → classification result.
    """
    client = client or get_llm_client()
    prompt_text = _load_prompt("transfer_reason_classifier_v1.txt")
    system_prompt, user_template = _split_system_user(prompt_text)

    transferred = []
    for session in sessions:
        # Only process sessions that were classified in Stage 1
        if session.session_id not in journey_results:
            continue
        is_transferred, signal = detect_transfer(session)
        if is_transferred:
            transferred.append((session, signal))

    logger.info("Stage 2: Classifying transfer reasons for %d transferred sessions...", len(transferred))
    results = {}

    for i, (session, signal) in enumerate(transferred):
        if i % 50 == 0:
            logger.info("  Transfer classification: %d/%d", i, len(transferred))
        journey_name = journey_results.get(session.session_id, {}).get("journey_name", "Unknown")
        result = classify_transfer_reason(session, signal, journey_name, client, user_template, system_prompt)
        results[session.session_id] = result

    logger.info("Stage 2 complete: %d transfer reasons classified", len(results))
    return results


# ---------------------------------------------------------------------------
# STAGE 3: FAILURE MODE DEEP CODING
# ---------------------------------------------------------------------------

def run_failure_mode_coding(
    sessions: list[ReconstructedSession],
    journey_results: dict[str, dict],
    transfer_results: dict[str, dict],
    client: Optional[LLMClient] = None,
    sample_per_journey_min: int = 30,
    sample_per_journey_pct: float = 0.10,
) -> dict[str, dict]:
    """
    Stage 3: Deep-code a stratified sample of transferred sessions.
    Returns dict of session_id → deep-code result.
    """
    client = client or get_llm_client()
    prompt_text = _load_prompt("failure_mode_classifier_v1.txt")
    system_prompt, user_template = _split_system_user(prompt_text)

    # Group transferred sessions by journey
    by_journey: dict[str, list] = defaultdict(list)
    for session in sessions:
        if session.session_id in transfer_results:
            jid = journey_results.get(session.session_id, {}).get("journey_id", "INTL_005")
            by_journey[jid].append(session)

    # Sample per journey
    to_code: list[ReconstructedSession] = []
    for jid, j_sessions in by_journey.items():
        n_sample = max(sample_per_journey_min, int(len(j_sessions) * sample_per_journey_pct))
        n_sample = min(n_sample, len(j_sessions))
        sampled = random.sample(j_sessions, n_sample)
        to_code.extend(sampled)

    logger.info("Stage 3: Deep-coding %d sessions (stratified sample)...", len(to_code))
    results = {}

    for i, session in enumerate(to_code):
        if i % 20 == 0:
            logger.info("  Failure mode coding: %d/%d", i, len(to_code))
        transcript = extract_full_transcript(session)
        jid = journey_results.get(session.session_id, {}).get("journey_id", "")
        journey_name = journey_results.get(session.session_id, {}).get("journey_name", "")
        transfer_code = transfer_results.get(session.session_id, {}).get("transfer_reason_code", "")

        user_prompt = (
            user_template
            .replace("{full_transcript}", transcript)
            .replace("{journey_id}", jid)
            .replace("{journey_name}", journey_name)
            .replace("{transfer_reason_code}", transfer_code)
        )
        result = client.classify(prompt=user_prompt, system=system_prompt)
        result["session_id"] = session.session_id
        results[session.session_id] = result

    logger.info("Stage 3 complete: %d sessions deep-coded", len(results))
    return results


# ---------------------------------------------------------------------------
# STAGE 4: REMEDIATION TYPE ASSIGNMENT
# ---------------------------------------------------------------------------

def run_remediation_assignment(
    journey_results: dict[str, dict],
    transfer_results: dict[str, dict],
    failure_mode_results: dict[str, dict],
    client: Optional[LLMClient] = None,
) -> dict[str, dict]:
    """
    Stage 4: Assign remediation types per journey by aggregating failure mode data.
    Returns dict of journey_id → remediation recommendation.
    """
    client = client or get_llm_client()
    prompt_text = _load_prompt("remediation_type_classifier_v1.txt")
    system_prompt, user_template = _split_system_user(prompt_text)

    # Aggregate failure mode distribution per journey
    journey_fm: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    journey_names: dict[str, str] = {}
    journey_fm_details: dict[str, list[dict]] = defaultdict(list)

    for session_id, fm_result in failure_mode_results.items():
        jid = journey_results.get(session_id, {}).get("journey_id", "INTL_005")
        journey_name = journey_results.get(session_id, {}).get("journey_name", "Unknown")
        journey_names[jid] = journey_name

        transfer_code = transfer_results.get(session_id, {}).get("transfer_reason_code", "unknown")
        journey_fm[jid][transfer_code] += 1
        journey_fm_details[jid].append({
            "session_id": session_id,
            "transfer_reason": transfer_code,
            "agent_resolution": fm_result.get("agent_resolution", ""),
            "what_would_have_prevented": fm_result.get("what_would_have_prevented_escalation", ""),
        })

    logger.info("Stage 4: Assigning remediation types for %d journeys...", len(journey_fm))
    results = {}

    for jid, fm_dist in journey_fm.items():
        journey_name = journey_names.get(jid, "Unknown")
        n_sessions = sum(fm_dist.values())

        # Summarize agent explanations for knowledge transfer
        agent_explanations = [
            d["agent_resolution"] for d in journey_fm_details[jid]
            if d.get("agent_resolution")
        ]
        agent_cluster_text = "\n".join(f"- {e}" for e in agent_explanations[:5]) or "None available."

        user_prompt = (
            user_template
            .replace("{journey_id}", jid)
            .replace("{journey_name}", journey_name)
            .replace("{n_sessions}", str(n_sessions))
            .replace("{failure_mode_summary}", _format_fm_summary(journey_fm_details[jid]))
            .replace("{failure_mode_distribution}", json.dumps(dict(fm_dist), indent=2))
            .replace("{agent_explanation_clusters}", agent_cluster_text)
        )
        result = client.classify(prompt=user_prompt, system=system_prompt)
        result["journey_id"] = jid
        result["journey_name"] = journey_name
        result["sessions_in_sample"] = n_sessions
        results[jid] = result

    logger.info("Stage 4 complete: %d journey remediations assigned", len(results))
    return results


def _format_fm_summary(details: list[dict]) -> str:
    lines = []
    for d in details[:10]:  # Cap at 10 for prompt length
        lines.append(
            f"- Session {d['session_id'][:12]}... | Transfer: {d['transfer_reason']} "
            f"| Prevention: {d['what_would_have_prevented']}"
        )
    return "\n".join(lines) or "No details available."


# ---------------------------------------------------------------------------
# OUTPUT PERSISTENCE
# ---------------------------------------------------------------------------

def save_classifications(
    journey_results: dict,
    transfer_results: dict,
    failure_mode_results: dict,
    remediation_results: dict,
    output_dir: Optional[Path] = None,
) -> None:
    """Save all classification results to data/outputs/."""
    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    def _save(data: dict, filename: str):
        path = out_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Saved %d records to %s", len(data), path)

    _save(journey_results, "journey_classifications.json")
    _save(transfer_results, "transfer_classifications.json")
    _save(failure_mode_results, "failure_mode_classifications.json")
    _save(remediation_results, "remediation_assignments.json")


def load_classifications(output_dir: Optional[Path] = None) -> dict:
    """Load previously saved classification results."""
    out_dir = output_dir or OUTPUT_DIR
    results = {}
    for key, filename in [
        ("journey", "journey_classifications.json"),
        ("transfer", "transfer_classifications.json"),
        ("failure_mode", "failure_mode_classifications.json"),
        ("remediation", "remediation_assignments.json"),
    ]:
        path = out_dir / filename
        if path.exists():
            with open(path) as f:
                results[key] = json.load(f)
        else:
            results[key] = {}
    return results
