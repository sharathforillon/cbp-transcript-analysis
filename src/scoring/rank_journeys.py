"""
rank_journeys.py — Five-dimension composite journey scoring for CBP MVP prioritization.

Score = weighted sum of normalized dimensions:
  volume_share (0.25) + leakage_rate (0.25) + remediability (0.20)
  + cbp_capability_fit (0.15) + csat_signal (0.10)
  - regulatory_complexity_penalty (0.05)

Weights tunable in config/taxonomies.yaml.
Outputs scored_journeys.json and mvp_shortlist.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
TAXONOMY_PATH = REPO_ROOT / "config" / "taxonomies.yaml"
OUTPUT_DIR = REPO_ROOT / "data" / "outputs"


# ---------------------------------------------------------------------------
# CONFIG LOADING
# ---------------------------------------------------------------------------

def _load_taxonomy() -> dict:
    with open(TAXONOMY_PATH) as f:
        return yaml.safe_load(f)


def _journey_config_map(taxonomy: dict) -> dict[str, dict]:
    """Index taxonomy journey list by journey_id."""
    return {j["id"]: j for j in taxonomy.get("journeys", [])}


def _exclusion_set(taxonomy: dict) -> set[str]:
    return {e["journey_id"] for e in taxonomy.get("exclusions", [])}


def _remediation_difficulty(code: str, taxonomy: dict) -> int:
    """Return difficulty score (1-5) for a remediation type code."""
    for r in taxonomy.get("remediation_types", []):
        if r["code"] == code:
            return r.get("difficulty", 3)
    return 3


# ---------------------------------------------------------------------------
# SCORE COMPUTATION
# ---------------------------------------------------------------------------

def score_journeys(
    leakage_map: dict,
    csat_joined: dict,
    remediation_assignments: dict,
    weights: Optional[dict] = None,
    output_dir: Optional[Path] = None,
    min_volume_override: Optional[int] = None,
) -> dict:
    """
    Compute composite scores for all journeys.

    Args:
        leakage_map: Output of leakage_map.build_leakage_map()
        csat_joined: Output of csat_joiner.join_csat()
        remediation_assignments: Output of classifier.run_remediation_assignment()
        weights: Override scoring weights (default from taxonomies.yaml)

    Returns:
        Dict with scored_journeys and mvp_shortlist.
    """
    taxonomy = _load_taxonomy()
    journey_config = _journey_config_map(taxonomy)
    exclusions = _exclusion_set(taxonomy)
    default_weights = taxonomy.get("scoring_weights", {})
    thresholds = taxonomy.get("scoring_thresholds", {})
    shortlist_n = taxonomy.get("mvp_shortlist_size", 10)
    runners_up_n = taxonomy.get("mvp_runners_up", 5)

    w = weights or default_weights

    journeys = leakage_map.get("journeys", [])
    total_escalated = leakage_map["summary"]["total_escalated"]
    total_sessions = leakage_map["summary"]["total_sessions"]

    # Build CSAT score per journey (session_id → journey_id → average score)
    journey_csat_scores: dict[str, list[float]] = {}
    for record in csat_joined.get("joined", []):
        jid = record.get("journey_id")
        score = record.get("score")
        if jid and score:
            try:
                journey_csat_scores.setdefault(jid, []).append(float(score))
            except (ValueError, TypeError):
                pass

    # Scoring pass
    min_volume = min_volume_override if min_volume_override is not None else thresholds.get("minimum_volume_sessions", 500)
    min_leakage = thresholds.get("minimum_leakage_rate", 0.05)

    raw_scores = []

    for j in journeys:
        journey_id = j["journey_id"]
        journey_name = j["journey_name"]
        n_total = j["total_sessions"]
        n_escalated = j["escalated_sessions"]
        leakage_rate = j["escalation_rate"]

        # Apply exclusion list
        excluded = journey_id in exclusions
        exclusion_reason = ""
        if excluded:
            matching = [e for e in taxonomy.get("exclusions", []) if e["journey_id"] == journey_id]
            exclusion_reason = matching[0].get("reason", "excluded") if matching else "excluded"

        # Apply minimum thresholds
        below_threshold = n_total < min_volume or leakage_rate < min_leakage

        # Get config-level attributes
        jcfg = journey_config.get(journey_id, {})
        cbp_fit = jcfg.get("cbp_capability_fit", 0.5)
        reg_complexity = jcfg.get("regulatory_complexity", 0.3)

        # Get remediation difficulty
        remediation = remediation_assignments.get(journey_id, {})
        rems = remediation.get("recommended_remediations", [])
        if rems:
            top_rem = rems[0].get("remediation_type", "")
            difficulty = _remediation_difficulty(top_rem, taxonomy)
        else:
            difficulty = 3  # Default medium
        remediability = 1 - (difficulty - 1) / 4  # Invert: 1=easy→1.0, 5=hard→0.0

        # CSAT signal (1 - normalized avg score → low CSAT = higher priority)
        csat_scores = journey_csat_scores.get(journey_id, [])
        avg_csat = sum(csat_scores) / len(csat_scores) if csat_scores else 3.0  # Neutral default
        csat_signal = 1 - (avg_csat - 1) / 4  # Normalize 1-5 → 1.0-0.0

        # Volume share (% of total escalated sessions)
        volume_share = n_escalated / total_escalated if total_escalated > 0 else 0

        # Composite score (only compute if not excluded and above threshold)
        if not excluded and not below_threshold:
            score = (
                w.get("volume_share", 0.25) * volume_share * 10  # Scale up (max ~0.1 → multiply by 10)
                + w.get("leakage_rate", 0.25) * leakage_rate
                + w.get("remediability", 0.20) * remediability
                + w.get("cbp_capability_fit", 0.15) * cbp_fit
                + w.get("csat_signal", 0.10) * csat_signal
                + w.get("regulatory_complexity_penalty", -0.05) * reg_complexity
            )
        else:
            score = -1.0  # Excluded / below threshold

        # Confidence tier based on sample size
        if n_total >= 1000:
            confidence = "HIGH"
        elif n_total >= 200:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Primary remediation type
        primary_remediation = rems[0].get("remediation_type", "") if rems else "unknown"

        raw_scores.append({
            "journey_id": journey_id,
            "journey_name": journey_name,
            "domain": journey_id.split("_")[0].lower(),
            "total_sessions": n_total,
            "escalated_sessions": n_escalated,
            "escalation_rate": leakage_rate,
            "volume_share": round(volume_share, 4),
            "remediability": round(remediability, 4),
            "cbp_capability_fit": cbp_fit,
            "regulatory_complexity": reg_complexity,
            "avg_csat": round(avg_csat, 2),
            "csat_sample_size": len(csat_scores),
            "csat_signal": round(csat_signal, 4),
            "composite_score": round(score, 4),
            "confidence": confidence,
            "primary_remediation": primary_remediation,
            "excluded": excluded,
            "exclusion_reason": exclusion_reason,
            "below_threshold": below_threshold,
            "why_this_matters": _generate_why_this_matters(
                journey_name, leakage_rate, n_escalated, primary_remediation, cbp_fit
            ),
        })

    # Sort by composite score (descending)
    raw_scores.sort(key=lambda x: -x["composite_score"])

    # Assign MVP ranks (only to non-excluded, above-threshold journeys)
    eligible = [s for s in raw_scores if not s["excluded"] and not s["below_threshold"]]
    for rank, s in enumerate(eligible, start=1):
        s["mvp_rank"] = rank

    for s in raw_scores:
        if "mvp_rank" not in s:
            s["mvp_rank"] = None

    # Build shortlist
    mvp_top = [s for s in eligible[:shortlist_n]]
    mvp_runners = [s for s in eligible[shortlist_n:shortlist_n + runners_up_n]]

    result = {
        "scoring_weights_used": w,
        "thresholds_used": thresholds,
        "total_journeys_scored": len(raw_scores),
        "eligible_for_ranking": len(eligible),
        "scored_journeys": raw_scores,
    }

    mvp_result = {
        "mvp_shortlist": mvp_top,
        "runners_up": mvp_runners,
        "excluded_journeys": [s for s in raw_scores if s["excluded"]],
        "below_threshold_journeys": [s for s in raw_scores if s["below_threshold"] and not s["excluded"]],
    }

    out_dir = output_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "scored_journeys.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    with open(out_dir / "mvp_shortlist.json", "w") as f:
        json.dump(mvp_result, f, indent=2, ensure_ascii=False)

    logger.info(
        "Scoring complete: %d journeys ranked, top %d in MVP shortlist",
        len(eligible), min(shortlist_n, len(eligible))
    )
    return {**result, **mvp_result}


def _generate_why_this_matters(
    journey_name: str,
    leakage_rate: float,
    n_escalated: int,
    primary_remediation: str,
    cbp_fit: float,
) -> str:
    """Generate a one-line 'why this matters' summary for the dashboard."""
    remediation_labels = {
        "content_quality_improvement": "quick content fix",
        "faq_addition": "simple FAQ addition",
        "intent_model_improvement": "NLU retraining needed",
        "flow_redesign": "flow redesign",
        "backend_integration": "backend integration required",
        "authentication_friction_reduction": "auth UX improvement",
        "language_dialect_coverage": "dialect coverage gap",
        "product_or_policy_change": "policy change needed",
        "no_action": "no action needed",
    }
    rem_label = remediation_labels.get(primary_remediation, primary_remediation)
    cbp_label = "bot-ready" if cbp_fit > 0.7 else "partial capability" if cbp_fit > 0.4 else "needs backend work"

    return (
        f"{n_escalated:,} escalations ({leakage_rate:.0%} rate); "
        f"fix via {rem_label}; CBP is {cbp_label}."
    )
