"""Streamlit entry point."""

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from .config import REFRESH_MS, WS_LTF_ENABLED
from .data_binance import get_usdt_perpetuals
from .research import log_scan_snapshot
from .scoring import build_ltf_regime_metrics, build_metrics
from .ui_styles import _inject_app_styles
from .ui_pages import (
    _render_best_setups_dashboard,
    _render_bitcoin_section,
    _render_btc_options_cockpit,
    _render_htf_momentum_dashboard,
    _render_ltf_scalping_dashboard,
    _render_research_dashboard,
    _render_term_guide,
)


def main():
    st.set_page_config(
        page_title="Binance Perp Scanner 2.0",
        page_icon=":satellite:",
        layout="wide",
    )
    _inject_app_styles()

    st_autorefresh(interval=REFRESH_MS, key="scanner_refresh")

    if "app_page" not in st.session_state:
        st.session_state["app_page"] = "Altcoins"
    elif st.session_state["app_page"] == "BTC Options Cockpit":
        st.session_state["app_page"] = "BTC Options Screener"

    page = st.session_state["app_page"]
    options_anchor_mode = "Weekly Open"
    bitcoin_mode = "Bitcoin Spot Vol (Binance)"
    bubble_timeframe = "1D"
    bubble_lookback_days = 365
    altcoin_views = ["LTF Scalping", "HTF Momentum", "Best Setups"]
    if "altcoin_screener_mode" not in st.session_state:
        st.session_state["altcoin_screener_mode"] = "LTF Scalping"

    def _open_altcoin_screener():
        st.session_state["app_page"] = "Altcoins"

    with st.sidebar:
        if st.button("Force refresh"):
            st.cache_data.clear()
            st.rerun()

        if st.button("📖 Glossary / Term Guide", use_container_width=True):
            st.session_state["app_page"] = "Glossary / Term Guide"
            st.rerun()

        if st.button("🔬 Research / Signal Quality", use_container_width=True):
            st.session_state["app_page"] = "Research"
            st.rerun()

        st.divider()

        with st.expander("Altcoins", expanded=page == "Altcoins"):
            altcoin_screener_mode = st.radio(
                "Altcoin Screener",
                altcoin_views,
                index=altcoin_views.index(st.session_state["altcoin_screener_mode"])
                if st.session_state["altcoin_screener_mode"] in altcoin_views
                else 0,
                key="altcoin_screener_mode",
                on_change=_open_altcoin_screener,
                label_visibility="collapsed",
            )

        with st.expander("Bitcoin", expanded=page in {"BITCOIN", "BTC Options Screener"}):
            if st.button("Bitcoin Spot Volume Bubblemap", key="page_bitcoin_bubble", use_container_width=True):
                st.session_state["app_page"] = "BITCOIN"
                st.rerun()
            if st.button("BTC Options Screener", key="page_btc_options", use_container_width=True):
                st.session_state["app_page"] = "BTC Options Screener"
                st.rerun()

        page = st.session_state["app_page"]

        if page == "Glossary / Term Guide":
            st.divider()
            st.caption("Reference page for all screener terms and score labels.")
        elif page == "Research":
            st.divider()
            st.caption("Signal-quality report built from logged scan snapshots.")
        elif page == "BTC Options Screener":
            st.subheader("BTC Options")
            options_anchor_mode = st.radio(
                "Anchored VWAP",
                ["Weekly Open", "Monthly Open", "Prior 24H High", "Prior 24H Low"],
                index=0,
            )
            st.caption("Phase 1 uses public Deribit BTC options data plus Binance BTCUSDT perpetual context.")
        elif page == "BITCOIN":
            st.subheader("BITCOIN")
            bitcoin_mode = st.radio(
                "Bitcoin Spot Volume Bubblemap",
                [
                    "Bitcoin Spot Vol (Binance)",
                    "Bitcoin Spot Vol (Coinbase)",
                    "Bitcoin Spot Vol (Aggregated)",
                ],
                index=0,
            )
            bubble_timeframe = st.radio("Timeframe", ["1D", "12H", "8H"], index=0, horizontal=True)
            bubble_lookback_days = st.slider("Days to show", 90, 1000, 365, 30)
            st.caption("Aggregated view uses public spot data from Binance, Coinbase, Bybit, OKX, and Kraken when available.")
        else:
            st.subheader("Liquidity gates")
            min_quote_volume = st.number_input(
                "Min 24H quote volume (USDT)",
                min_value=0.0,
                value=10_000_000.0,
                step=1_000_000.0,
                format="%.0f",
            )
            min_trades = st.number_input(
                "Min 24H trades",
                min_value=0.0,
                value=15_000.0,
                step=1_000.0,
                format="%.0f",
            )
            min_oi_value = st.number_input(
                "Min open interest value",
                min_value=0.0,
                value=5_000_000.0,
                step=500_000.0,
                format="%.0f",
            )
            st.caption("Balanced defaults: 10M quote volume, 15k trades, 5M open interest.")

            st.divider()
            st.subheader("Dashboard filters")
            min_dashboard_score = st.slider("Min dashboard score", 0, 100, 70, 1)
            max_overextension_score = st.slider("Max overextension score", 0, 100, 65, 1)
            top_n = st.slider("Rows to show", 10, 100, 30, 5)

            st.divider()
            st.subheader("View mode")
            view = st.radio(
                "Chart",
                [
                    "Momentum vs Overextension",
                    "RS 4H vs RS 24H",
                    "Volume vs OI Expansion",
                ],
                index=0,
            )

            st.divider()
            use_ws_feed = st.toggle(
                "Websocket LTF feed",
                value=WS_LTF_ENABLED,
                help=(
                    "Stream closed 5m/15m/1h bars over websocket instead of re-polling "
                    "REST every scan. Falls back to REST automatically when a buffer is "
                    "cold or stale. Off by default: some networks (including the one this "
                    "was built on) never receive fstream.binance.com frames even though "
                    "REST works - enable it on a VPS/network where fstream delivers."
                ),
            )
            st.caption("Universe excludes BTCUSDT by design.")
            st.caption("Data: Binance Futures public market data endpoints.")

    if page == "Glossary / Term Guide":
        st.title("Binance Perp Scanner 2.0")
        _render_term_guide()
        return

    if page == "Research":
        st.title("Binance Perp Scanner 2.0")
        _render_research_dashboard()
        return

    if page == "BTC Options Screener":
        _render_btc_options_cockpit(options_anchor_mode)
        return

    if page == "BITCOIN":
        st.title("Binance Perp Scanner 2.0")
        st.caption(
            "Altcoin momentum screener for Binance USDT-M perps using BTC-relative strength, "
            "volume expansion, EMA/VWAP trend, open interest, and funding quality."
        )
        _render_bitcoin_section(bitcoin_mode, bubble_timeframe, bubble_lookback_days)
        return

    st.title("Binance Perp Scanner 2.0")
    st.caption(
        "Altcoin momentum screener for Binance USDT-M perps using BTC-relative strength, "
        "volume expansion, EMA/VWAP trend, open interest, and funding quality."
    )

    with st.spinner("Fetching active Binance perpetuals..."):
        try:
            symbols = tuple(get_usdt_perpetuals())
        except Exception as e:
            st.error(f"Could not connect to Binance: {e}")
            st.stop()

    progress_msg = st.empty()
    progress_msg.info("Building core 1H momentum model for Binance altcoin perps...")
    df = build_metrics(symbols, min_quote_volume, min_trades, min_oi_value)

    if df.empty:
        progress_msg.empty()
        st.error("No altcoins matched the current gates. Lower the liquidity thresholds and try again.")
        st.stop()

    ltf_df = pd.DataFrame()
    if altcoin_screener_mode in {"LTF Scalping", "Best Setups"}:
        progress_msg.info("Building accurate native LTF ATR ignition model - this fetches 5m, 15m, and 1h data...")
        ltf_df = build_ltf_regime_metrics(symbols, min_quote_volume, min_trades, min_oi_value, use_ws=use_ws_feed)
    progress_msg.empty()

    # Research loop: persist the scored scan so forward returns can be
    # measured later (rank IC / trigger event study). Never breaks the app.
    log_scan_snapshot(df, ltf_df)

    if altcoin_screener_mode == "LTF Scalping":
        _render_ltf_scalping_dashboard(
            df,
            ltf_df,
            min_dashboard_score,
            max_overextension_score,
            top_n,
            view,
        )
    elif altcoin_screener_mode == "HTF Momentum":
        _render_htf_momentum_dashboard(
            df,
            min_dashboard_score,
            max_overextension_score,
            top_n,
            view,
        )
    else:
        _render_best_setups_dashboard(
            df,
            ltf_df,
            min_dashboard_score,
            top_n,
        )
