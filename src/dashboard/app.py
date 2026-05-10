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
    # Target card divs by their unique border-left style — Streamlit strips
    # class= attributes but preserves <style> tags and inline style= attributes.
    st.markdown(
        """
        <style>
        div[style*="border-left: 4px solid"] { color: #1a1a1a !important; }
        div[style*="border-left: 4px solid"] b,
        div[style*="border-left: 4px solid"] small { color: #1a1a1a !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header():
    _inject_css()
    is_syn = _is_synthetic()
    banner_color = "#FF4B4B" if is_syn else "#00CC88"
    banner_text = "⚠️  SYNTHETIC DATA — NOT REAL CUSTOMER DATA" if is_syn else "📊 PRODUCTION DATA"

    st.markdown(
        f"""
        <div style='background-color:{banner_color};padding:8px 16px;border-radius:6px;
                    color:white;font-weight:bold;font-size:14px;margin-bottom:12px;'>
            {banner_text}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.title("🏦 Mashreq CBP Transcript Analysis")
    st.caption("Conversational Banking Platform — MVP Journey Prioritization Tool")


# ============================================================================
# VIEW 1: TOP PRIORITY JOURNEYS
# ============================================================================

def view_top_journeys():
    st.header("1. Top Priority Journeys")
    st.caption("Ranked by composite score. Click any row to drill down (View 2).")

    mvp = load_mvp_shortlist()
    shortlist = mvp.get("mvp_shortlist", [])
    runners = mvp.get("runners_up", [])
    excluded = mvp.get("excluded_journeys", [])

    if not shortlist:
        st.warning("No scored journeys found. Run the pipeline first: `python -m src.pipeline --source full --dry-run --yes`")
        return

    # ---- SORT CONTROLS ----
    sort_by = st.radio(
        "Sort by:", ["Composite Score", "Total Volume", "Leakage Rate"],
        horizontal=True, key="v1_sort"
    )
    sort_map = {
        "Composite Score": ("composite_score", False),
        "Total Volume": ("total_sessions", False),
        "Leakage Rate": ("escalation_rate", False),
    }
    sort_col, sort_asc = sort_map[sort_by]

    # Build display dataframe
    all_journeys = shortlist + runners
    df = pd.DataFrame(all_journeys)
    if df.empty:
        st.warning("No data available.")
        return

    df = df.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)

    # ---- METRICS ROW ----
    col1, col2, col3, col4 = st.columns(4)
    leakage = load_leakage_map()
    summary = leakage.get("summary", {})
    with col1:
        st.metric("Total Sessions", f"{summary.get('total_sessions', 0):,}")
    with col2:
        st.metric("Total Escalations", f"{summary.get('total_escalated', 0):,}")
    with col3:
        rate = summary.get('overall_escalation_rate', 0)
        st.metric("Overall Escalation Rate", f"{rate:.1%}")
    with col4:
        st.metric("MVP Candidates", len(shortlist))

    st.divider()

    # ---- TOP 10 TABLE ----
    st.subheader("🏆 MVP Shortlist (Top 10)")

    for i, row in df.head(10).iterrows():
        rank = row.get("mvp_rank", i + 1)
        jid = row.get("journey_id", "")
        name = row.get("journey_name", jid)
        n = row.get("total_sessions", 0)
        e = row.get("escalated_sessions", 0)
        rate = row.get("escalation_rate", 0)
        score = row.get("composite_score", 0)
        rem = row.get("primary_remediation", "")
        conf = row.get("confidence", "MEDIUM")
        why = row.get("why_this_matters", "")
        cbp_fit = row.get("cbp_capability_fit", 0)

        # Color by MVP fit
        if cbp_fit >= 0.80 and score > 0.5:
            card_color = "#e6f4ea"  # green
        elif cbp_fit >= 0.50:
            card_color = "#fff8e1"  # amber
        else:
            card_color = "#fce8e6"  # red

        with st.container():
            st.markdown(
                f"""
                <div style='background:{card_color};border-radius:8px;padding:12px 16px;margin-bottom:8px;border-left:4px solid {"#34a853" if cbp_fit>=0.8 else "#fbbc04" if cbp_fit>=0.5 else "#ea4335"};'>
                <b style='color:#1a1a1a;'>#{rank} {name}</b>&nbsp;<small style='color:#1a1a1a;'>| {jid} | {_confidence_badge(conf)}</small><br>
                <small style='color:#1a1a1a;'>{e:,} escalations ({rate:.0%} rate) &nbsp;•&nbsp; Score: <b style='color:#1a1a1a;'>{score:.3f}</b> &nbsp;•&nbsp; {_remediation_color(rem)} {rem}</small><br>
                <small style='color:#444444;'>{why}</small>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if st.button(f"🔍 Drill into {jid}", key=f"drill_{jid}"):
                st.session_state["selected_journey"] = jid
                st.session_state["_pending_nav"] = "Per-Journey Deep Dive"
                st.rerun()

    # ---- RUNNERS UP ----
    if runners:
        with st.expander(f"🏃 Runners-Up ({len(runners)} journeys)"):
            runners_df = pd.DataFrame(runners)[["journey_id", "journey_name", "total_sessions", "escalated_sessions", "escalation_rate", "composite_score", "primary_remediation"]]
            runners_df.columns = ["ID", "Journey", "Sessions", "Escalations", "Rate", "Score", "Remediation"]
            runners_df["Rate"] = runners_df["Rate"].apply(lambda x: f"{x:.0%}")
            runners_df["Score"] = runners_df["Score"].apply(lambda x: f"{x:.3f}")
            st.dataframe(runners_df, use_container_width=True, hide_index=True)

    # ---- EXCLUDED ----
    if excluded:
        with st.expander(f"🚫 Excluded Journeys ({len(excluded)})"):
            excl_df = pd.DataFrame(excluded)[["journey_id", "journey_name", "exclusion_reason", "total_sessions"]]
            excl_df.columns = ["ID", "Journey", "Excluded Because", "Sessions"]
            st.dataframe(excl_df, use_container_width=True, hide_index=True)
            st.caption("Excluded journeys: regulatory_required_human, high_emotional_stakes, or product_advisory_complex.")

    # ---- EXPORT ----
    all_df = pd.DataFrame(shortlist + runners)
    if not all_df.empty:
        _export_button(all_df, "MVP Shortlist", "export_mvp")


# ============================================================================
# VIEW 2: PER-JOURNEY DEEP DIVE
# ============================================================================

def view_per_journey():
    st.header("2. Per-Journey Deep Dive")

    mvp = load_mvp_shortlist()
    all_journeys = mvp.get("mvp_shortlist", []) + mvp.get("runners_up", [])
    journey_names = {j["journey_id"]: j["journey_name"] for j in all_journeys}

    if not all_journeys:
        st.warning("No journey data. Run pipeline first.")
        return

    # Journey selector
    default_jid = st.session_state.get("selected_journey", all_journeys[0]["journey_id"] if all_journeys else "")
    journey_options = [f"{j['journey_id']} — {j['journey_name']}" for j in all_journeys]
    default_idx = next((i for i, j in enumerate(all_journeys) if j["journey_id"] == default_jid), 0)

    selected_label = st.selectbox("Select Journey", journey_options, index=default_idx, key="v2_journey")
    selected_jid = selected_label.split(" — ")[0]

    journey_data = next((j for j in all_journeys if j["journey_id"] == selected_jid), {})

    if not journey_data:
        st.warning("Journey data not found.")
        return

    # ---- HEADER METRICS ----
    st.subheader(f"{journey_data.get('journey_name', selected_jid)}")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("MVP Rank", f"#{journey_data.get('mvp_rank', 'N/A')}")
    with col2:
        st.metric("Total Sessions", f"{journey_data.get('total_sessions', 0):,}")
    with col3:
        st.metric("Escalations", f"{journey_data.get('escalated_sessions', 0):,}")
    with col4:
        st.metric("Escalation Rate", f"{journey_data.get('escalation_rate', 0):.1%}")
    with col5:
        st.metric("Avg CSAT", f"{journey_data.get('avg_csat', 0):.1f}/5.0")

    st.caption(
        f"Confidence: {_confidence_badge(journey_data.get('confidence', 'MEDIUM'))} | "
        f"Primary Remediation: {journey_data.get('primary_remediation', 'Unknown')} | "
        f"Composite Score: {journey_data.get('composite_score', 0):.3f}"
    )

    st.divider()

    # ---- TRANSFER REASON BREAKDOWN ----
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Failure Mode Breakdown")
        leakage = load_leakage_map()
        journey_leakage = next(
            (j for j in leakage.get("journeys", []) if j["journey_id"] == selected_jid), {}
        )
        tr_breakdown = journey_leakage.get("transfer_reason_breakdown", {})
        if tr_breakdown:
            tr_df = pd.DataFrame([
                {"Transfer Reason": k, "Count": v}
                for k, v in sorted(tr_breakdown.items(), key=lambda x: -x[1])
            ])
            st.bar_chart(tr_df.set_index("Transfer Reason"))
            with st.expander("Show raw data"):
                st.dataframe(tr_df, use_container_width=True, hide_index=True)
        else:
            st.info("No transfer reason breakdown available.")

    with col_right:
        st.subheader("Weekly Leakage Trend")
        trends = load_temporal_trends()
        journey_trend = next(
            (j for j in trends.get("journeys", []) if j["journey_id"] == selected_jid), {}
        )
        timeseries = journey_trend.get("timeseries", [])
        if timeseries:
            ts_df = pd.DataFrame(timeseries)
            ts_df = ts_df.rename(columns={"week": "Week", "rate_pct": "Escalation Rate %"})
            st.line_chart(ts_df.set_index("Week")[["Escalation Rate %"]])

            step_changes = journey_trend.get("step_changes", [])
            if step_changes:
                st.warning(f"⚠️ {len(step_changes)} step-change(s) detected in this journey's trend.")
                for sc in step_changes:
                    st.caption(f"  Week {sc['week']}: {sc['from_rate_pct']}% → {sc['to_rate_pct']}% ({sc['delta_pp']:+.1f}pp)")
        else:
            st.info("No temporal data available.")

    # ---- WHY THIS MATTERS ----
    st.divider()
    st.subheader("Why This Matters")
    st.info(journey_data.get("why_this_matters", "No summary available."))

    # ---- GENERATE WORD DOC ----
    st.divider()
    st.subheader("Generate Report")
    if st.button("📄 Generate Word Report for this Journey", key="gen_doc"):
        with st.spinner("Generating Word document..."):
            try:
                from src.reporting.per_journey_doc import JourneyDocBuilder
                builder = JourneyDocBuilder(journey_data)
                safe_name = journey_data.get("journey_name", selected_jid).replace(" ", "_").replace("/", "_")[:40]
                doc_path = OUTPUT_DIR / "journey_reports" / f"{selected_jid}_{safe_name}.docx"
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
