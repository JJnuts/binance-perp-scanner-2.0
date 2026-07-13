"""Streamlit page renderers and tables."""

import numpy as np
import pandas as pd
import streamlit as st
from html import escape
from datetime import datetime

from .config import BTC_BUBBLE_TIMEFRAMES, CONFLUENCE_WEIGHTS, RESEARCH_DB_PATH, TERM_GUIDE_GROUPS
from .utils import _format_gex_billions, _format_human_count, _safe_float
from .data_spot import _spot_flow_summary, build_bitcoin_bubble_data
from .options_analytics import build_btc_options_cockpit
from .research import (
    confluence_component_ic,
    rank_ic_report,
    snapshot_counts,
    suggest_confluence_weights,
    trigger_event_study,
)
from .scoring import _build_best_setups
from .ui_charts import (
    _build_avwap_chart,
    _build_bitcoin_bubble_chart,
    _build_coinbase_premium_chart,
    _build_etf_tape_chart,
    _build_expiry_map_chart,
    _build_gex_strike_chart,
    _build_iv_curve_chart,
    _build_risk_reversal_chart,
    _build_spot_cvd_chart,
    _build_strike_expiry_heatmap,
    _regime_scatter,
    _scatter,
)
from .ui_jarvis import _render_institutional_positioning_summary, _render_jarvis_widget


def _render_gex_levels(levels: pd.DataFrame, title: str, field: str):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    if levels.empty:
        st.caption("No levels found in the current simple GEX window.")
        return
    rows = []
    for _, row in levels.iterrows():
        distance = _safe_float(row.get("distance_pct"))
        rows.append(
            f"""
            <div class="gex-level-item">
                <span class="gex-chip">{row['strike']:,.0f}</span>
                <span class="gex-chip">{field}: {_format_gex_billions(row[field])}</span>
                <span class="gex-chip">OI: {row['total_oi']:.2f} BTC</span>
                <span class="gex-chip">Distance: {distance:+.2f}%</span>
            </div>
            """
        )
    render(
        f"""
        <div class="gex-level-list">
            {''.join(rows)}
        </div>
        """
    )
def _render_options_snapshot_table(summary_rows: pd.DataFrame):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    body_rows = "".join(
        f"<tr><td>{escape(str(row['Metric']))}</td><td>{escape(str(row['Value']))}</td></tr>"
        for _, row in summary_rows.iterrows()
    )
    render(
        f"""
        <table class="options-snapshot-table">
            <thead>
                <tr><th>Metric</th><th>Value</th></tr>
            </thead>
            <tbody>
                {body_rows}
            </tbody>
        </table>
        """
    )
