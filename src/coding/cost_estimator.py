"""
cost_estimator.py — Pre-run cost estimation and YES gate for LLM pipeline runs.

Before any LLM API calls, this module:
1. Counts sessions to be processed per stage
2. Estimates token volume and cost
3. Prints a clear summary
4. Requires user to type YES to continue

Usage:
  from src.coding.cost_estimator import estimate_and_confirm
  if not estimate_and_confirm(n_sessions=10000, stages=["journey", "transfer_reason"]):
      sys.exit(0)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
LLM_CONFIG_PATH = REPO_ROOT / "config" / "llm_config.yaml"


# ---------------------------------------------------------------------------
# STAGE DEFINITIONS
# ---------------------------------------------------------------------------

STAGE_DEFAULTS = {
    "journey": {
        "label": "Journey Classification",
        "avg_input_tokens": 400,
        "avg_output_tokens": 100,
        "fraction_of_sessions": 1.0,
        "description": "All sessions (first 5 user messages)",
    },
    "transfer_reason": {
        "label": "Transfer Reason Classification",
        "avg_input_tokens": 1200,
        "avg_output_tokens": 150,
        "fraction_of_sessions": None,  # Set dynamically (transferred sessions only)
        "description": "Transferred sessions only (estimated ~30%)",
    },
    "failure_mode": {
        "label": "Failure Mode Deep Coding",
        "avg_input_tokens": 2500,
        "avg_output_tokens": 300,
        "fraction_of_sessions": None,  # min(30, 10% per journey) — set dynamically
        "description": "Stratified sample (min 30, up to 10% per journey)",
    },
    "remediation": {
        "label": "Remediation Type Assignment",
        "avg_input_tokens": 1500,
        "avg_output_tokens": 250,
        "fraction_of_sessions": None,  # 1 per journey = ~90 calls
        "description": "One call per journey (~90 total)",
    },
    "doc_generation": {
        "label": "Per-Journey Doc Generation",
        "avg_input_tokens": 6000,
        "avg_output_tokens": 2000,
        "fraction_of_sessions": None,  # top-N journeys
        "description": "One doc per top-N journeys",
    },
}


# ---------------------------------------------------------------------------
# COST ESTIMATION
# ---------------------------------------------------------------------------

def estimate_cost(
    n_calls: int,
    avg_input_tokens: int,
    avg_output_tokens: int,
    input_rate_per_million: float,
    output_rate_per_million: float,
) -> float:
    """Return estimated USD cost for n_calls."""
    input_cost = (n_calls * avg_input_tokens / 1_000_000) * input_rate_per_million
    output_cost = (n_calls * avg_output_tokens / 1_000_000) * output_rate_per_million
    return input_cost + output_cost


def compute_stage_estimates(
    n_total_sessions: int,
    n_transferred_sessions: int,
    n_journeys: int,
    n_top_journeys: int,
    input_rate: float,
    output_rate: float,
    n_classify_sessions: Optional[int] = None,
) -> list[dict]:
    """
    Compute call counts and costs for each pipeline stage.

    Args:
        n_classify_sessions: If set (POC mode), Stage 1 uses this count instead
                             of n_total_sessions. Downstream stages scale accordingly.
    """
    stages = []

    # Stage 1: Journey classification
    s1_calls = n_classify_sessions if n_classify_sessions else n_total_sessions
    s1 = STAGE_DEFAULTS["journey"]
    poc_note = f" [POC sample: {s1_calls:,} of {n_total_sessions:,}]" if n_classify_sessions else ""
    stages.append({
        "stage": "journey",
        "label": s1["label"],
        "n_calls": s1_calls,
        "avg_input": s1["avg_input_tokens"],
        "avg_output": s1["avg_output_tokens"],
        "total_input_tokens": s1_calls * s1["avg_input_tokens"],
        "total_output_tokens": s1_calls * s1["avg_output_tokens"],
        "estimated_cost": estimate_cost(s1_calls, s1["avg_input_tokens"], s1["avg_output_tokens"], input_rate, output_rate),
        "description": s1["description"] + poc_note,
    })

    # Stage 2: Transfer reason — transferred sessions only (scaled to classified set)
    classify_fraction = s1_calls / n_total_sessions if n_total_sessions else 1.0
    s2_calls = int(n_transferred_sessions * classify_fraction)
    s2 = STAGE_DEFAULTS["transfer_reason"]
    stages.append({
        "stage": "transfer_reason",
        "label": s2["label"],
        "n_calls": s2_calls,
        "avg_input": s2["avg_input_tokens"],
        "avg_output": s2["avg_output_tokens"],
        "total_input_tokens": s2_calls * s2["avg_input_tokens"],
        "total_output_tokens": s2_calls * s2["avg_output_tokens"],
        "estimated_cost": estimate_cost(s2_calls, s2["avg_input_tokens"], s2["avg_output_tokens"], input_rate, output_rate),
        "description": s2["description"],
    })

    # Stage 3: Failure mode — sample (scaled to classified transferred set)
    s3_calls = min(s2_calls, n_journeys * 30)
    s3 = STAGE_DEFAULTS["failure_mode"]
    stages.append({
        "stage": "failure_mode",
        "label": s3["label"],
        "n_calls": s3_calls,
        "avg_input": s3["avg_input_tokens"],
        "avg_output": s3["avg_output_tokens"],
        "total_input_tokens": s3_calls * s3["avg_input_tokens"],
        "total_output_tokens": s3_calls * s3["avg_output_tokens"],
        "estimated_cost": estimate_cost(s3_calls, s3["avg_input_tokens"], s3["avg_output_tokens"], input_rate, output_rate),
        "description": s3["description"],
    })

    # Stage 4: Remediation — 1 per journey
    s4_calls = n_journeys
    s4 = STAGE_DEFAULTS["remediation"]
    stages.append({
        "stage": "remediation",
        "label": s4["label"],
        "n_calls": s4_calls,
        "avg_input": s4["avg_input_tokens"],
        "avg_output": s4["avg_output_tokens"],
        "total_input_tokens": s4_calls * s4["avg_input_tokens"],
        "total_output_tokens": s4_calls * s4["avg_output_tokens"],
        "estimated_cost": estimate_cost(s4_calls, s4["avg_input_tokens"], s4["avg_output_tokens"], input_rate, output_rate),
        "description": s4["description"],
    })

    return stages


def print_estimate(
    n_total_sessions: int,
    n_transferred_sessions: int,
    n_journeys: int = 90,
    n_top_journeys: int = 10,
    config_path: Optional[Path] = None,
    n_classify_sessions: Optional[int] = None,
) -> dict:
    """
    Print a cost estimate summary table and return stage breakdown.

    Args:
        n_classify_sessions: If set (POC mode), Stage 1 is capped at this many
                             calls. Downstream stages scale proportionally.
    Does NOT prompt for confirmation — call estimate_and_confirm() for that.
    """
    cfg_path = config_path or LLM_CONFIG_PATH
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        cfg = {}

    client_type = cfg.get("client", "anthropic")
    if client_type == "anthropic":
        model = cfg.get("anthropic", {}).get("model", "claude-3-5-haiku-20241022")
        rates = cfg.get("cost_rates", {}).get("anthropic", {}).get(model, {})
    else:
        model = cfg.get("azure_openai", {}).get("deployment_name", "gpt-4o")
        rates = cfg.get("cost_rates", {}).get("azure_openai", {}).get("gpt-4o", {})

    input_rate = rates.get("input_per_million", 0.80)
    output_rate = rates.get("output_per_million", 4.00)

    stages = compute_stage_estimates(
        n_total_sessions=n_total_sessions,
        n_transferred_sessions=n_transferred_sessions,
        n_journeys=n_journeys,
        n_top_journeys=n_top_journeys,
        input_rate=input_rate,
        output_rate=output_rate,
        n_classify_sessions=n_classify_sessions,
    )

    total_calls = sum(s["n_calls"] for s in stages)
    total_input = sum(s["total_input_tokens"] for s in stages)
    total_output = sum(s["total_output_tokens"] for s in stages)
    total_cost = sum(s["estimated_cost"] for s in stages)

    poc_banner = (
        f"  *** POC MODE: Stage 1 capped at {n_classify_sessions:,} of {n_total_sessions:,} sessions ***\n"
        if n_classify_sessions else ""
    )

    print("\n" + "=" * 72)
    print("COST ESTIMATE — CBP TRANSCRIPT ANALYSIS PIPELINE")
    print("=" * 72)
    if poc_banner:
        print(poc_banner, end="")
    print(f"  LLM Provider:  {client_type} ({model})")
    print(f"  Sessions:      {n_total_sessions:,} total | {n_transferred_sessions:,} escalated")
    print(f"  Input rate:    ${input_rate:.2f}/M tokens | Output: ${output_rate:.2f}/M tokens")
    print()
    print(f"  {'Stage':<35} {'Calls':>7} {'Input Tok':>10} {'Output Tok':>11} {'Est. Cost':>10}")
    print(f"  {'-'*35} {'-'*7} {'-'*10} {'-'*11} {'-'*10}")
    for s in stages:
        print(f"  {s['label']:<35} {s['n_calls']:>7,} {s['total_input_tokens']:>10,} {s['total_output_tokens']:>11,} ${s['estimated_cost']:>9.2f}")
    print(f"  {'TOTAL':<35} {total_calls:>7,} {total_input:>10,} {total_output:>11,} ${total_cost:>9.2f}")
    print()
    print(f"  ➜ About to process {n_classify_sessions or n_total_sessions:,} sessions across {len(stages)} stages.")
    print(f"    Estimated {total_calls:,} LLM calls, ~{(total_input+total_output)/1_000_000:.1f}M tokens.")
    print(f"    Estimated cost: ${total_cost:.2f} at configured rates.")
    print("=" * 72)

    return {
        "stages": stages,
        "total_calls": total_calls,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_estimated_cost_usd": round(total_cost, 2),
        "provider": client_type,
        "model": model,
    }


def estimate_and_confirm(
    n_total_sessions: int,
    n_transferred_sessions: Optional[int] = None,
    n_journeys: int = 90,
    n_top_journeys: int = 10,
    config_path: Optional[Path] = None,
    auto_confirm: bool = False,
    n_classify_sessions: Optional[int] = None,
) -> bool:
    """
    Print cost estimate and require user to type YES to proceed.

    Args:
        n_classify_sessions: POC cap for Stage 1 journey classification calls.
        auto_confirm: If True, skip the prompt (for testing / --dry-run).

    Returns:
        True if user confirmed (or auto_confirm=True), False if declined.
    """
    if n_transferred_sessions is None:
        n_transferred_sessions = int(n_total_sessions * 0.30)  # Estimate 30%

    estimate = print_estimate(
        n_total_sessions=n_total_sessions,
        n_transferred_sessions=n_transferred_sessions,
        n_journeys=n_journeys,
        n_top_journeys=n_top_journeys,
        config_path=config_path,
        n_classify_sessions=n_classify_sessions,
    )

    if auto_confirm:
        print("  [auto_confirm=True] Proceeding without prompt.")
        return True

    print("\n  Type YES (case-sensitive) to proceed, or anything else to cancel: ", end="", flush=True)
    try:
        response = input().strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return False

    if response == "YES":
        print("  Confirmed. Starting pipeline...")
        return True
    else:
        print("  Cancelled. Run again when ready.")
        return False
