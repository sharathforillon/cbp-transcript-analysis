"""
app.py — Mashreq CBP Transcript Analysis Dashboard (Streamlit)

Five views:
  1. Top Priority Journeys     — MVP candidate ranked list
  2. Per-Journey Deep Dive     — Drill-down view
  3. Bot vs. Agent Labor       — Division of effort chart
  4. Knowledge Transfer        — Recurring agent explanations
  5. What-If Scoring           — Weight sliders for live re-ranking

Run: streamlit run src/dashboard/app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_DIR = DATA_DIR / "outputs"

sys.path.insert(0, str(REPO_ROOT))

# ============================================================================
# CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="Mashreq CBP Analysis",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _is_synthetic() -> bool:
    try:
        import yaml
        with open(REPO_ROOT / "config" / "schema_mapping.yaml") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("pipeline", {}).get("mode", "synthetic") == "synthetic"
    except Exception:
        return True


# ============================================================================
# DATA LOADERS (cached)
# ============================================================================

@st.cache_data(ttl=300)
def load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


@st.cache_data(ttl=300)
def load_scored_journeys() -> pd.DataFrame:
    data = load_json(OUTPUT_DIR / "scored_journeys.json")
    if not data:
        return pd.DataFrame()
    journeys = data.get("scored_journeys", [])
    return pd.DataFrame(journeys)


@st.cache_data(ttl=300)
def load_mvp_shortlist() -> dict:
    data = load_json(OUTPUT_DIR / "mvp_shortlist.json")
    return data or {"mvp_shortlist": [], "runners_up": [], "excluded_journeys": []}


@st.cache_data(ttl=300)
def load_leakage_map() -> dict:
    return load_json(OUTPUT_DIR / "leakage_map.json") or {"journeys": [], "summary": {}}


@st.cache_data(ttl=300)
def load_labor() -> dict:
    return load_json(OUTPUT_DIR / "division_of_labor.json") or {"journeys": [], "summary": {}}


@st.cache_data(ttl=300)
def load_knowledge_transfer() -> dict:
    return load_json(OUTPUT_DIR / "knowledge_transfer.json") or {"opportunities": [], "summary": {}}


@st.cache_data(ttl=300)
def load_temporal_trends() -> dict:
    return load_json(OUTPUT_DIR / "temporal_trends.json") or {"journeys": []}


@st.cache_data(ttl=300)
def load_review_log() -> dict:
    return load_json(OUTPUT_DIR / "review_log.json") or {"reviews": [], "accuracy_by_journey": {}}


@st.cache_data(ttl=300)
def load_taxonomy() -> dict:
    try:
        import yaml
        with open(REPO_ROOT / "config" / "taxonomies.yaml") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


@st.cache_data(ttl=300)
def load_remediation_assignments() -> dict:
    """Returns dict keyed by journey_id with LLM remediation outputs."""
    return load_json(OUTPUT_DIR / "remediation_assignments.json") or {}


@st.cache_data(ttl=300)
def load_failure_classifications() -> dict:
    """Returns dict keyed by session_id with per-session failure analysis."""
    return load_json(OUTPUT_DIR / "failure_mode_classifications.json") or {}


# ============================================================================
# HELPERS
# ============================================================================

def _confidence_badge(tier: str) -> str:
    colors = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
    return f"{colors.get(tier, '⚪')} {tier}"


def _export_button(df: pd.DataFrame, label: str, key: str):
    csv = df.to_csv(index=False)
    st.download_button(
        label=f"⬇ Export {label} (CSV)",
        data=csv,
        file_name=f"{key}.csv",
        mime="text/csv",
        key=key,
    )


def _remediation_color(code: str) -> str:
    easy = {"faq_addition", "content_quality_improvement"}
    hard = {"backend_integration", "product_or_policy_change"}
    if code in easy:
        return "🟢"
    elif code in hard:
        return "🔴"
    return "🟡"


# ============================================================================
# HEADER
# ============================================================================

def _inject_css():
    """Global typography + design-token polish.

    Streamlit strips most class attributes, so we target by inline style and
    by data-testid selectors that Streamlit emits for native widgets.
    """
    st.markdown(
        """
        <style>
        /* ── design tokens ───────────────────────────────────────────────── */
        :root {
            --ink-1:    #0b1220;
            --ink-2:    #1f2937;
            --ink-3:    #4b5563;
            --ink-4:    #6b7280;
            --bg-1:     #ffffff;
            --bg-2:     #f7f8fb;
            --bg-3:     #eef1f6;
            --brand:    #1a73e8;
            --brand-d:  #0f4d9e;
            --success:  #21a366;
            --warn:     #ef9b1f;
            --danger:   #d53e2c;
            --accent:   #6750a4;
        }

        /* ── overall page ────────────────────────────────────────────────── */
        section.main > div.block-container { padding-top: 1rem !important; max-width: 1280px; }
        h1, h2, h3, h4 { color: var(--ink-1) !important; font-weight: 700; letter-spacing: -0.01em; }
        h1 { font-size: 28px !important; }
        h2 { font-size: 22px !important; margin-top: 8px !important; }
        h3 { font-size: 18px !important; }
        h4 { font-size: 15px !important; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-3) !important; }

        /* ── force dark text inside coloured cards (Streamlit otherwise inherits) ── */
        div[style*="border-left"] { color: var(--ink-1) !important; }
        div[style*="border-left"] b, div[style*="border-left"] small, div[style*="border-left"] span {
            color: var(--ink-1) !important;
        }

        /* ── metric polish ───────────────────────────────────────────────── */
        [data-testid="stMetric"] {
            background: var(--bg-2);
            border: 1px solid var(--bg-3);
            border-radius: 10px;
            padding: 14px 16px;
        }
        [data-testid="stMetricLabel"] {
            font-size: 12px !important;
            font-weight: 600 !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--ink-3) !important;
        }
        [data-testid="stMetricValue"] {
            font-size: 26px !important;
            font-weight: 700 !important;
            color: var(--ink-1) !important;
        }

        /* ── buttons polish ──────────────────────────────────────────────── */
        [data-testid="stBaseButton-secondary"] {
            background: var(--bg-1);
            border: 1px solid var(--bg-3);
            color: var(--brand);
            font-weight: 600;
            border-radius: 8px;
            padding: 6px 14px;
        }
        [data-testid="stBaseButton-secondary"]:hover {
            background: var(--bg-2);
            border-color: var(--brand);
        }
        [data-testid="stBaseButton-primary"] {
            background: var(--brand);
            border: none;
            color: white;
            font-weight: 600;
            border-radius: 8px;
        }

        /* ── radio & selectbox ───────────────────────────────────────────── */
        [data-testid="stRadio"] label, [data-testid="stSelectbox"] label {
            font-weight: 600;
            color: var(--ink-2) !important;
        }

        /* ── hero card ───────────────────────────────────────────────────── */
        .cbp-hero {
            background: linear-gradient(135deg, #1a73e8 0%, #6750a4 100%);
            color: white;
            border-radius: 14px;
            padding: 24px 28px;
            margin: 8px 0 18px 0;
            box-shadow: 0 6px 18px rgba(26, 115, 232, 0.18);
        }
        .cbp-hero .cbp-eyebrow {
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.18em;
            opacity: 0.88;
            font-weight: 600;
            margin-bottom: 6px;
        }
        .cbp-hero .cbp-number {
            font-size: 48px;
            font-weight: 800;
            line-height: 1.0;
            letter-spacing: -0.02em;
        }
        .cbp-hero .cbp-tagline {
            font-size: 15px;
            opacity: 0.94;
            margin-top: 10px;
            max-width: 720px;
        }

        /* ── Quick-win callout ───────────────────────────────────────────── */
        .cbp-quickwins {
            background: #fff7e6;
            border: 1px solid #f5c87a;
            border-left: 5px solid var(--warn);
            border-radius: 10px;
            padding: 16px 20px;
            margin: 14px 0;
        }
        .cbp-quickwins h4 { color: #8a5a00 !important; margin: 0 0 8px 0; }
        .cbp-quickwins ol { margin: 4px 0 0 18px; padding: 0; color: var(--ink-1); }
        .cbp-quickwins li { padding: 4px 0; font-size: 14px; }
        .cbp-quickwins li b { color: var(--ink-1); }

        /* ── journey card ────────────────────────────────────────────────── */
        .cbp-card {
            background: white;
            border: 1px solid var(--bg-3);
            border-left: 5px solid var(--brand);
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 8px;
            transition: box-shadow 0.15s ease;
        }
        .cbp-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
        .cbp-card .cbp-rank {
            display: inline-block;
            background: var(--bg-2);
            color: var(--brand-d);
            font-weight: 700;
            font-size: 12px;
            padding: 3px 9px;
            border-radius: 99px;
            margin-right: 8px;
        }
        .cbp-card .cbp-name {
            font-size: 16px;
            font-weight: 700;
            color: var(--ink-1);
        }
        .cbp-card .cbp-meta {
            font-size: 12px;
            color: var(--ink-3);
            margin-top: 2px;
        }
        .cbp-card .cbp-row {
            display: flex; gap: 22px; margin-top: 10px; flex-wrap: wrap;
            font-size: 13px; color: var(--ink-2);
        }
        .cbp-card .cbp-row b { color: var(--ink-1); font-size: 14px; }
        .cbp-card .cbp-fix {
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid var(--bg-3);
            font-size: 13px;
            color: var(--ink-3);
        }
        .cbp-card .cbp-fix b { color: var(--ink-1); }

        /* ── containability pill ─────────────────────────────────────────── */
        .cbp-pill {
            display: inline-block;
            padding: 2px 10px;
            border-radius: 99px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .cbp-pill.high   { background: #e6f4ea; color: #1b5e20; }
        .cbp-pill.medium { background: #fff4d6; color: #8a5a00; }
        .cbp-pill.low    { background: #fde0dd; color: #8b1c14; }

        /* ── section divider ─────────────────────────────────────────────── */
        hr { margin: 1.4rem 0 !important; border-color: var(--bg-3) !important; }

        /* ── sidebar ─────────────────────────────────────────────────────── */
        [data-testid="stSidebar"] { background: var(--bg-2); }
        [data-testid="stSidebar"] h2, [data-testid="stSidebar"] p { color: var(--ink-2) !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header():
    _inject_css()
    is_syn = _is_synthetic()
    banner_color = "#fde0dd" if is_syn else "#e6f4ea"
    banner_text  = "⚠️  Synthetic data — not real customer records" if is_syn else "📊 Production data"
    banner_ink   = "#8b1c14" if is_syn else "#1b5e20"

    st.markdown(
        f"""
        <div style='background:{banner_color};color:{banner_ink};padding:6px 14px;
                    border-radius:6px;font-size:12px;font-weight:600;margin-bottom:6px;
                    text-transform:uppercase;letter-spacing:0.05em;display:inline-block;'>
            {banner_text}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h1 style='margin-bottom:2px;'>Mashreq CBP — Containment Strategy</h1>"
        "<p style='color:#6b7280;margin-top:0;font-size:14px;'>"
        "MVP journey prioritization · powered by LLM analysis of bot→agent transcripts"
        "</p>",
        unsafe_allow_html=True,
    )


# ============================================================================
# VIEW 1: TOP PRIORITY JOURNEYS
# ============================================================================

def _containability(rem: dict, cbp_fit: float) -> tuple[str, str, float]:
    """Return (label, color, score 0–1) for how containable this journey is.

    Containability = how realistic is it to move this journey from agent → bot,
    given the recommended remediation's complexity and the CBP's current capability fit.
    """
    top = (rem.get("recommended_remediations") or [{}])[0]
    impact     = top.get("estimated_impact", "medium")
    complexity = top.get("implementation_complexity", "medium")
    imp_w   = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(impact, 0.5)
    comp_w  = {"low": 1.0, "medium": 0.6, "high": 0.3}.get(complexity, 0.5)
    score   = imp_w * comp_w * (0.4 + 0.6 * cbp_fit)
    if score >= 0.55:
        return "HIGH", "#34a853", score
    if score >= 0.30:
        return "MEDIUM", "#fbbc04", score
    return "LOW", "#ea4335", score


def _monthly_volume(row: dict, total_dataset_sessions: int, months: int = 6) -> int:
    """Project monthly agent-handoff volume from the classified sample.

    POC mode: classifier saw a small fraction of total sessions, so we
    scale up the journey's share to the full dataset.
    """
    n_classified = row.get("total_sessions", 0)
    sample_share = row.get("volume_share", 0)
    if sample_share > 0:
        monthly = int(sample_share * total_dataset_sessions / max(months, 1))
        return max(monthly, n_classified)
    return n_classified


def view_top_journeys():
    mvp        = load_mvp_shortlist()
    shortlist  = mvp.get("mvp_shortlist", [])
    runners    = mvp.get("runners_up", [])
    excluded   = mvp.get("excluded_journeys", [])
    rem_data   = load_remediation_assignments()
    leakage    = load_leakage_map()

    if not shortlist:
        st.warning(
            "No scored journeys found. Run the pipeline first: "
            "`python -m src.pipeline --source full --max-classify 100`"
        )
        return

    total_dataset_sessions = leakage.get("summary", {}).get("total_sessions", 10000)

    # ── enrich rows with strategic metrics ─────────────────────────────
    enriched = []
    for r in shortlist + runners:
        jid    = r["journey_id"]
        rem    = rem_data.get(jid, {})
        c_label, c_color, c_score = _containability(rem, r.get("cbp_capability_fit", 0.5))
        monthly = _monthly_volume(r, total_dataset_sessions)
        top_rem = (rem.get("recommended_remediations") or [{}])[0]
        enriched.append({
            **r,
            "_monthly_volume":        monthly,
            "_containability_label":  c_label,
            "_containability_color":  c_color,
            "_containability_score":  c_score,
            "_rem_object":            rem,
            "_top_rem":               top_rem,
            "_impact":                top_rem.get("estimated_impact", "medium"),
            "_complexity":            top_rem.get("implementation_complexity", "medium"),
        })

    enriched.sort(key=lambda x: x.get("composite_score", 0) or 0, reverse=True)
    top10 = enriched[:10]

    # ── EXECUTIVE METRICS ──────────────────────────────────────────────
    total_volume       = sum(j["_monthly_volume"]      for j in top10)
    high_containable   = sum(1 for j in top10 if j["_containability_label"] == "HIGH")

    # Quick wins = high or medium impact + low complexity
    quick_wins = [
        j for j in top10
        if j["_impact"] in {"high", "medium"} and j["_complexity"] == "low"
    ][:3]

    # Annual savings opportunity (containment lift × volume × cost)
    impact_mult = {"high": 0.60, "medium": 0.35, "low": 0.15}
    annual_opportunity = sum(
        j["_monthly_volume"] * impact_mult.get(j["_impact"], 0.35) * 4.50 * 12
        for j in top10
    )

    # Time to first win
    fastest_delivery = "1–2 weeks" if quick_wins else "3–6 weeks"

    # ── HERO BANNER ────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class='cbp-hero'>
          <div class='cbp-eyebrow'>Total Containment Opportunity</div>
          <div class='cbp-number'>${annual_opportunity / 1000:,.0f}K <span style='font-size:22px;opacity:0.7;font-weight:500;'>annual</span></div>
          <div class='cbp-tagline'>
            Across the top 10 MVP journeys, we can recapture an estimated
            <b>{int(total_volume * 0.4):,} agent handoffs per month</b> into bot scope,
            unlocking <b>${annual_opportunity / 12 / 1000:,.0f}K / month</b> in agent cost savings
            and freeing capacity for higher-value work.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── 4 SUPPORTING KPIs ──────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Monthly Handoffs",   f"{total_volume:,}",
              help="Estimated bot→agent handoffs/month across the top 10")
    k2.metric("HIGH Containable",   f"{high_containable} / 10",
              help="Journeys with low-complexity, high-impact fix paths")
    k3.metric("Quick Wins",         f"{len(quick_wins)}",
              help="High/medium impact + low complexity remediations")
    k4.metric("Time to First Win",  fastest_delivery,
              help="Fastest-deliverable remediation in the shortlist")

    # ── QUICK WINS CALLOUT (the Monday-morning answer) ─────────────────
    if quick_wins:
        items_html = "".join(
            f"<li><b>{q.get('journey_name','—')}</b> "
            f"&nbsp;·&nbsp; {q['_top_rem'].get('remediation_type','').replace('_',' ').title()} "
            f"&nbsp;·&nbsp; ~{q['_monthly_volume']:,} handoffs/mo</li>"
            for q in quick_wins
        )
        st.markdown(
            f"""
            <div class='cbp-quickwins'>
              <h4>🎯 This Quarter's Quick Wins</h4>
              <p style='font-size:13px;color:#5f4500;margin:0 0 6px 0;'>
                Low-complexity remediations with measurable impact — ship in 1–2 weeks.
              </p>
              <ol>{items_html}</ol>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── STRATEGIC SHORTLIST ────────────────────────────────────────────
    st.markdown("### Strategic MVP Shortlist")
    st.caption("Ranked by composite strategic score (volume × containability × CBP fit). "
               "Click any card to drill into the WHAT, WHERE and HOW.")

    for i, row in enumerate(top10):
        rank    = row.get("mvp_rank", i + 1)
        jid     = row["journey_id"]
        name    = row.get("journey_name", jid)
        domain  = (row.get("domain", "") or jid.split("_")[0]).upper()
        conf    = row.get("confidence", "MEDIUM")
        score   = row.get("composite_score", 0)
        cbp     = row.get("cbp_capability_fit", 0.5)
        rem     = row["_rem_object"]
        c_lbl   = row["_containability_label"]
        c_col   = row["_containability_color"]
        vol     = row["_monthly_volume"]
        pfm     = (rem.get("primary_failure_mode", "") or "—").replace("_", " ").title()
        rem_typ = row["_top_rem"].get("remediation_type", "—").replace("_", " ").title()
        rationale_short = (row["_top_rem"].get("rationale") or "")[:160]
        if rationale_short and len(row["_top_rem"].get("rationale", "")) > 160:
            rationale_short += "…"

        st.markdown(
            f"""
            <div class='cbp-card' style='border-left-color:{c_col};'>
              <div style='display:flex;justify-content:space-between;align-items:flex-start;'>
                <div>
                  <span class='cbp-rank'>#{rank}</span>
                  <span class='cbp-name'>{name}</span>
                  <div class='cbp-meta'>{domain} &nbsp;·&nbsp; {jid} &nbsp;·&nbsp; {_confidence_badge(conf)}</div>
                </div>
                <div style='text-align:right;'>
                  <span class='cbp-pill {c_lbl.lower()}'>{c_lbl} containability</span>
                  <div style='font-size:11px;color:#6b7280;margin-top:4px;'>Score {score:.2f}</div>
                </div>
              </div>
              <div class='cbp-row'>
                <span>📊 <b>{vol:,}</b> handoffs/mo</span>
                <span>🤖 <b>{cbp:.0%}</b> CBP fit</span>
                <span>⚠️ Primary failure: <b>{pfm}</b></span>
              </div>
              <div class='cbp-fix'>
                <b>🛠 Top fix:</b> {rem_typ} &nbsp;·&nbsp; <i>{rationale_short or "—"}</i>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(f"🔍  Drill into {jid}", key=f"drill_{jid}"):
            st.session_state["selected_journey"] = jid
            st.session_state["_pending_nav"]     = "Per-Journey Deep Dive"
            st.rerun()

    # ── secondary content (collapsed) ──────────────────────────────────
    st.markdown("---")
    if runners:
        with st.expander(f"🏃  Runners-up ({len(runners)} journeys)"):
            r_df = pd.DataFrame(runners)[
                ["journey_id", "journey_name", "total_sessions",
                 "composite_score", "primary_remediation", "cbp_capability_fit"]
            ].rename(columns={
                "journey_id":          "ID",
                "journey_name":        "Journey",
                "total_sessions":      "Sample",
                "composite_score":     "Score",
                "primary_remediation": "Remediation",
                "cbp_capability_fit":  "CBP Fit",
            })
            r_df["Score"]   = r_df["Score"].apply(lambda x: f"{x:.3f}")
            r_df["CBP Fit"] = r_df["CBP Fit"].apply(lambda x: f"{x:.0%}")
            st.dataframe(r_df, use_container_width=True, hide_index=True)

    if excluded:
        with st.expander(f"🚫  Excluded journeys ({len(excluded)})"):
            e_df = pd.DataFrame(excluded)[["journey_id", "journey_name", "exclusion_reason"]]
            e_df.columns = ["ID", "Journey", "Excluded because"]
            st.dataframe(e_df, use_container_width=True, hide_index=True)
            st.caption("Excluded: regulatory_required_human, high_emotional_stakes, product_advisory_complex.")

    with st.expander("📐  Methodology"):
        st.markdown(
            "**Strategic Score** = volume × containability × CBP fit, weighted by taxonomy priorities.  \n"
            "**Containability** = `impact × (1 / complexity) × (0.4 + 0.6 × CBP fit)` → HIGH ≥ 0.55, MEDIUM ≥ 0.30, LOW < 0.30  \n"
            "**Monthly Volume** = journey share of sample × total dataset sessions ÷ analysis window months  \n"
            "**Annual Opportunity** = sum over top-10 of *(monthly_volume × impact_lift × $4.50 × 12)*  \n"
            "**Impact lifts** = high 60%, medium 35%, low 15% (conservative)."
        )

    all_df = pd.DataFrame(shortlist + runners)
    if not all_df.empty:
        _export_button(all_df, "Strategic Shortlist", "export_mvp")


# ============================================================================
# VIEW 2: PER-JOURNEY DEEP DIVE
# ============================================================================

def view_per_journey():  # noqa: C901
    pass  # noop — actual rendering begins below
    st.markdown("### Strategic Journey Drill-Down")
    st.caption("Diagnose a single MVP candidate end-to-end: failure modes, failure points, and the fix plan.")

    # ── load all data sources ──────────────────────────────────────────────
    mvp            = load_mvp_shortlist()
    rem_assigns    = load_remediation_assignments()
    fail_clf       = load_failure_classifications()
    leakage        = load_leakage_map()
    trends         = load_temporal_trends()

    all_journeys = mvp.get("mvp_shortlist", []) + mvp.get("runners_up", [])
    if not all_journeys:
        st.warning("No journey data. Run the pipeline first.")
        return

    # ── journey selector ──────────────────────────────────────────────────
    default_jid    = st.session_state.get("selected_journey", all_journeys[0]["journey_id"])
    journey_opts   = [f"{j['journey_id']} — {j['journey_name']}" for j in all_journeys]
    default_idx    = next((i for i, j in enumerate(all_journeys) if j["journey_id"] == default_jid), 0)
    selected_label = st.selectbox("Select Journey", journey_opts, index=default_idx, key="v2_journey")
    selected_jid   = selected_label.split(" — ")[0]
    jd             = next((j for j in all_journeys if j["journey_id"] == selected_jid), {})
    if not jd:
        st.warning("Journey data not found.")
        return

    # ── pull related data ─────────────────────────────────────────────────
    rem        = rem_assigns.get(selected_jid, {})
    j_sessions = [v for v in fail_clf.values() if v.get("journey_id") == selected_jid]
    j_leakage  = next((j for j in leakage.get("journeys", []) if j["journey_id"] == selected_jid), {})
    j_trend    = next((j for j in trends.get("journeys", []) if j["journey_id"] == selected_jid), {})
    timeseries = j_trend.get("timeseries", [])

    # ── derived scalars (strategic, not rate-based) ───────────────────────
    cbp_fit         = jd.get("cbp_capability_fit", 0.5)
    n_sample        = jd.get("total_sessions", 0)
    score           = jd.get("composite_score", 0)
    top_rem         = (rem.get("recommended_remediations") or [{}])[0]
    impact_level    = top_rem.get("estimated_impact", "medium")
    complexity      = top_rem.get("implementation_complexity", "medium")
    rem_type        = top_rem.get("remediation_type", jd.get("primary_remediation", "unknown"))

    total_dataset_sessions = leakage.get("summary", {}).get("total_sessions", 10000)
    monthly_volume         = _monthly_volume(jd, total_dataset_sessions)
    c_label, c_color, c_score = _containability(rem, cbp_fit)

    # ══════════════════════════════════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════════════════════════════════
    domain = (jd.get("domain", "") or selected_jid.split("_")[0]).upper()
    rank   = jd.get("mvp_rank", "—")
    st.markdown(f"### {jd.get('journey_name', selected_jid)}")
    st.caption(
        f"**{domain}** · MVP Rank **#{rank}** · Strategic Score **{score:.3f}** · "
        f"{_confidence_badge(jd.get('confidence', 'MEDIUM'))} confidence"
    )

    # ── strategic KPI row (volume + containability, NOT rates) ────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📊 Monthly Volume", f"{monthly_volume:,}",
              help="Estimated bot→agent handoffs per month for this journey")
    c2.metric("🔬 Sessions Analyzed", f"{n_sample:,}",
              help="Number of sessions LLM-coded for this journey in the current sample")
    c3.metric("🎯 Containability", c_label,
              help=f"How realistic to move this journey back into bot scope (score: {c_score:.2f})")
    c4.metric("🤖 CBP Fit", f"{cbp_fit:.0%}",
              help="Share of this journey the bot can handle with current capabilities")
    c5.metric("🛠 Implementation", complexity.title(),
              help=f"Effort to deliver the top remediation: {rem_type.replace('_', ' ').title()}")

    # ══════════════════════════════════════════════════════════════════════
    # 1️⃣  WHAT IS BREAKING?
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(
        "<h4 style='color:#1a73e8!important;'>STEP 1 &nbsp;·&nbsp; What is breaking?</h4>"
        "<p style='font-size:13px;color:#6b7280;margin-top:-6px;'>"
        "Failure modes, customer intents and the LLM's diagnostic synthesis.</p>",
        unsafe_allow_html=True,
    )

    primary_fm  = rem.get("primary_failure_mode", "")
    fm_dist     = rem.get("failure_mode_distribution", {})
    llm_reason  = rem.get("reasoning", "")

    if primary_fm:
        st.error(
            f"**Primary Failure Mode:** `{primary_fm.replace('_', ' ').title()}` "
            f"— confirmed across {rem.get('sessions_in_sample', n_sample)} sampled session(s)."
        )

    col_synth, col_dist = st.columns([3, 2])

    with col_synth:
        if llm_reason:
            st.markdown("**🤖 LLM Diagnostic Synthesis**")
            st.info(llm_reason)

        # customer intent clustering
        if j_sessions:
            intents = [s.get("customer_intent", "") for s in j_sessions if s.get("customer_intent")]
            if intents:
                st.markdown("**🎯 Customer Intents Observed**")
                for intent in intents[:5]:
                    st.markdown(f"- {intent}")

    with col_dist:
        if fm_dist:
            st.markdown("**Failure Mode Distribution**")
            fm_df = pd.DataFrame([
                {"Failure Mode": k.replace("_", " ").title(), "Sessions": v}
                for k, v in sorted(fm_dist.items(), key=lambda x: -x[1])
            ])
            st.bar_chart(fm_df.set_index("Failure Mode"))

        tr_breakdown = {
            k: v for k, v in j_leakage.get("transfer_reason_breakdown", {}).items()
            if k != "unknown"
        }
        if tr_breakdown:
            st.markdown("**Transfer Reason Codes**")
            total_tr = sum(tr_breakdown.values())
            for reason, count in sorted(tr_breakdown.items(), key=lambda x: -x[1])[:6]:
                pct = count / total_tr * 100 if total_tr else 0
                st.caption(f"• `{reason}` — {count} ({pct:.0f}%)")

    # ══════════════════════════════════════════════════════════════════════
    # 2️⃣  WHERE IS IT BREAKING?
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(
        "<h4 style='color:#1a73e8!important;'>STEP 2 &nbsp;·&nbsp; Where is it breaking?</h4>"
        "<p style='font-size:13px;color:#6b7280;margin-top:-6px;'>"
        "The exact bot turn that fails, plus segmentation by channel / language / geography.</p>",
        unsafe_allow_html=True,
    )

    if j_sessions:
        st.markdown("**📍 Per-Session Bot Failure Points**")

        def _trunc(s: str, n: int = 70) -> str:
            return (s[:n] + "…") if len(s) > n else s

        rows = [
            {
                "Customer Intent":    _trunc(s.get("customer_intent", "—"), 55),
                "Bot Failure Point":  _trunc(s.get("bot_failure_point", "—"), 75),
                "Root Blocker":       _trunc(s.get("actual_blocker", "—"), 60),
                "Agent Action":       _trunc(s.get("agent_action", "—"), 60),
                "Prevention":         s.get("what_would_have_prevented_escalation", "—"),
            }
            for s in j_sessions[:8]
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Expandable evidence per session
        for s in j_sessions[:3]:
            detail = s.get("prevention_detail", "")
            if detail:
                sid_short = s.get("session_id", "")[:14]
                with st.expander(f"💬 Evidence — session `{sid_short}…`"):
                    st.markdown(f"**Prevention detail:** {detail}")
                    if s.get("customer_confusion_signal"):
                        st.markdown(f"**Customer confusion signal:** {s['customer_confusion_signal']}")
                    if s.get("agent_resolution_status"):
                        st.markdown(f"**Agent resolution status:** `{s['agent_resolution_status']}`")
    else:
        st.info("No per-session evidence available for this journey.")

    # segmentation
    seg_by_channel  = j_leakage.get("by_channel", {})
    seg_by_language = j_leakage.get("by_language", {})
    seg_by_geo      = j_leakage.get("by_geography", {})
    if any([seg_by_channel, seg_by_language, seg_by_geo]):
        st.markdown("**🌐 Volume Segmentation**")
        sc1, sc2, sc3 = st.columns(3)

        def _seg_volume(seg: dict, col, label: str):
            if not seg:
                return
            rows = [
                {"Segment": k, "Sessions": v["total"], "Escalations": v["escalated"]}
                for k, v in sorted(seg.items(), key=lambda x: -x[1].get("total", 0))
                if v.get("total", 0) > 0
            ]
            if rows:
                col.markdown(f"_{label}_")
                col.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                top = rows[0]
                col.caption(f"🎯 Largest segment: **{top['Segment']}** ({top['Sessions']:,} sessions)")

        _seg_volume(seg_by_channel,  sc1, "By Channel")
        _seg_volume(seg_by_language, sc2, "By Language")
        _seg_volume(seg_by_geo,      sc3, "By Geography")

    # trend
    if timeseries:
        st.markdown("**📈 Weekly Volume Trend**")
        ts_df = pd.DataFrame(timeseries).rename(
            columns={"week": "Week", "escalated": "Agent Handoffs"}
        )
        col_chart, col_stat = st.columns([3, 1])
        with col_chart:
            st.line_chart(ts_df.set_index("Week")[["Agent Handoffs"]])
        with col_stat:
            if len(timeseries) >= 4:
                early = sum(w.get("escalated", 0) for w in timeseries[:4])
                late  = sum(w.get("escalated", 0) for w in timeseries[-4:])
                delta = late - early
                trend = "📈 Growing" if delta > 5 else ("📉 Shrinking" if delta < -5 else "➡️ Stable")
                st.metric("Trend", trend)
                st.metric("Recent / Early", f"{late} / {early}",
                          delta=f"{delta:+d}")
        step_changes = j_trend.get("step_changes", [])
        if step_changes:
            st.warning(
                f"⚠️ {len(step_changes)} step-change(s) detected — investigate "
                "deployments or product changes around these dates."
            )

    # ══════════════════════════════════════════════════════════════════════
    # 3️⃣  HOW TO FIX IT?
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(
        "<h4 style='color:#1a73e8!important;'>STEP 3 &nbsp;·&nbsp; How to fix it?</h4>"
        "<p style='font-size:13px;color:#6b7280;margin-top:-6px;'>"
        "Priority-ordered remediation plan, content additions, and knowledge transfer opportunities.</p>",
        unsafe_allow_html=True,
    )

    rems = rem.get("recommended_remediations", [])
    if rems:
        for i, r in enumerate(rems, 1):
            rtype     = r.get("remediation_type", "").replace("_", " ").title()
            rationale = r.get("rationale", "No rationale provided.")
            imp       = r.get("estimated_impact", "medium")
            comp      = r.get("implementation_complexity", "medium")
            imp_icon  = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(imp, "🟡")
            comp_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(comp, "🟡")
            delivery  = {"high": "8–12 wks", "medium": "3–6 wks", "low": "1–2 wks"}.get(comp, "4–6 wks")
            st.markdown(
                f"""
                <div style='background:#f8f9fa;border-left:4px solid #1a73e8;
                            border-radius:6px;padding:14px 18px;margin-bottom:10px;'>
                <b style='color:#1a1a1a;font-size:15px;'>Priority {i}: {rtype}</b><br>
                <small style='color:#555;'>{imp_icon} Impact: <b>{imp.title()}</b>
                &nbsp;|&nbsp; {comp_icon} Complexity: <b>{comp.title()}</b>
                &nbsp;|&nbsp; ⏱ Est. delivery: <b>{delivery}</b></small><br>
                <small style='color:#222;margin-top:6px;display:block;'>{rationale}</small>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("No remediation plan available — run pipeline with real LLM to generate recommendations.")

    col_faq, col_kt = st.columns(2)

    with col_faq:
        faq_topics = rem.get("recommended_faq_topics", [])
        if faq_topics:
            st.markdown("**📋 Content / FAQ Topics to Add**")
            for t in faq_topics:
                st.markdown(f"- {t}")

    with col_kt:
        kt_note = rem.get("knowledge_transfer_opportunity")
        if kt_note:
            st.markdown("**💡 Knowledge Transfer Opportunity**")
            st.info(kt_note)

    # ══════════════════════════════════════════════════════════════════════
    # CTA: STRATEGIC IMPACT PROJECTION (hidden by default)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("---")
    show_proj_key = f"show_proj_{selected_jid}"
    if show_proj_key not in st.session_state:
        st.session_state[show_proj_key] = False

    cta_col, _ = st.columns([1, 2])
    with cta_col:
        if not st.session_state[show_proj_key]:
            if st.button("📊 Show Strategic Impact Projection", key=f"btn_{show_proj_key}",
                         use_container_width=True, type="primary"):
                st.session_state[show_proj_key] = True
                st.rerun()
        else:
            if st.button("🔼 Hide Projection", key=f"hide_{show_proj_key}",
                         use_container_width=True):
                st.session_state[show_proj_key] = False
                st.rerun()

    if st.session_state[show_proj_key]:
        # ── compute projection ───────────────────────────────────────────
        impact_mult   = {"high": 0.60, "medium": 0.35, "low": 0.15}.get(impact_level, 0.35)
        csat_uplift   = {"high": 0.5,  "medium": 0.3,  "low": 0.1}.get(impact_level, 0.3)
        timeline      = {"high": "8–12 weeks", "medium": "3–6 weeks", "low": "1–2 weeks"}.get(complexity, "4–6 weeks")
        COST_PER_HND  = 4.50   # $ per agent handoff
        MIN_PER_HND   = 8

        proj_vol      = int(monthly_volume * (1 - impact_mult))
        vol_saved     = monthly_volume - proj_vol
        cost_saved_mo = vol_saved * COST_PER_HND
        mins_saved_mo = vol_saved * MIN_PER_HND
        avg_csat      = jd.get("avg_csat", 3.0)
        proj_csat     = min(5.0, avg_csat + csat_uplift)

        st.markdown("### 🎯 Strategic Impact Projection")
        rem_label = rem_type.replace("_", " ").title()
        st.markdown(
            f"If **{rem_label}** is delivered (impact: **{impact_level.title()}** · "
            f"complexity: **{complexity.title()}** · ETA: **{timeline}**), here is the projected containment lift:"
        )

        before, arrow, after = st.columns([5, 1, 5])
        with before:
            st.markdown(
                "<div style='background:#fce8e6;border-radius:8px;padding:10px 14px;"
                "text-align:center;margin-bottom:8px;'>"
                "<b style='color:#c62828;'>Status Quo</b></div>",
                unsafe_allow_html=True,
            )
            m1, m2 = st.columns(2)
            m1.metric("Monthly Handoffs", f"{monthly_volume:,}")
            m2.metric("Monthly Cost",     f"${monthly_volume * COST_PER_HND:,.0f}")
            m1.metric("Agent Mins / mo",  f"{monthly_volume * MIN_PER_HND:,}")
            m2.metric("Avg CSAT",         f"{avg_csat:.1f}/5.0")

        with arrow:
            st.markdown(
                "<div style='text-align:center;padding-top:55px;font-size:26px;color:#555;'>→</div>",
                unsafe_allow_html=True,
            )

        with after:
            st.markdown(
                "<div style='background:#e6f4ea;border-radius:8px;padding:10px 14px;"
                "text-align:center;margin-bottom:8px;'>"
                "<b style='color:#1b5e20;'>After Containment Fix</b></div>",
                unsafe_allow_html=True,
            )
            m1, m2 = st.columns(2)
            m1.metric("Monthly Handoffs", f"{proj_vol:,}",
                      delta=f"-{vol_saved:,}", delta_color="inverse")
            m2.metric("Monthly Cost",     f"${proj_vol * COST_PER_HND:,.0f}",
                      delta=f"-${cost_saved_mo:,.0f}", delta_color="inverse")
            m1.metric("Agent Mins / mo",  f"{proj_vol * MIN_PER_HND:,}",
                      delta=f"-{mins_saved_mo:,}", delta_color="inverse")
            m2.metric("Projected CSAT",   f"{proj_csat:.1f}/5.0",
                      delta=f"+{csat_uplift:.1f}", delta_color="normal")

        st.success(
            f"**Projected annual saving: ${cost_saved_mo * 12:,.0f}** · "
            f"**{vol_saved * 12:,} handoffs contained/year** · "
            f"**{int(mins_saved_mo * 12 / 60):,} agent-hours freed/year**"
        )
        st.caption(
            f"ℹ️ Methodology — {impact_level.title()} impact → {impact_mult:.0%} containment lift. "
            f"$4.50 per handoff · 8 min avg handle time · CSAT uplift +{csat_uplift:.1f} pts. "
            "Conservative estimates; refine after real deployment data."
        )

    # ── word doc ──────────────────────────────────────────────────────────
    st.markdown("---")
    if st.button("📄 Generate Word Report for this Journey", key="gen_doc"):
        with st.spinner("Generating Word document…"):
            try:
                from src.reporting.per_journey_doc import JourneyDocBuilder
                builder   = JourneyDocBuilder(jd)
                safe_name = jd.get("journey_name", selected_jid).replace(" ", "_").replace("/", "_")[:40]
                doc_path  = OUTPUT_DIR / "journey_reports" / f"{selected_jid}_{safe_name}.docx"
                doc_path.parent.mkdir(parents=True, exist_ok=True)
                builder.build(doc_path)
                with open(doc_path, "rb") as f:
                    st.download_button(
                        "⬇ Download Word Report",
                        data=f.read(),
                        file_name=doc_path.name,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                st.success(f"Report generated: {doc_path.name}")
            except Exception as e:
                st.error(f"Document generation failed: {e}")


# ============================================================================
# VIEW 3: BOT vs. AGENT DIVISION OF LABOR
# ============================================================================

def view_division_of_labor():
    st.header("3. Bot vs. Agent Division of Labor")
    st.caption(
        "Per-journey breakdown: what the bot handled correctly, what it escalated, "
        "and where agent performance lagged."
    )

    labor = load_labor()
    journeys = labor.get("journeys", [])
    summary = labor.get("summary", {})

    if not journeys:
        st.warning("No division of labor data. Run pipeline first.")
        return

    # ---- AGGREGATE METRICS ----
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Bot Correctly Contained", f"{summary.get('total_correctly_contained', 0):,}")
    with col2:
        st.metric("Bot Correctly Escalated", f"{summary.get('total_correctly_escalated', 0):,}")
    with col3:
        st.metric("Bot Incorrectly Escalated", f"{summary.get('total_incorrectly_escalated', 0):,}")
    with col4:
        st.metric("Agent Slow to Resolve", f"{summary.get('total_agent_slow', 0):,}")

    st.divider()

    df = pd.DataFrame(journeys)
    if df.empty:
        return

    # ---- SORT ----
    sort_col = st.selectbox(
        "Sort by:",
        ["bot_incorrectly_escalated", "bot_correctly_contained", "agent_slow_to_resolve", "total_sessions"],
        format_func=lambda x: x.replace("_", " ").title(),
        key="v3_sort",
    )
    df = df.sort_values(sort_col, ascending=False).head(25)

    # ---- STACKED BAR ----
    chart_df = df.set_index("journey_id")[
        ["bot_correctly_contained", "bot_correctly_escalated",
         "bot_incorrectly_escalated", "agent_slow_to_resolve"]
    ]
    chart_df.columns = ["Bot Contained ✓", "Bot Escalated ✓", "Bot Escalated ✗", "Agent Slow"]
    st.bar_chart(chart_df)

    st.divider()

    # ---- TABLE ----
    st.subheader("Detailed Breakdown")
    display_df = df[[
        "journey_id", "journey_name", "total_sessions",
        "bot_correctly_contained", "bot_correctly_escalated",
        "bot_incorrectly_escalated", "agent_slow_to_resolve", "escalation_rate"
    ]].copy()
    display_df["escalation_rate"] = display_df["escalation_rate"].apply(lambda x: f"{x:.0%}")
    display_df.columns = ["ID", "Journey", "Sessions", "Bot ✓ Contained", "Bot ✓ Escalated",
                          "Bot ✗ Escalated", "Agent Slow", "Escalation Rate"]

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    with st.expander("What does each column mean?"):
        st.markdown("""
        - **Bot ✓ Contained**: Session handled entirely by bot, no agent needed
        - **Bot ✓ Escalated**: Bot correctly escalated per policy (e.g. loan restructuring)
        - **Bot ✗ Escalated**: Bot escalated unnecessarily — these are improvement opportunities
        - **Agent Slow**: Agent took >5 minutes to resolve after receiving transfer
        """)

    _export_button(df, "Division of Labor", "export_labor")


# ============================================================================
# VIEW 4: KNOWLEDGE TRANSFER OPPORTUNITIES
# ============================================================================

def view_knowledge_transfer():
    st.header("4. Agent Knowledge Transfer Opportunities")
    st.caption(
        "Recurring agent explanations that could be automated by the bot. "
        "Sorted by frequency × bot feasibility."
    )

    kt = load_knowledge_transfer()
    opportunities = kt.get("opportunities", [])
    kt_summary = kt.get("summary", {})

    if not opportunities:
        st.warning("No knowledge transfer data. Run pipeline first.")
        return

    # ---- SUMMARY ----
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Opportunities", kt_summary.get("total_opportunities", 0))
    with col2:
        bot_feasible = sum(1 for o in opportunities if o.get("bot_feasibility") == "yes")
        st.metric("Bot-Feasible (No Integration)", bot_feasible)
    with col3:
        with_data = sum(1 for o in opportunities if o.get("bot_feasibility") == "with_data_access")
        st.metric("Feasible with Data Access", with_data)

    st.divider()

    # ---- FILTERS ----
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        feasibility_filter = st.multiselect(
            "Bot Feasibility",
            ["yes", "with_data_access", "no"],
            default=["yes", "with_data_access"],
            key="v4_feasibility",
        )
    with col_f2:
        min_freq = st.slider("Min Frequency", 1, 50, 3, key="v4_min_freq")

    filtered = [
        o for o in opportunities
        if o.get("bot_feasibility") in feasibility_filter
        and o.get("frequency", 0) >= min_freq
    ]

    st.caption(f"Showing {len(filtered)} opportunities (filtered from {len(opportunities)} total)")

    # ---- OPPORTUNITY LIST ----
    for i, opp in enumerate(filtered[:30]):
        jid = opp.get("journey_id", "")
        jname = opp.get("journey_name", jid)
        freq = opp.get("frequency", 0)
        feasibility = opp.get("bot_feasibility", "")
        impact = opp.get("automation_impact_score", 0)
        msg = opp.get("representative_message", "")[:200]

        feasibility_icon = {"yes": "🟢", "with_data_access": "🟡", "no": "🔴"}.get(feasibility, "⚪")

        with st.expander(
            f"{feasibility_icon} [{jid}] {msg[:80]}... | Freq: {freq} | Impact: {impact:.1f}",
            expanded=i == 0,
        ):
            st.markdown(f"**Journey:** {jname}")
            st.markdown(f"**Frequency:** Appeared {freq} times in corpus")
            st.markdown(f"**Bot Feasibility:** {feasibility_icon} {feasibility}")
            st.markdown(f"**Automation Impact Score:** {impact:.1f}")
            st.markdown("**Representative Agent Message:**")
            st.code(opp.get("representative_message", ""), language=None)

            examples = opp.get("example_messages", [])
            if len(examples) > 1:
                st.markdown("**Other Examples:**")
                for ex in examples[1:3]:
                    st.text(f"• {ex[:150]}")

    if not filtered:
        st.info("No opportunities match the current filters.")
        return

    opp_df = pd.DataFrame(filtered)[["journey_id", "journey_name", "representative_message",
                                      "frequency", "bot_feasibility", "automation_impact_score"]]
    _export_button(opp_df, "Knowledge Transfer", "export_kt")


# ============================================================================
# VIEW 5: WHAT-IF SCORING
# ============================================================================

def view_what_if():
    st.header("5. What-If Scoring")
    st.caption(
        "Adjust scoring weights to see how the MVP ranking changes. "
        "Default weights match config/taxonomies.yaml."
    )

    # ---- WEIGHT SLIDERS ----
    st.subheader("Scoring Weights")
    st.caption("Note: These weights do not need to sum to 1.0 — the regulatory penalty is subtracted.")

    col1, col2, col3 = st.columns(3)
    with col1:
        w_volume = st.slider("Volume Share", 0.0, 0.5, 0.25, 0.05, key="w_volume")
        w_leakage = st.slider("Leakage Rate", 0.0, 0.5, 0.25, 0.05, key="w_leakage")
    with col2:
        w_remediability = st.slider("Remediability", 0.0, 0.5, 0.20, 0.05, key="w_remediability")
        w_cbp = st.slider("CBP Capability Fit", 0.0, 0.5, 0.15, 0.05, key="w_cbp")
    with col3:
        w_csat = st.slider("CSAT Signal", 0.0, 0.3, 0.10, 0.05, key="w_csat")
        w_reg = st.slider("Regulatory Penalty", -0.2, 0.0, -0.05, 0.01, key="w_reg")

    custom_weights = {
        "volume_share": w_volume,
        "leakage_rate": w_leakage,
        "remediability": w_remediability,
        "cbp_capability_fit": w_cbp,
        "csat_signal": w_csat,
        "regulatory_complexity_penalty": w_reg,
    }

    # ---- LIVE RE-RANKING ----
    scored = load_json(OUTPUT_DIR / "scored_journeys.json")
    if not scored:
        st.warning("No scored journeys. Run pipeline first.")
        return

    journeys = scored.get("scored_journeys", [])
    if not journeys:
        return

    def _recompute_score(j: dict, w: dict) -> float:
        return (
            w.get("volume_share", 0.25) * j.get("volume_share", 0) * 10
            + w.get("leakage_rate", 0.25) * j.get("escalation_rate", 0)
            + w.get("remediability", 0.20) * j.get("remediability", 0)
            + w.get("cbp_capability_fit", 0.15) * j.get("cbp_capability_fit", 0)
            + w.get("csat_signal", 0.10) * j.get("csat_signal", 0)
            + w.get("regulatory_complexity_penalty", -0.05) * j.get("regulatory_complexity", 0)
        )

    eligible = [j for j in journeys if not j.get("excluded") and not j.get("below_threshold")]

    # Default ranking
    default_ranked = sorted(eligible, key=lambda j: -j.get("composite_score", 0))
    default_order = {j["journey_id"]: i + 1 for i, j in enumerate(default_ranked)}

    # Custom ranking
    for j in eligible:
        j["custom_score"] = round(_recompute_score(j, custom_weights), 4)
    custom_ranked = sorted(eligible, key=lambda j: -j["custom_score"])

    # Side-by-side comparison
    st.divider()
    st.subheader("Ranking Comparison")
    col_default, col_custom = st.columns(2)

    with col_default:
        st.markdown("**Default Weights**")
        for rank, j in enumerate(default_ranked[:10], start=1):
            st.markdown(f"`#{rank}` {j['journey_id']} — {j['journey_name'][:30]} ({j.get('composite_score', 0):.3f})")

    with col_custom:
        st.markdown("**Your Weights**")
        for rank, j in enumerate(custom_ranked[:10], start=1):
            default_rank = default_order.get(j["journey_id"], "N/A")
            delta = default_rank - rank if isinstance(default_rank, int) else 0
            delta_str = f" {'↑' if delta > 0 else '↓' if delta < 0 else '='}{abs(delta)}" if delta != 0 else ""
            st.markdown(f"`#{rank}`{delta_str} {j['journey_id']} — {j['journey_name'][:30]} ({j.get('custom_score', 0):.3f})")

    # ---- SCATTER ----
    st.divider()
    st.subheader("Custom Score Distribution")
    custom_df = pd.DataFrame(custom_ranked[:20])[[
        "journey_id", "journey_name", "custom_score", "composite_score",
        "escalation_rate", "cbp_capability_fit"
    ]].rename(columns={
        "custom_score": "Custom Score",
        "composite_score": "Default Score",
        "escalation_rate": "Leakage Rate",
        "cbp_capability_fit": "CBP Fit",
    })
    st.dataframe(custom_df, use_container_width=True, hide_index=True)

    # ---- SAVE PROFILE ----
    st.divider()
    profile_name = st.text_input("Save as scoring profile:", placeholder="e.g. 'Reddy-high-volume-focus'", key="profile_name")
    if st.button("💾 Save Scoring Profile", key="save_profile"):
        if profile_name:
            profile_path = OUTPUT_DIR / f"scoring_profile_{profile_name}.json"
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            with open(profile_path, "w") as f:
                json.dump({"name": profile_name, "weights": custom_weights}, f, indent=2)
            st.success(f"Saved to {profile_path.name}")
        else:
            st.error("Please enter a profile name.")


# ============================================================================
# SPOT-CHECK REVIEW QUEUE (sidebar menu item)
# ============================================================================

def view_spot_check():
    st.header("🔍 LLM Spot-Check Review Queue")
    st.caption(
        "Review LLM classifications manually to estimate accuracy. "
        "Results are stored in data/outputs/review_log.json."
    )

    from src.coding.review_queue import load_review_log, save_review_log, add_review, compute_accuracy

    log = load_review_log()
    accuracy = log.get("accuracy_by_journey", {})
    reviews = log.get("reviews", [])

    # ---- ACCURACY SUMMARY ----
    if accuracy:
        st.subheader("Per-Journey LLM Accuracy")
        acc_rows = []
        for jid, stats in accuracy.items():
            acc_rows.append({
                "Journey ID": jid,
                "Agree": stats["agree"],
                "Disagree": stats["disagree"],
                "Skip": stats["skip"],
                "Reviewed": stats["reviewed"],
                "LLM Precision": f"{stats['precision_pct']}%" if stats["precision_pct"] else "N/A",
                "Confidence": _confidence_badge(stats.get("confidence_tier", "LOW")),
            })
        st.dataframe(pd.DataFrame(acc_rows), use_container_width=True, hide_index=True)
        st.divider()

    # ---- MOCK REVIEW INTERFACE ----
    st.subheader("Review a Session")
    st.info(
        "In a live deployment, this shows actual LLM-coded sessions for your review. "
        "Below is a demonstration with synthetic data."
    )

    # Build a demo review item
    demo_session_id = "sess-demo-001"
    demo_item = {
        "session_id": demo_session_id,
        "journey_id": "ACCT_004",
        "transcript_excerpt": (
            "USER: I want to close my savings account\n"
            "BOT: There is a balance in your account. Account closure cannot be processed at this time.\n"
            "USER: My balance is zero, what does that mean?\n"
            "BOT: Connecting you to an agent..."
        ),
        "llm_journey_id": "ACCT_004",
        "llm_journey_name": "Account Closure Request",
        "llm_journey_confidence": 4,
        "llm_journey_reasoning": "Customer explicitly asked to close their savings account.",
        "llm_transfer_reason": "bot_has_data_communicates_poorly",
        "llm_transfer_confidence": 4,
        "llm_transfer_reasoning": "Bot had account data but gave generic response instead of explaining merchant hold.",
    }

    with st.container():
        st.markdown("**Transcript:**")
        st.code(demo_item["transcript_excerpt"], language=None)
        st.markdown(f"**LLM Journey:** `{demo_item['llm_journey_id']}` — {demo_item['llm_journey_name']} (confidence: {demo_item['llm_journey_confidence']}/5)")
        st.markdown(f"**LLM Transfer Reason:** `{demo_item['llm_transfer_reason']}` (confidence: {demo_item['llm_transfer_confidence']}/5)")
        st.markdown(f"**LLM Reasoning:** _{demo_item['llm_transfer_reasoning']}_")

        note = st.text_input("Note (optional):", key="review_note")
        col_agree, col_disagree, col_skip = st.columns(3)

        with col_agree:
            if st.button("✅ Agree", key="btn_agree"):
                add_review(log, demo_session_id, "ACCT_004", demo_item, "agree", note)
                save_review_log(log)
                st.success("Recorded: Agree")
                st.rerun()

        with col_disagree:
            if st.button("❌ Disagree", key="btn_disagree"):
                add_review(log, demo_session_id, "ACCT_004", demo_item, "disagree", note)
                save_review_log(log)
                st.warning("Recorded: Disagree")
                st.rerun()

        with col_skip:
            if st.button("⏭ Skip", key="btn_skip"):
                add_review(log, demo_session_id, "ACCT_004", demo_item, "skip", note)
                save_review_log(log)
                st.info("Recorded: Skip")
                st.rerun()

    st.divider()
    st.caption(f"Total reviews logged: {len(reviews)}")


# ============================================================================
# MAIN APP
# ============================================================================

def main():
    render_header()

    # Navigation
    view_options = [
        "Top Priority Journeys",
        "Per-Journey Deep Dive",
        "Bot vs. Agent Division of Labor",
        "Agent Knowledge Transfer",
        "What-If Scoring",
        "Spot-Check Review Queue",
    ]

    if "nav" not in st.session_state:
        st.session_state["nav"] = "Top Priority Journeys"

    # Apply pending navigation from drill buttons (must happen before radio renders)
    if "_pending_nav" in st.session_state:
        st.session_state["nav"] = st.session_state.pop("_pending_nav")

    selected_view = st.sidebar.radio(
        "Navigation",
        view_options,
        key="nav",
    )

    # Sidebar info
    st.sidebar.divider()
    st.sidebar.caption("**Data Source**")
    st.sidebar.caption("10,000 synthetic sessions | Nov 2025 – Apr 2026")
    st.sidebar.caption("Run pipeline to refresh data:")
    st.sidebar.code("python -m src.pipeline --source full --dry-run --yes")

    # Check data availability
    pipeline_ran = (OUTPUT_DIR / "scored_journeys.json").exists()
    if not pipeline_ran:
        st.sidebar.warning("⚠️ Pipeline output not found. Run pipeline first.")
    else:
        st.sidebar.success("✅ Pipeline output loaded")

    # Render selected view
    if selected_view == "Top Priority Journeys":
        view_top_journeys()
    elif selected_view == "Per-Journey Deep Dive":
        view_per_journey()
    elif selected_view == "Bot vs. Agent Division of Labor":
        view_division_of_labor()
    elif selected_view == "Agent Knowledge Transfer":
        view_knowledge_transfer()
    elif selected_view == "What-If Scoring":
        view_what_if()
    elif selected_view == "Spot-Check Review Queue":
        view_spot_check()


if __name__ == "__main__":
    main()