def _render_btc_options_cockpit(anchor_mode: str):
    status = st.empty()
    status.info("Building BTC options screener from public Deribit + Binance data...")
    try:
        bundle = build_btc_options_cockpit(anchor_mode)
    except Exception as exc:
        status.empty()
        st.error(f"BTC options screener failed to load: {exc}")
        st.caption("This page depends on live public Deribit and Binance endpoints. Try Force refresh in a moment.")
        return
    status.empty()
    if "error" in bundle:
        st.error(str(bundle["error"]))
        return

    spot = float(bundle["spot"])
    options_df = bundle["options_df"]
    gamma_flip = bundle["gamma_flip"]
    perp = bundle["perp_snapshot"]
    strike_map = bundle["strike_map"]
    strike_expiry_context = bundle.get("strike_expiry_context", strike_map)
    strike_expiry_breakdown = bundle.get("strike_expiry_breakdown", pd.DataFrame())
    expiry_map = bundle["expiry_map"]
    atm_iv = bundle["atm_iv"]
    avwap_df = bundle["avwap_df"]
    sweeps = bundle["sweeps"]
    support_levels = bundle["support_levels"]
    resistance_levels = bundle["resistance_levels"]
    ibit = bundle["ibit_context"]
    front_24h = bundle["front_24h"]
    front_7d = bundle["front_7d"]
    pin = bundle["pin"]
    iv_term = bundle["iv_term"]
    risk_reversal = bundle["risk_reversal"]
    pressure_forecast = bundle["pressure_forecast"]
    history_comparison = bundle["history_comparison"]
    block_flow_7d = bundle.get("block_flow_7d", {})
    block_flow_30d = bundle.get("block_flow_30d", {})
    block_store_update = bundle.get("block_store_update", {})
    block_disagreements = bundle.get("block_disagreements", pd.DataFrame())

    st.title("BTC Options Screener")
    st.caption(
        "Phase 1 public-data framework using Deribit BTC options and Binance BTCUSDT perpetuals. "
        "GEX here is a simple call-minus-put gamma approximation built from Deribit open interest and greeks."
    )

    st.markdown("### Institutional Positioning Summary")
    _render_institutional_positioning_summary(bundle)

    st.markdown("### IBIT Flow Context")
    if isinstance(ibit, dict) and "error" in ibit:
        st.caption(str(ibit["error"]))
    else:
        ib1, ib2, ib3, ib4, ib5 = st.columns(5)
        ib1.metric("IBIT Price", f"{_safe_float(ibit.get('price')):,.2f}")
        ib2.metric("Session Return", f"{_safe_float(ibit.get('session_return')):+.2%}")
        ib3.metric("Session Volume", _format_human_count(_safe_float(ibit.get("session_volume"))))
        ib4.metric("20D Avg Volume", _format_human_count(_safe_float(ibit.get("avg_20d_volume"))))
        ib5.metric("ETF Flow", str(ibit.get("flow_state", "Unavailable")))
        market_ts = ibit.get("market_ts")
        if isinstance(market_ts, pd.Timestamp):
            market_copy = market_ts.strftime("%Y-%m-%d %H:%M UTC")
        else:
            market_copy = "latest available session data"
        st.caption(
            f"IBIT is used here as a US-session spot-demand confirmation layer. Volume is running at "
            f"{_safe_float(ibit.get('volume_ratio')):.2f}x its 20-day average as of {market_copy}. {str(ibit.get('flow_copy', ''))}"
        )

    metric_help = {
        "BTC Spot": "Current BTC reference price used to anchor option strikes, moneyness, and distance calculations.",
        "OI 1H": "One-hour change in Binance BTCUSDT perpetual open interest; rising OI often means new leverage is entering.",
        "Gamma Flip": "Estimated BTC price where dealer gamma exposure changes sign, often shifting hedging from stabilizing to amplifying moves.",
        "Net GEX Approx": "Simple call-minus-put gamma exposure estimate across the visible Deribit option chain.",
        "Front 24H GEX": "Absolute gamma exposure in options expiring within the next 24 hours.",
        "Front 7D GEX": "Absolute gamma exposure in options expiring within the next seven days.",
        "Pin Score": "How strongly nearby option gamma may pull BTC toward a candidate strike into expiry.",
        "Funding 8H": "Latest eight-hour BTCUSDT perpetual funding rate; positive means longs pay shorts.",
        "ATM IV": "At-the-money implied volatility for the nearest liquid BTC options expiry.",
        "IV Term": "Front-expiry IV versus back-expiry IV; contango means back IV is higher, backwardation means front IV is higher.",
        "Front RR": "Front-expiry risk reversal, comparing call IV to put IV; positive favors calls, negative favors puts.",
        "Pressure Est.": "Estimated next hedging pressure bias from the current gamma and charm/vanna context.",
        "7D Block GEX Est.": "Seven-day block-trade-adjusted gamma estimate from matched Deribit block option legs.",
        "Block Legs": "Number of matched Deribit block option legs used in the seven-day block-flow estimate.",
        "RFQ Legs": "Matched block legs tagged as RFQ, weighted as higher-confidence institutional flow.",
        "30D Stored Blocks": "Number of distinct Deribit block trades currently stored in the local 30-day block-flow database.",
    }

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("BTC Spot", f"{spot:,.2f}", help=metric_help["BTC Spot"])
    c2.metric("OI 1H", f"{float(bundle['oi_change_1h']):+.2%}", help=metric_help["OI 1H"])
    c3.metric("Gamma Flip", f"{gamma_flip:,.0f}" if gamma_flip else "n/a", help=metric_help["Gamma Flip"])
    c4.metric("Net GEX Approx", _format_gex_billions(float(bundle["total_signed_gex"]), signed=True), help=metric_help["Net GEX Approx"])
    c5.metric("Front 24H GEX", _format_gex_billions(float(front_24h["abs_gex"])), help=metric_help["Front 24H GEX"])
    c6.metric("Front 7D GEX", _format_gex_billions(float(front_7d["abs_gex"])), help=metric_help["Front 7D GEX"])

    f1, f2, f3, f4, f5, f6 = st.columns(6)
    f1.metric("Pin Score", f"{_safe_float(pin.get('pin_score')):.0f}/100", help=metric_help["Pin Score"])
    f2.metric("Funding 8H", f"{_safe_float(perp.get('last_funding_rate')):.4%}", help=metric_help["Funding 8H"])
    f3.metric("ATM IV", f"{float(atm_iv['effective_iv'].iloc[0]):.1f}" if not atm_iv.empty else "n/a", help=metric_help["ATM IV"])
    f4.metric("IV Term", str(iv_term.get("term_regime", "n/a")), f"{_safe_float(iv_term.get('iv_ratio')):.2f}x", help=metric_help["IV Term"])
    f5.metric("Front RR", f"{_safe_float(risk_reversal['risk_reversal'].iloc[0]):+.2f}" if not risk_reversal.empty else "n/a", help=metric_help["Front RR"])
    f6.metric("Pressure Est.", str(pressure_forecast.get("pressure_bias", "Neutral")), help=metric_help["Pressure Est."])

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("7D Block GEX Est.", _format_gex_billions(_safe_float(block_flow_7d.get("total_block_gex")), signed=True), help=metric_help["7D Block GEX Est."])
    b2.metric("Block Legs", f"{int(block_flow_7d.get('matched_legs', 0)):,}", f"+{int(block_store_update.get('inserted', 0)):,} new", help=metric_help["Block Legs"])
    b3.metric("RFQ Legs", f"{int(block_flow_7d.get('rfq_trades', 0)):,}", help=metric_help["RFQ Legs"])
    b4.metric("30D Stored Blocks", f"{int(block_flow_30d.get('blocks', 0)):,}", help=metric_help["30D Stored Blocks"])

    st.caption(
        f"{str(pin.get('pin_copy', 'No pin candidate.'))} "
        f"{str(iv_term.get('term_copy', ''))} "
        f"{str(pressure_forecast.get('pressure_copy', ''))}"
    )
    st.caption(
        "Block-adjusted gamma is an estimate from Deribit block trades only. "
        "Direction is interpreted as customer/aggressor side, RFQ-tagged legs get higher confidence, and strikes without block flow remain map-only."
    )

    s1, s2 = st.columns(2)
    with s1:
        st.markdown("### Potential GEX Support")
        _render_gex_levels(support_levels, "Support", "put_gex")
    with s2:
        st.markdown("### Potential GEX Resistance")
        _render_gex_levels(resistance_levels, "Resistance", "call_gex")

    st.caption(
        "Support/resistance levels are ranked from strike-level put/call gamma concentration near spot. "
        "Treat them as probabilistic levels, not guaranteed barriers."
    )

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.plotly_chart(_build_gex_strike_chart(strike_expiry_context, spot), use_container_width=True)
    with chart_right:
        st.plotly_chart(_build_expiry_map_chart(expiry_map), use_container_width=True)

    st.markdown("### Strike Expiry Breakdown")
    if isinstance(strike_expiry_breakdown, pd.DataFrame) and not strike_expiry_breakdown.empty:
        display_breakdown = strike_expiry_breakdown[
            [
                "strike",
                "distance_pct",
                "total_gex_b",
                "call_gex_b",
                "put_gex_b",
                "net_gex_b",
                "dominant_expiry",
                "dominant_share",
                "top_call_expiry",
                "top_put_expiry",
                "front_24h_share",
                "front_7d_share",
                "total_oi",
            ]
        ].copy()
        for share_col in ["dominant_share", "front_24h_share", "front_7d_share"]:
            display_breakdown[share_col] = display_breakdown[share_col] * 100.0
        st.dataframe(
            display_breakdown,
            use_container_width=True,
            height=320,
            column_config={
                "strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                "distance_pct": st.column_config.NumberColumn("Distance", format="%.2f%%"),
                "total_gex_b": st.column_config.NumberColumn("Total GEX ($B)", format="%.2f"),
                "call_gex_b": st.column_config.NumberColumn("Call GEX ($B)", format="%.2f"),
                "put_gex_b": st.column_config.NumberColumn("Put GEX ($B)", format="%.2f"),
                "net_gex_b": st.column_config.NumberColumn("Net GEX ($B)", format="%+.2f"),
                "dominant_expiry": st.column_config.TextColumn("Dominant Expiry"),
                "dominant_share": st.column_config.NumberColumn("Dominant Share", format="%.0f%%"),
                "top_call_expiry": st.column_config.TextColumn("Top Call Expiry"),
                "top_put_expiry": st.column_config.TextColumn("Top Put Expiry"),
                "front_24h_share": st.column_config.NumberColumn("Front 24H", format="%.0f%%"),
                "front_7d_share": st.column_config.NumberColumn("Front 7D", format="%.0f%%"),
                "total_oi": st.column_config.NumberColumn("OI (BTC)", format="%.0f"),
            },
            hide_index=True,
        )
    else:
        st.caption("No strike-expiry breakdown is available for the current options snapshot.")

    st.plotly_chart(_build_strike_expiry_heatmap(options_df, spot), use_container_width=True)

    if isinstance(block_disagreements, pd.DataFrame) and not block_disagreements.empty:
        st.markdown("### Block Flow Disagreements")
        display_disagreements = block_disagreements[
            ["strike", "distance_pct", "signed_gex", "block_adjusted_gex", "block_abs_gex", "block_count", "rfq_trades"]
        ].copy()
        st.dataframe(
            display_disagreements,
            use_container_width=True,
            height=180,
            column_config={
                "strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                "distance_pct": st.column_config.NumberColumn("Distance", format="%.2f%%"),
                "signed_gex": st.column_config.NumberColumn("Raw Signed GEX", format="%.2f"),
                "block_adjusted_gex": st.column_config.NumberColumn("Block GEX Est.", format="%.2f"),
                "block_abs_gex": st.column_config.NumberColumn("Block Abs GEX", format="%.2f"),
                "block_count": st.column_config.NumberColumn("Blocks", format="%d"),
                "rfq_trades": st.column_config.NumberColumn("RFQ Legs", format="%d"),
            },
            hide_index=True,
        )

    curve_col, flow_col = st.columns(2)
    with curve_col:
        st.plotly_chart(_build_iv_curve_chart(atm_iv), use_container_width=True)
    with flow_col:
        summary_rows = pd.DataFrame(
            [
                {"Metric": "Total Signed GEX", "Value": _format_gex_billions(float(bundle["total_signed_gex"]), signed=True)},
                {"Metric": "Total Absolute GEX", "Value": _format_gex_billions(float(bundle["total_abs_gex"]))},
                {"Metric": "Call GEX", "Value": _format_gex_billions(float(bundle["call_gex"]))},
                {"Metric": "Put GEX", "Value": _format_gex_billions(float(bundle["put_gex"]))},
                {"Metric": "Top 5 Concentration", "Value": f"{float(bundle['top5_concentration']):.1%}"},
                {"Metric": "Perp Mark / Index", "Value": f"{_safe_float(perp.get('mark_price')):,.2f} / {_safe_float(perp.get('index_price')):,.2f}"},
                {"Metric": "Current OI Contracts", "Value": f"{_safe_float(perp.get('open_interest_contracts')):,.0f}"},
                {"Metric": "OI Value (latest)", "Value": f"{float(bundle['oi_latest_value']):,.0f}"},
                {"Metric": "7D Block GEX Estimate", "Value": _format_gex_billions(_safe_float(block_flow_7d.get("total_block_gex")), signed=True)},
                {"Metric": "7D Block Legs / Blocks", "Value": f"{int(block_flow_7d.get('matched_legs', 0)):,} / {int(block_flow_7d.get('blocks', 0)):,}"},
                {"Metric": "30D Stored Block Legs", "Value": f"{int(block_flow_30d.get('stored_trades', 0)):,}"},
            ]
        )
        st.markdown("### Options / Perp Snapshot")
        _render_options_snapshot_table(summary_rows)

    rr_col, hist_col = st.columns(2)
    with rr_col:
        st.plotly_chart(_build_risk_reversal_chart(risk_reversal), use_container_width=True)
    with hist_col:
        front_rows = pd.DataFrame(
            [
                {"Metric": "Front 24H Signed GEX", "Value": _format_gex_billions(float(front_24h["signed_gex"]), signed=True)},
                {"Metric": "Front 24H Top Strike", "Value": f"{_safe_float(front_24h.get('top_strike')):,.0f} ({_safe_float(front_24h.get('top_distance_pct')):+.2f}%)"},
                {"Metric": "Front 7D Signed GEX", "Value": _format_gex_billions(float(front_7d["signed_gex"]), signed=True)},
                {"Metric": "Front 7D Top Strike", "Value": f"{_safe_float(front_7d.get('top_strike')):,.0f} ({_safe_float(front_7d.get('top_distance_pct')):+.2f}%)"},
                {"Metric": "GEX vs History", "Value": f"{_safe_float(history_comparison.get('gex_vs_30d_median')):+.1%} vs rolling median"},
                {"Metric": "OI 24H Delta", "Value": f"{_safe_float(history_comparison.get('oi_24h_delta')):+,.0f} contracts"},
                {"Metric": "Front RR Z", "Value": f"{_safe_float(history_comparison.get('rr_zscore')):+.2f}"},
                {"Metric": "History Rows", "Value": f"{int(history_comparison.get('history_rows', 0)):,} snapshots"},
            ]
        )
        st.markdown("### Front-Week / History")
        _render_options_snapshot_table(front_rows)

    st.plotly_chart(_build_avwap_chart(avwap_df, bundle["anchor_ts"]), use_container_width=True)

    st.markdown("### Sweep Dashboard")
    if sweeps.empty:
        st.caption("No recent 5m sweep candidates were detected from the current price / wick / volume / OI rules.")
    else:
        sweep_df = sweeps.copy()
        sweep_df["ts"] = sweep_df["ts"].dt.strftime("%Y-%m-%d %H:%M")
        sweep_df["oi_value_change"] = sweep_df["oi_value_change"].map(lambda value: f"{float(value):+.2%}")
        st.dataframe(
            sweep_df.sort_values("ts", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "ts": st.column_config.TextColumn("Time"),
                "direction": st.column_config.TextColumn("Sweep Type"),
                "close": st.column_config.NumberColumn("Close", format="%.2f"),
                "volume_z": st.column_config.NumberColumn("Vol Z", format="%.2f"),
                "taker_imbalance": st.column_config.NumberColumn("Taker Imb", format="%.2f"),
                "oi_value_change": st.column_config.TextColumn("OI Change"),
                "broken_level": st.column_config.NumberColumn("Broken Level", format="%.2f"),
            },
            height=260,
        )

    _render_jarvis_widget(bundle, anchor_mode)
def _render_term_guide():
    st.subheader("Glossary / Term Guide")
    st.caption("Quick explanations for the score names and raw fields used in the screener table.")
    sections = []
    for idx, (group_name, items) in enumerate(TERM_GUIDE_GROUPS):
        cards = []
        for term, description in items:
            cards.append(
                f'<div class="term-guide-card"><div class="term-guide-title">{term}</div><div class="term-guide-copy">{description}</div></div>'
            )
        open_attr = " open" if idx == 0 else ""
        sections.append(
            f'<details class="term-guide-section"{open_attr}><summary class="term-guide-summary">{group_name}</summary><div class="term-guide-section-body"><div class="term-guide-grid">{"".join(cards)}</div></div></details>'
        )
    st.markdown("".join(sections), unsafe_allow_html=True)
def _render_research_dashboard():
    st.subheader("Research / Signal Quality")
    st.caption(
        "Every scan is logged locally so the scores can be judged against forward returns. "
        "Rank IC answers whether a high score actually ranked future winners; the event study "
        "answers whether fresh ignition triggers beat the cross-section."
    )
    counts = snapshot_counts()
    col_a, col_b = st.columns(2)
    for col, (table, info) in zip((col_a, col_b), counts.items()):
        with col:
            st.metric(table.replace("_", " ").title(), f"{info['rows']:,} rows")
            st.caption(f"{info['cross_sections']:,} cross-sections | {info['first']} .. {info['last']}")

    st.divider()
    st.markdown("**Rank IC** (Spearman, factor score vs forward return; positive = the score ranks winners)")
    ic = rank_ic_report()
    if ic.empty:
        st.info(
            "No measurable data yet. Leave the app running on the Altcoins page so snapshots "
            "accumulate - a day of uptime gives a few hundred cross-sections."
        )
    else:
        st.dataframe(
            ic,
            use_container_width=True,
            hide_index=True,
            column_config={
                "factor": st.column_config.TextColumn("Factor"),
                "horizon_h": st.column_config.NumberColumn("Horizon (h)", format="%.0f"),
                "mean_ic": st.column_config.NumberColumn("Mean IC", format="%.4f"),
                "ic_std": st.column_config.NumberColumn("IC Std", format="%.4f"),
                "t_stat": st.column_config.NumberColumn("t-stat", format="%.2f"),
                "cross_sections": st.column_config.NumberColumn("N", format="%d"),
            },
        )
        st.caption(
            "Rule of thumb: |t| >= 2 with a consistent sign is evidence; anything else is noise. "
            "A negative IC on a score you rank by means the score is actively hurting."
        )

    st.divider()
    st.markdown("**Ignition trigger event study** (direction-adjusted, excess vs same-scan cross-section)")
    ev = trigger_event_study()
    if ev.empty:
        st.info("No fresh triggers with forward snapshots yet.")
    else:
        st.dataframe(
            ev,
            use_container_width=True,
            hide_index=True,
            column_config={
                "horizon_h": st.column_config.NumberColumn("Horizon (h)", format="%.0f"),
                "triggers": st.column_config.NumberColumn("Triggers", format="%d"),
                "mean_signed_ret": st.column_config.NumberColumn("Mean Ret", format="%.4f"),
                "median_signed_ret": st.column_config.NumberColumn("Median Ret", format="%.4f"),
                "hit_rate": st.column_config.NumberColumn("Hit Rate", format="%.2f"),
                "mean_excess_ret": st.column_config.NumberColumn("Excess Ret", format="%.4f"),
                "excess_hit_rate": st.column_config.NumberColumn("Excess Hit", format="%.2f"),
            },
        )

    st.divider()
    st.markdown(
        "**Confluence component IC & weight calibration** "
        "(directional, veto-conditioned; measures each trigger confirmation on its own)"
    )
    comp_ic = confluence_component_ic()
    if comp_ic.empty:
        st.info(
            "No component data yet. Components are logged with each scan; they become "
            "measurable once veto-active snapshots have forward returns behind them."
        )
    else:
        st.dataframe(
            comp_ic,
            use_container_width=True,
            hide_index=True,
            column_config={
                "component": st.column_config.TextColumn("Component"),
                "horizon_h": st.column_config.NumberColumn("Horizon (h)", format="%.0f"),
                "mean_ic": st.column_config.NumberColumn("Mean IC", format="%.4f"),
                "ic_std": st.column_config.NumberColumn("IC Std", format="%.4f"),
                "t_stat": st.column_config.NumberColumn("t-stat", format="%.2f"),
                "cross_sections": st.column_config.NumberColumn("N", format="%d"),
            },
        )
        suggestion = suggest_confluence_weights(comp_ic)
        weights_df = pd.DataFrame(
            {
                "component": list(CONFLUENCE_WEIGHTS.keys()),
                "current": [float(CONFLUENCE_WEIGHTS[k]) for k in CONFLUENCE_WEIGHTS],
                "suggested": [suggestion["weights"].get(k, float(CONFLUENCE_WEIGHTS[k])) for k in CONFLUENCE_WEIGHTS],
            }
        )
        st.dataframe(
            weights_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "component": st.column_config.TextColumn("Component"),
                "current": st.column_config.NumberColumn("Current Weight", format="%.2f"),
                "suggested": st.column_config.NumberColumn("Suggested Weight", format="%.4f"),
            },
        )
        if suggestion["ready"]:
            st.caption(
                "Suggestion is advisory - nothing is applied automatically. To adopt it, review "
                "and paste into perpscanner/config.py: "
                f"`CONFLUENCE_WEIGHTS = {suggestion['weights']}`"
            )
        else:
            st.caption(f"No weight suggestion yet: {suggestion['reason']} (current weights shown unchanged).")
    st.caption(f"Store: {RESEARCH_DB_PATH}. CLI report: `py -m perpscanner.research` from the repo root.")
def _set_page(page_name: str):
    st.session_state["app_page"] = page_name
def _render_glossary_jump():
    st.button(
        "📖 Glossary / Term Guide",
        key="open_glossary_page",
        on_click=_set_page,
        args=("Glossary / Term Guide",),
        use_container_width=True,
    )
def _term_help(term: str) -> str:
    for _, items in TERM_GUIDE_GROUPS:
        for item_term, description in items:
            if item_term == term:
                return description
    fallback = {
        "Overall HTF Strength": "Single summary of the asset's higher-timeframe momentum profile. In this view it is the HTF Momentum score.",
        "Overall LTF Strength": "Single summary of the asset's lower-timeframe momentum profile. In this view it is the LTF Scalping score.",
        "Symbol": "Binance USDT-M perpetual contract symbol.",
    }
    return fallback.get(term, "")
def _format_percent_columns(table_df: pd.DataFrame, percent_cols: list[str]) -> pd.DataFrame:
    for col in percent_cols:
        if col in table_df:
            table_df[col] = table_df[col].map(lambda value: f"{float(value):+.2%}")
    return table_df
def _show_ltf_ignition_table(df: pd.DataFrame):
    if df.empty:
        st.info("No LTF ignition candidates match the current filters.")
        return

    cols = [
        "symbol",
        "ignition_state",
        "ignition_tf",
        "ltf_ignition_score",
        "conviction_net",
        "atr_percentile",
        "atr_roc",
        "atr_compression_score",
        "atr_expansion_score",
        "volume_zscore",
        "oi_zscore",
        "taker_imbalance",
        "cvd_3bar_slope",
        "basis_bp",
        "basis_delta_3bar_bp",
        "price_distance_from_vwap_atr",
        "breakout_distance_atr",
        "break_hold_confirmed",
        "rs_vs_btc",
        "rs_vs_eth",
        "compression_recent_bars",
        "bars_since_trigger",
        "tf_alignment_score",
        "ignition_score_5m",
        "ignition_score_15m",
        "ignition_score_1h",
    ]
    table_df = _format_percent_columns(df[cols].copy(), ["atr_roc", "rs_vs_btc", "rs_vs_eth"])
    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        hide_index=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "ignition_state": st.column_config.TextColumn("State"),
            "ignition_tf": st.column_config.TextColumn("TF"),
            "ltf_ignition_score": st.column_config.NumberColumn("Ignition", format="%.1f"),
            "conviction_net": st.column_config.NumberColumn("Conviction", format="%.2f"),
            "atr_percentile": st.column_config.NumberColumn("ATR %ile", format="%.1f"),
            "atr_roc": st.column_config.TextColumn("ATR ROC"),
            "atr_compression_score": st.column_config.NumberColumn("Compression", format="%.1f"),
            "atr_expansion_score": st.column_config.NumberColumn("Expansion", format="%.1f"),
            "volume_zscore": st.column_config.NumberColumn("Vol Z", format="%.2f"),
            "oi_zscore": st.column_config.NumberColumn("OI Z", format="%.2f"),
            "taker_imbalance": st.column_config.NumberColumn("Taker", format="%.2f"),
            "cvd_3bar_slope": st.column_config.NumberColumn("CVD 3", format="%.0f"),
            "basis_bp": st.column_config.NumberColumn("Basis bp", format="%.1f"),
            "basis_delta_3bar_bp": st.column_config.NumberColumn("Basis d3", format="%.1f"),
            "price_distance_from_vwap_atr": st.column_config.NumberColumn("VWAP Dist ATR", format="%.2f"),
            "breakout_distance_atr": st.column_config.NumberColumn("Breakout ATR", format="%.2f"),
            "break_hold_confirmed": st.column_config.CheckboxColumn("Hold"),
            "rs_vs_btc": st.column_config.TextColumn("RS BTC"),
            "rs_vs_eth": st.column_config.TextColumn("RS ETH"),
            "compression_recent_bars": st.column_config.NumberColumn("Comp Bars", format="%d"),
            "bars_since_trigger": st.column_config.NumberColumn("Age", format="%d"),
            "tf_alignment_score": st.column_config.NumberColumn("TF Align", format="%d"),
            "ignition_score_5m": st.column_config.NumberColumn("5m", format="%.1f"),
            "ignition_score_15m": st.column_config.NumberColumn("15m", format="%.1f"),
            "ignition_score_1h": st.column_config.NumberColumn("1h", format="%.1f"),
        },
    )
def _show_htf_expansion_table(df: pd.DataFrame):
    if df.empty:
        st.info("No HTF expansion candidates match the current filters.")
        return

    cols = [
        "symbol",
        "htf_expansion_direction",
        "htf_expansion_score",
        "htf_atr_percentile",
        "htf_atr_roc",
        "htf_atr_compression_score",
        "htf_atr_expansion_score",
        "htf_breakout_distance_atr",
        "htf_range_width_atr",
        "daily_structure_score",
        "daily_atr_percentile",
        "daily_volume_ratio",
        "daily_volume_persistence_days",
        "daily_oi_persistence_days",
        "daily_swing_high",
        "daily_swing_low",
        "daily_long_confirmed",
        "daily_short_confirmed",
        "btc_daily_regime",
        "btc_daily_regime_score",
        "htf_momentum_score",
        "htf_setup_score",
        "htf_relative_strength_score",
        "volume_score",
        "oi_score",
        "rs_24h",
        "rs_72h",
    ]
    table_df = _format_percent_columns(df[cols].copy(), ["htf_atr_roc", "rs_24h", "rs_72h"])
    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        hide_index=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "htf_expansion_direction": st.column_config.TextColumn("State"),
            "htf_expansion_score": st.column_config.NumberColumn("Expansion", format="%.1f"),
            "htf_atr_percentile": st.column_config.NumberColumn("ATR %ile", format="%.1f"),
            "htf_atr_roc": st.column_config.TextColumn("ATR ROC"),
            "htf_atr_compression_score": st.column_config.NumberColumn("Compression", format="%.1f"),
            "htf_atr_expansion_score": st.column_config.NumberColumn("ATR Expand", format="%.1f"),
            "htf_breakout_distance_atr": st.column_config.NumberColumn("Breakout ATR", format="%.2f"),
            "htf_range_width_atr": st.column_config.NumberColumn("Range ATR", format="%.2f"),
            "daily_structure_score": st.column_config.NumberColumn("Daily Struct", format="%.1f"),
            "daily_atr_percentile": st.column_config.NumberColumn("D ATR %ile", format="%.1f"),
            "daily_volume_ratio": st.column_config.NumberColumn("D Vol", format="%.2f"),
            "daily_volume_persistence_days": st.column_config.NumberColumn("Vol Days", format="%d"),
            "daily_oi_persistence_days": st.column_config.NumberColumn("OI Days", format="%d"),
            "daily_swing_high": st.column_config.NumberColumn("Swing High", format="%.4g"),
            "daily_swing_low": st.column_config.NumberColumn("Swing Low", format="%.4g"),
            "daily_long_confirmed": st.column_config.CheckboxColumn("D Long"),
            "daily_short_confirmed": st.column_config.CheckboxColumn("D Short"),
            "btc_daily_regime": st.column_config.TextColumn("BTC Regime"),
            "btc_daily_regime_score": st.column_config.NumberColumn("BTC Score", format="%.1f"),
            "htf_momentum_score": st.column_config.NumberColumn("HTF Momentum", format="%.1f"),
            "htf_setup_score": st.column_config.NumberColumn("HTF Setup", format="%.1f"),
            "htf_relative_strength_score": st.column_config.NumberColumn("HTF RS", format="%.1f"),
            "volume_score": st.column_config.NumberColumn("Vol Score", format="%.1f"),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f"),
            "rs_24h": st.column_config.TextColumn("RS 24H"),
            "rs_72h": st.column_config.TextColumn("RS 72H"),
        },
    )
def _show_best_setups_table(df: pd.DataFrame):
    if df.empty:
        st.info("No combined best setups match the current filters.")
        return

    cols = [
        "symbol",
        "best_setup_state",
        "best_setup_score",
        "ignition_state",
        "ignition_tf",
        "ltf_ignition_score",
        "htf_expansion_direction",
        "htf_expansion_score",
        "daily_structure_score",
        "daily_volume_persistence_days",
        "daily_oi_persistence_days",
        "btc_daily_regime",
        "alignment_score",
        "tf_alignment_score",
        "bars_since_trigger",
        "volume_zscore",
        "oi_zscore",
        "taker_imbalance",
        "cvd_3bar_slope",
        "basis_bp",
        "basis_delta_3bar_bp",
        "atr_percentile",
        "atr_roc",
        "breakout_distance_atr",
        "rs_vs_btc",
        "rs_vs_eth",
    ]
    table_df = _format_percent_columns(df[cols].copy(), ["atr_roc", "rs_vs_btc", "rs_vs_eth"])
    st.dataframe(
        table_df,
        use_container_width=True,
        height=460,
        hide_index=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "best_setup_state": st.column_config.TextColumn("Best Setup"),
            "best_setup_score": st.column_config.NumberColumn("Score", format="%.1f"),
            "ignition_state": st.column_config.TextColumn("LTF State"),
            "ignition_tf": st.column_config.TextColumn("TF"),
            "ltf_ignition_score": st.column_config.NumberColumn("LTF Ignition", format="%.1f"),
            "htf_expansion_direction": st.column_config.TextColumn("HTF State"),
            "htf_expansion_score": st.column_config.NumberColumn("HTF Expansion", format="%.1f"),
            "daily_structure_score": st.column_config.NumberColumn("Daily Struct", format="%.1f"),
            "daily_volume_persistence_days": st.column_config.NumberColumn("Vol Days", format="%d"),
            "daily_oi_persistence_days": st.column_config.NumberColumn("OI Days", format="%d"),
            "btc_daily_regime": st.column_config.TextColumn("BTC Regime"),
            "alignment_score": st.column_config.NumberColumn("Align", format="%.1f"),
            "tf_alignment_score": st.column_config.NumberColumn("TF Align", format="%d"),
            "bars_since_trigger": st.column_config.NumberColumn("Age", format="%d"),
            "volume_zscore": st.column_config.NumberColumn("Vol Z", format="%.2f"),
            "oi_zscore": st.column_config.NumberColumn("OI Z", format="%.2f"),
            "taker_imbalance": st.column_config.NumberColumn("Taker", format="%.2f"),
            "cvd_3bar_slope": st.column_config.NumberColumn("CVD 3", format="%.0f"),
            "basis_bp": st.column_config.NumberColumn("Basis bp", format="%.1f"),
            "basis_delta_3bar_bp": st.column_config.NumberColumn("Basis d3", format="%.1f"),
            "atr_percentile": st.column_config.NumberColumn("ATR %ile", format="%.1f"),
            "atr_roc": st.column_config.TextColumn("ATR ROC"),
            "breakout_distance_atr": st.column_config.NumberColumn("Breakout ATR", format="%.2f"),
            "rs_vs_btc": st.column_config.TextColumn("RS BTC"),
            "rs_vs_eth": st.column_config.TextColumn("RS ETH"),
        },
    )
def _render_classic_altcoin_dashboard(
    df: pd.DataFrame,
    scoring_mode: str,
    min_score: int,
    max_overextension_score: int,
    top_n: int,
    view: str,
):
    is_htf = scoring_mode == "HTF Momentum"
    if is_htf:
        momentum_col = "htf_momentum_score"
        setup_col = "htf_setup_score"
        score_label = "HTF Momentum"
        sort_cols = ["htf_setup_score", "htf_momentum_score"]
    else:
        momentum_col = "momentum_score"
        setup_col = "setup_score"
        score_label = "LTF Scalping"
        sort_cols = ["setup_score", "momentum_score"]

    setups = df[
        (df[momentum_col] >= float(min_score))
        & (df["overextension_score"] <= float(max_overextension_score))
    ].sort_values(sort_cols, ascending=False)
    ranked_df = df.sort_values([momentum_col, setup_col], ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alts Scanned", f"{len(df):,}")
    c2.metric("Qualified Setups", f"{len(setups):,}")
    c3.metric(f"Top {score_label}", f"{df[momentum_col].max():.1f}")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    if view == "Momentum vs Overextension":
        fig = _scatter(
            ranked_df,
            momentum_col,
            "overextension_score",
            "alpha_score" if not is_htf else "htf_alpha_score",
            f"{score_label} vs Overextension",
            f"{score_label} Score",
            "Overextension Score",
        )
    elif view == "RS 4H vs RS 24H":
        fig = _scatter(
            ranked_df,
            "rs_4h",
            "rs_24h",
            momentum_col,
            "Relative Strength vs BTC",
            "RS vs BTC (4H)",
            "RS vs BTC (24H)",
        )
    else:
        fig = _scatter(
            ranked_df,
            "volume_ratio_1h",
            "oi_change_1h",
            momentum_col,
            "Volume Expansion vs Open Interest Expansion",
            "1H Volume Ratio",
            "1H OI Change",
        )

    st.plotly_chart(fig, use_container_width=True)

    heading_col, glossary_col = st.columns([0.76, 0.24], vertical_alignment="bottom")
    with heading_col:
        st.subheader(f"Classic {score_label.lower()} dashboard ({len(setups)} assets)")
    with glossary_col:
        _render_glossary_jump()
    _show_table(setups.head(top_n), scoring_mode)

    with st.expander(f"Full classic screener ({len(df)} assets)"):
        _show_table(ranked_df, scoring_mode)
def _render_ltf_scalping_dashboard(
    df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    min_score: int,
    max_overextension_score: int,
    top_n: int,
    view: str,
):
    st.subheader("LTF Ignition")
    st.caption("Native 5m, 15m, and 1h ATR regime scan with fresh-trigger, taker/CVD, basis, and break-hold gates.")
    if ltf_df.empty:
        ignition_df = ltf_df
    else:
        ignition_df = ltf_df[
            (ltf_df["ltf_ignition_score"] >= float(min_score))
            & ltf_df["trigger_fresh"]
            & ltf_df["fresh_setup_pass"]
        ].sort_values(
            ["ltf_ignition_score", "tf_alignment_score", "volume_zscore", "oi_zscore"],
            ascending=False,
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LTF Assets Scanned", f"{len(ltf_df):,}")
    c2.metric("Ignition Candidates", f"{len(ignition_df):,}")
    c3.metric("Top Ignition", f"{ltf_df['ltf_ignition_score'].max():.1f}" if not ltf_df.empty else "0.0")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    if not ignition_df.empty:
        fig = _regime_scatter(
            ignition_df,
            "volume_zscore",
            "oi_zscore",
            "ltf_ignition_score",
            "LTF Ignition: Volume Spike vs OI Spike",
            "Volume Z-Score",
            "OI Z-Score",
        )
        st.plotly_chart(fig, use_container_width=True)
    _show_ltf_ignition_table(ignition_df.head(top_n))

    st.divider()
    _render_classic_altcoin_dashboard(df, "LTF Scalping", min_score, max_overextension_score, top_n, view)
def _render_htf_momentum_dashboard(
    df: pd.DataFrame,
    min_score: int,
    max_overextension_score: int,
    top_n: int,
    view: str,
):
    st.subheader("HTF Expansion")
    st.caption("Swing context: 1h expansion plus daily candle structure, pivot reclaim/rejection, BTC regime, OI persistence, and volume persistence.")
    expansion_df = df[df["htf_expansion_score"] >= float(min_score)].sort_values(
        ["htf_expansion_score", "daily_structure_score", "daily_oi_persistence_days"],
        ascending=False,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("HTF Assets Scanned", f"{len(df):,}")
    c2.metric("Expansion Candidates", f"{len(expansion_df):,}")
    c3.metric("Top Expansion", f"{df['htf_expansion_score'].max():.1f}")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    if not expansion_df.empty:
        fig = _regime_scatter(
            expansion_df,
            "htf_atr_percentile",
            "htf_atr_roc",
            "htf_expansion_score",
            "HTF Expansion: ATR Percentile vs ATR ROC",
            "ATR Percentile",
            "ATR ROC",
        )
        st.plotly_chart(fig, use_container_width=True)
    _show_htf_expansion_table(expansion_df.head(top_n))

    st.divider()
    _render_classic_altcoin_dashboard(df, "HTF Momentum", min_score, max_overextension_score, top_n, view)
def _render_best_setups_dashboard(
    df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    min_score: int,
    top_n: int,
):
    st.subheader("Best Setups")
    st.caption("Fresh LTF triggers only: 2-of-3 timeframe alignment, CVD/taker confirmation, basis confirmation, and HTF context.")
    best_df = _build_best_setups(ltf_df, df)
    if not best_df.empty:
        best_df = best_df[best_df["best_setup_score"] >= float(min_score)].sort_values("best_setup_score", ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Combined Assets", f"{len(best_df):,}")
    c2.metric("Long Setups", f"{best_df['best_setup_state'].str.startswith('Long').sum() if not best_df.empty else 0:,}")
    c3.metric("Short Setups", f"{best_df['best_setup_state'].str.startswith('Short').sum() if not best_df.empty else 0:,}")
    c4.metric("Top Best Setup", f"{best_df['best_setup_score'].max():.1f}" if not best_df.empty else "0.0")

    if not best_df.empty:
        fig = _regime_scatter(
            best_df,
            "ltf_ignition_score",
            "htf_expansion_score",
            "best_setup_score",
            "Best Setups: LTF Ignition vs HTF Expansion",
            "LTF Ignition Score",
            "HTF Expansion Score",
        )
        st.plotly_chart(fig, use_container_width=True)
    _show_best_setups_table(best_df.head(top_n))
def _show_table(df: pd.DataFrame, scoring_mode: str):
    cols = [
        "symbol"
    ]
    if df.empty:
        st.info("No assets match the current filters.")
        return

    if scoring_mode in {"HTF Leadership", "HTF Momentum"}:
        cols = [
            "symbol",
            "htf_setup_score",
            "htf_momentum_score",
            "vol_adjusted_score",
            "htf_trend_score",
            "oi_score",
            "funding_trend_quality_score",
            "htf_relative_strength_score",
            "rs_24h",
            "rs_72h",
            "htf_alpha_score",
            "alpha_24h",
            "alpha_72h",
            "overall_ltf_strength",
        ]
        percent_cols = ["rs_24h", "rs_72h", "alpha_24h", "alpha_72h"]
        table_df = df.copy()
        table_df["overall_ltf_strength"] = table_df["momentum_score"]
        column_config = {
            "symbol": st.column_config.TextColumn("Symbol", help=_term_help("Symbol")),
            "htf_setup_score": st.column_config.NumberColumn("HTF Setup", format="%.1f", help=_term_help("HTF Setup")),
            "htf_momentum_score": st.column_config.NumberColumn("HTF Momentum", format="%.1f", help=_term_help("HTF Momentum")),
            "vol_adjusted_score": st.column_config.NumberColumn("Vol-Adj", format="%.1f", help=_term_help("Vol-Adj")),
            "htf_trend_score": st.column_config.NumberColumn("HTF Trend", format="%.1f", help=_term_help("HTF Trend")),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f", help=_term_help("OI Score")),
            "funding_trend_quality_score": st.column_config.NumberColumn("Funding Trend", format="%.1f", help=_term_help("Funding Trend")),
            "htf_relative_strength_score": st.column_config.NumberColumn("HTF RS", format="%.1f", help=_term_help("HTF RS")),
            "rs_24h": st.column_config.TextColumn("RS 24H", help=_term_help("RS 24H")),
            "rs_72h": st.column_config.TextColumn("RS 72H", help=_term_help("RS 72H")),
            "htf_alpha_score": st.column_config.NumberColumn("HTF Alpha", format="%.1f", help=_term_help("HTF Alpha")),
            "alpha_24h": st.column_config.TextColumn("Alpha 24H", help=_term_help("Alpha 24H")),
            "alpha_72h": st.column_config.TextColumn("Alpha 72H", help=_term_help("Alpha 72H")),
            "overall_ltf_strength": st.column_config.NumberColumn("Overall LTF Strength", format="%.1f", help=_term_help("Overall LTF Strength")),
        }
    else:
        cols = [
            "symbol",
            "setup_score",
            "momentum_score",
            "volume_score",
            "trend_score",
            "volume_ratio_1h",
            "volume_z_1h",
            "overextension_score",
            "oi_change_1h",
            "oi_trend_raw",
            "oi_score",
            "premium_bp",
            "premium_roc_bp_h",
            "relative_strength_score",
            "rs_1h",
            "rs_4h",
            "rs_24h",
            "funding_quality_score",
            "alpha_score",
            "alpha_4h",
            "alpha_24h",
            "overall_htf_strength",
        ]
        percent_cols = ["rs_1h", "rs_4h", "rs_24h", "alpha_4h", "alpha_24h", "oi_change_1h", "oi_trend_raw"]
        table_df = df.copy()
        table_df["overall_htf_strength"] = table_df["htf_momentum_score"]
        column_config = {
            "symbol": st.column_config.TextColumn("Symbol", help=_term_help("Symbol")),
            "setup_score": st.column_config.NumberColumn("Scalp Setup", format="%.1f", help=_term_help("Setup")),
            "momentum_score": st.column_config.NumberColumn("LTF Scalping", format="%.1f", help=_term_help("Momentum")),
            "volume_score": st.column_config.NumberColumn("Vol Score", format="%.1f", help=_term_help("Vol Score")),
            "trend_score": st.column_config.NumberColumn("Trend Score", format="%.1f", help=_term_help("Trend Score")),
            "volume_ratio_1h": st.column_config.NumberColumn("Vol Ratio", format="%.2f", help=_term_help("Vol Ratio")),
            "volume_z_1h": st.column_config.NumberColumn("Vol Z", format="%.2f", help=_term_help("Vol Z")),
            "overextension_score": st.column_config.NumberColumn("Overext", format="%.1f", help=_term_help("Overext")),
            "oi_change_1h": st.column_config.TextColumn("OI 1H", help=_term_help("OI 1H")),
            "oi_trend_raw": st.column_config.TextColumn("OI Trend", help=_term_help("OI Trend")),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f", help=_term_help("OI Score")),
            "premium_bp": st.column_config.NumberColumn("Premium bp", format="%.1f", help=_term_help("Premium")),
            "premium_roc_bp_h": st.column_config.NumberColumn("Prem RoC", format="%.1f", help=_term_help("Premium RoC")),
            "relative_strength_score": st.column_config.NumberColumn("RS Score", format="%.1f", help=_term_help("RS Score")),
            "rs_1h": st.column_config.TextColumn("RS 1H", help=_term_help("RS 1H")),
            "rs_4h": st.column_config.TextColumn("RS 4H", help=_term_help("RS 4H")),
            "rs_24h": st.column_config.TextColumn("RS 24H", help=_term_help("RS 24H")),
            "funding_quality_score": st.column_config.NumberColumn("Funding Score", format="%.1f", help=_term_help("Funding Score")),
            "alpha_score": st.column_config.NumberColumn("Alpha Score", format="%.1f", help=_term_help("Alpha Score")),
            "alpha_4h": st.column_config.TextColumn("Alpha 4H", help=_term_help("Alpha 4H")),
            "alpha_24h": st.column_config.TextColumn("Alpha 24H", help=_term_help("Alpha 24H")),
            "overall_htf_strength": st.column_config.NumberColumn("Overall HTF Strength", format="%.1f", help=_term_help("Overall HTF Strength")),
        }

    table_df = table_df[cols].copy()
    for col in percent_cols:
        table_df[col] = table_df[col].map(lambda value: f"{value:.2%}")

    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        column_config=column_config,
        hide_index=True,
    )
def _render_bitcoin_section(bitcoin_mode: str, bubble_timeframe: str, bubble_lookback_days: int):
    config = BTC_BUBBLE_TIMEFRAMES[bubble_timeframe]
    progress_msg = st.empty()
    progress_msg.info(
        f"Building {str(config['label']).lower()} Bitcoin spot volume bubble map with auto-adjusted volume temperature..."
    )
    bubble_bundle = build_bitcoin_bubble_data(bubble_lookback_days, bubble_timeframe)
    progress_msg.empty()

    sources = bubble_bundle["sources"]
    aggregated = bubble_bundle["aggregated"]
    errors = bubble_bundle["errors"]
    aggregate_keys = bubble_bundle["aggregate_keys"]
    premium_df = bubble_bundle.get("coinbase_premium", pd.DataFrame())
    etf_tape = bubble_bundle.get("etf_tape", {})

    if bitcoin_mode == "Bitcoin Spot Vol (Binance)":
        source_key = "binance"
        title = "Bitcoin Spot Volume Bubble Map - Binance"
        df = sources.get(source_key, pd.DataFrame())
    elif bitcoin_mode == "Bitcoin Spot Vol (Coinbase)":
        source_key = "coinbase"
        title = "Bitcoin Spot Volume Bubble Map - Coinbase"
        df = sources.get(source_key, pd.DataFrame())
    else:
        source_key = "aggregated"
        title = "Bitcoin Spot Volume Bubble Map - Aggregated CEX"
        df = aggregated

    if df.empty:
        st.error("No Bitcoin spot bubble-map data was available for this source right now.")
        if errors:
            st.caption("Source errors: " + " | ".join(f"{key}: {value}" for key, value in errors.items()))
        st.stop()

    latest = df.iloc[-1]
    flow_summary = _spot_flow_summary(df, premium_df, etf_tape if isinstance(etf_tape, dict) else {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bars", f"{len(df):,}")
    c2.metric("Latest Price", f"{latest['close']:,.2f}")
    c3.metric("Spot Flow", str(flow_summary["state"]))
    c4.metric("Aggressor Imbalance", f"{_safe_float(latest.get('spot_imbalance')):+.1%}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Latest Spot Volume", f"{latest['quote_volume']:,.0f}")
    c6.metric("Volume State", str(latest["temperature"]))
    premium_latest = _safe_float(flow_summary.get("premium_latest"), np.nan)
    c7.metric("Coinbase Premium", "n/a" if not np.isfinite(premium_latest) else f"{premium_latest:+.1f} bp")
    etf_ratio = _safe_float(flow_summary.get("etf_proxy_ratio"), np.nan)
    c8.metric("ETF Tape Proxy", "n/a" if not np.isfinite(etf_ratio) else f"{etf_ratio:+.1%}")

    st.caption(
        f"Bubble size reflects {config['label']} total spot volume. When Binance taker flow is available, "
        "bubble color reflects aggressive buyer/seller imbalance: green = buyers lifting offers, red = sellers hitting bids."
    )
    st.caption(str(flow_summary["copy"]))
    if source_key == "aggregated":
        st.caption(
            "Aggregated view currently sums public spot data from: "
            + ", ".join(key.title() for key in aggregate_keys)
            + ". Aggressor color uses Binance taker-buy/taker-sell flow when present."
        )
    elif source_key in errors:
        st.caption(f"Fetch note for {source_key}: {errors[source_key]}")

    fig = _build_bitcoin_bubble_chart(df, title)
    st.plotly_chart(fig, use_container_width=True)

    if "spot_cvd" in df.columns and df["spot_cvd"].notna().any():
        st.plotly_chart(_build_spot_cvd_chart(df), use_container_width=True)
    else:
        st.info("Spot CVD is only available on Binance or aggregated views that include Binance taker-flow data.")

    if not premium_df.empty:
        st.plotly_chart(_build_coinbase_premium_chart(premium_df.tail(len(df))), use_container_width=True)
    else:
        st.info("Coinbase premium is unavailable right now because either Binance or Coinbase spot data did not load.")

    etf_df = etf_tape.get("data", pd.DataFrame()) if isinstance(etf_tape, dict) else pd.DataFrame()
    etf_error = etf_tape.get("error", "") if isinstance(etf_tape, dict) else ""
    if isinstance(etf_df, pd.DataFrame) and not etf_df.empty:
        st.plotly_chart(_build_etf_tape_chart(etf_df), use_container_width=True)
        st.caption(
            "ETF tape uses EODHD daily OHLCV for IBIT, FBTC, ARKB, and BITB. It is a signed-volume demand proxy, "
            "not official ETF creation/redemption net flow."
        )
    elif etf_error:
        st.info(etf_error)

    detail_cols = [
        "ts",
        "close",
        "quote_volume",
        "aggressive_buy_volume",
        "aggressive_sell_volume",
        "spot_imbalance",
        "spot_delta",
        "spot_cvd",
        "volume_z",
        "temperature",
        "flow_state",
    ]
    detail_df = df[[col for col in detail_cols if col in df.columns]].copy()
    ts_format = "%Y-%m-%d" if bubble_timeframe == "1D" else "%Y-%m-%d %H:%M"
    detail_df["ts"] = detail_df["ts"].dt.strftime(ts_format)
    st.dataframe(
        detail_df.sort_values("ts", ascending=False),
        use_container_width=True,
        height=360,
        column_config={
            "ts": st.column_config.TextColumn("Date"),
            "close": st.column_config.NumberColumn("BTC Price", format="%.2f"),
            "quote_volume": st.column_config.NumberColumn("Spot Volume (USD)", format="%.0f"),
            "aggressive_buy_volume": st.column_config.NumberColumn("Agg Buy Vol", format="%.0f"),
            "aggressive_sell_volume": st.column_config.NumberColumn("Agg Sell Vol", format="%.0f"),
            "spot_imbalance": st.column_config.NumberColumn("Agg Imbal", format="%.2f"),
            "spot_delta": st.column_config.NumberColumn("Spot Delta", format="%.0f"),
            "spot_cvd": st.column_config.NumberColumn("Spot CVD", format="%.0f"),
            "volume_z": st.column_config.NumberColumn("Volume Z", format="%.2f"),
            "temperature": st.column_config.TextColumn("State"),
            "flow_state": st.column_config.TextColumn("Flow"),
        },
        hide_index=True,
    )
