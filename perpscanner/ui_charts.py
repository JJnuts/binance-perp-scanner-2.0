"""Plotly figure builders."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .config import (
    APP_BG,
    APP_BORDER,
    APP_GRID,
    APP_MUTED,
    APP_PANEL,
    APP_TEXT,
    BUBBLE_SIZE_MAX,
    SPOT_COLOR_MAP,
    SPOT_FLOW_COLOR_MAP,
)
from .utils import _display_expiry_label, _safe_float


def _build_bitcoin_bubble_chart(df: pd.DataFrame, title: str):
    color_col = "flow_state" if "flow_state" in df.columns and (df["flow_state"] != "Unknown").any() else "temperature"
    color_map = SPOT_FLOW_COLOR_MAP if color_col == "flow_state" else SPOT_COLOR_MAP
    legend_title = "Spot Flow" if color_col == "flow_state" else "Volume State"
    fig = px.scatter(
        df,
        x="ts",
        y="close",
        size="bubble_size",
        size_max=int(BUBBLE_SIZE_MAX),
        color=color_col,
        color_discrete_map=color_map,
        hover_name="source",
        hover_data={
            "ts": "|%Y-%m-%d",
            "close": ":,.2f",
            "quote_volume": ":,.0f",
            "volume_z": ":.2f",
            "spot_imbalance": ":.2%",
            "spot_delta": ":,.0f",
            "bubble_size": False,
        },
        title=title,
        template="plotly_dark",
        height=700,
    )
    fig.update_traces(marker=dict(opacity=0.85, line=dict(width=0)))
    fig.update_layout(
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        legend_title=legend_title,
        title_font_size=17,
        xaxis_title="Date",
        yaxis_title="BTC Price (USD)",
        legend=dict(
            bgcolor="rgba(17, 24, 17, 0.0)",
            bordercolor="rgba(39, 50, 38, 0.0)",
            font=dict(color=APP_TEXT),
        ),
        margin=dict(l=30, r=20, t=60, b=30),
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_spot_cvd_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if "spot_cvd" in df.columns and df["spot_cvd"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=df["ts"],
                y=df["spot_cvd"],
                mode="lines",
                name="Spot CVD",
                line=dict(color="#86efac", width=2),
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=260,
        title="Spot CVD - Aggressive Buy Volume Minus Aggressive Sell Volume",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=55, b=25),
        xaxis_title="Date",
        yaxis_title="CVD (USD notional)",
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_coinbase_premium_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(
            go.Scatter(
                x=df["ts"],
                y=df["coinbase_premium_bp"],
                mode="lines",
                name="Coinbase Premium",
                line=dict(color="#93c5fd", width=2),
            )
        )
        fig.add_hline(y=0, line_color=APP_MUTED, line_dash="dot")
    fig.update_layout(
        template="plotly_dark",
        height=240,
        title="Coinbase Premium - Coinbase BTC/USD vs Binance BTC/USDT",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=55, b=25),
        xaxis_title="Date",
        yaxis_title="Premium (bp)",
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_etf_tape_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        colors = np.where(df["etf_signed_dollar_volume"] >= 0, "#22c55e", "#ef4444")
        fig.add_trace(
            go.Bar(
                x=df["date"],
                y=df["etf_signed_dollar_volume"],
                name="ETF Demand Proxy",
                marker_color=colors,
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=260,
        title="BTC ETF Tape Proxy - Signed Dollar Volume, Not Reported Net Flow",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=55, b=25),
        xaxis_title="Date",
        yaxis_title="Signed dollar volume",
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_gex_strike_chart(strike_map: pd.DataFrame, spot: float) -> go.Figure:
    nearby = strike_map[(strike_map["strike"] >= spot * 0.85) & (strike_map["strike"] <= spot * 1.15)].copy()
    if nearby.empty:
        nearby = strike_map.copy()
    nearby["call_gex_b"] = nearby["call_gex"] / 1000.0
    nearby["put_gex_b"] = nearby["put_gex"] / 1000.0
    if "block_adjusted_gex" in nearby.columns:
        nearby["block_adjusted_gex_b"] = nearby["block_adjusted_gex"] / 1000.0
    else:
        nearby["block_adjusted_gex_b"] = 0.0
    for col, default in {
        "call_expiry_hover": "Expiry detail unavailable",
        "put_expiry_hover": "Expiry detail unavailable",
        "dominant_expiry": "n/a",
        "dominant_expiry_share": 0.0,
        "front_24h_share": 0.0,
        "front_7d_share": 0.0,
    }.items():
        if col not in nearby.columns:
            nearby[col] = default
    customdata = nearby[
        [
            "call_expiry_hover",
            "put_expiry_hover",
            "front_24h_share",
            "front_7d_share",
            "dominant_expiry",
            "dominant_expiry_share",
            "call_gex_b",
            "put_gex_b",
        ]
    ].to_numpy()
    fig = go.Figure()
    fig.add_bar(
        name="Call GEX",
        x=nearby["strike"],
        y=nearby["call_gex_b"],
        marker_color="#9fab95",
        customdata=customdata,
        hovertemplate=(
            "Strike %{x:,.0f}<br>"
            "Call GEX %{customdata[6]:.2f}B<br>"
            "Top call expiries:<br>%{customdata[0]}<br>"
            "Dominant total expiry: %{customdata[4]} (%{customdata[5]:.0%})<br>"
            "Front 24h share: %{customdata[2]:.0%}<br>"
            "Front 7d share: %{customdata[3]:.0%}"
            "<extra>Call GEX</extra>"
        ),
    )
    fig.add_bar(
        name="Put GEX",
        x=nearby["strike"],
        y=-nearby["put_gex_b"],
        marker_color="#f472b6",
        customdata=customdata,
        hovertemplate=(
            "Strike %{x:,.0f}<br>"
            "Put GEX %{customdata[7]:.2f}B<br>"
            "Top put expiries:<br>%{customdata[1]}<br>"
            "Dominant total expiry: %{customdata[4]} (%{customdata[5]:.0%})<br>"
            "Front 24h share: %{customdata[2]:.0%}<br>"
            "Front 7d share: %{customdata[3]:.0%}"
            "<extra>Put GEX</extra>"
        ),
    )
    if nearby["block_adjusted_gex_b"].abs().sum() > 0:
        colors = np.where(
            nearby.get("block_agreement", pd.Series(index=nearby.index, data="No block read")).eq("Agrees"),
            "rgba(34, 197, 94, 0.62)",
            np.where(
                nearby.get("block_agreement", pd.Series(index=nearby.index, data="No block read")).eq("Disagrees"),
                "rgba(244, 211, 94, 0.72)",
                "rgba(148, 163, 184, 0.35)",
            ),
        )
        fig.add_bar(
            name="Block-Adjusted GEX Est.",
            x=nearby["strike"],
            y=nearby["block_adjusted_gex_b"],
            marker_color=colors,
            opacity=0.72,
            customdata=customdata,
            hovertemplate=(
                "Strike %{x:,.0f}<br>"
                "Block-adjusted est. %{y:.2f}B<br>"
                "Dominant total expiry: %{customdata[4]} (%{customdata[5]:.0%})<br>"
                "Front 24h share: %{customdata[2]:.0%}<br>"
                "Front 7d share: %{customdata[3]:.0%}"
                "<extra>Block-adjusted GEX</extra>"
            ),
        )
    fig.add_vline(x=spot, line_color="#e4eadf", line_dash="dash")
    fig.update_layout(
        barmode="relative",
        title="Strike Gamma Map + Block-Adjusted Gamma Overlay",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Strike",
        yaxis_title="Approx GEX ($B)",
        legend=dict(orientation="h"),
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=True, zerolinecolor=APP_BORDER, linecolor=APP_BORDER)
    return fig
def _build_expiry_map_chart(expiry_map: pd.DataFrame) -> go.Figure:
    chart_df = expiry_map.copy()
    chart_df["abs_gex_b"] = chart_df["abs_gex"] / 1000.0
    fig = go.Figure()
    fig.add_bar(x=chart_df["expiry_label"], y=chart_df["abs_gex_b"], name="Abs GEX ($B)", marker_color="#dfe7d8")
    fig.add_scatter(x=chart_df["expiry_label"], y=chart_df["total_oi"], name="OI (BTC)", mode="lines+markers", yaxis="y2", line=dict(color="#3b82f6"))
    fig.update_layout(
        title="Expiry Map",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=30, t=60, b=30),
        xaxis_title="Expiry",
        yaxis_title="Abs GEX ($B)",
        yaxis2=dict(title="OI (BTC)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h"),
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_strike_expiry_heatmap(options_df: pd.DataFrame, spot: float) -> go.Figure:
    fig = go.Figure()
    if options_df.empty:
        fig.update_layout(title="Strike x Expiry Gamma Heatmap")
        return fig

    nearby = options_df[(options_df["strike"] >= spot * 0.85) & (options_df["strike"] <= spot * 1.15)].copy()
    if nearby.empty:
        nearby = options_df.copy()
    grouped = (
        nearby.groupby(["expiration_ts", "expiry_label", "strike"], as_index=False)
        .agg(
            call_gex=("call_gex", "sum"),
            put_gex=("put_gex", "sum"),
            signed_gex=("signed_gex", "sum"),
            abs_gex=("gex_abs", "sum"),
            total_oi=("open_interest", "sum"),
            hours_to_expiry=("hours_to_expiry", "min"),
        )
        .sort_values(["expiration_ts", "strike"])
    )
    if grouped.empty:
        fig.update_layout(title="Strike x Expiry Gamma Heatmap")
        return fig

    grouped["expiry_display"] = grouped.apply(
        lambda row: _display_expiry_label(row.get("expiry_label"), _safe_float(row.get("hours_to_expiry"))),
        axis=1,
    )
    grouped["net_gex_b"] = grouped["signed_gex"] / 1000.0
    grouped["call_gex_b"] = grouped["call_gex"] / 1000.0
    grouped["put_gex_b"] = grouped["put_gex"] / 1000.0
    grouped["hover_text"] = grouped.apply(
        lambda row: (
            f"Expiry {row['expiry_display']}<br>"
            f"Strike {row['strike']:,.0f}<br>"
            f"Net GEX {row['net_gex_b']:+.2f}B<br>"
            f"Call GEX {row['call_gex_b']:.2f}B<br>"
            f"Put GEX {row['put_gex_b']:.2f}B<br>"
            f"OI {row['total_oi']:,.0f} BTC"
        ),
        axis=1,
    )

    x_values = sorted(grouped["strike"].unique())
    y_order = (
        grouped[["expiration_ts", "expiry_display"]]
        .drop_duplicates()
        .sort_values("expiration_ts")["expiry_display"]
        .tolist()
    )
    z = grouped.pivot(index="expiry_display", columns="strike", values="net_gex_b").reindex(index=y_order, columns=x_values)
    hover = grouped.pivot(index="expiry_display", columns="strike", values="hover_text").reindex(index=y_order, columns=x_values)
    fig.add_heatmap(
        x=x_values,
        y=y_order,
        z=z.to_numpy(),
        text=hover.to_numpy(),
        hovertemplate="%{text}<extra></extra>",
        colorscale=[
            [0.0, "#f472b6"],
            [0.48, "#253021"],
            [0.5, "#111811"],
            [0.52, "#2d3b2b"],
            [1.0, "#9fab95"],
        ],
        zmid=0.0,
        colorbar=dict(title="Net GEX ($B)"),
        xgap=1,
        ygap=1,
        hoverongaps=False,
    )
    fig.add_vline(x=spot, line_color="#e4eadf", line_dash="dash")
    fig.update_layout(
        title="Strike x Expiry Gamma Heatmap",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=30, t=60, b=35),
        xaxis_title="Strike",
        yaxis_title="Expiry",
        height=380,
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=False, linecolor=APP_BORDER)
    return fig
def _build_iv_curve_chart(atm_iv: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=atm_iv["expiry_label"], y=atm_iv["effective_iv"], mode="lines+markers", line=dict(color="#e4eadf"))
    fig.update_layout(
        title="ATM IV by Expiry",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Expiry",
        yaxis_title="ATM IV",
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_risk_reversal_chart(risk_reversal: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not risk_reversal.empty:
        fig.add_scatter(
            x=risk_reversal["expiry_label"],
            y=risk_reversal["risk_reversal"],
            mode="lines+markers",
            line=dict(color="#f4d35e"),
            name="25D Call IV - Put IV",
        )
        fig.add_hline(y=0.0, line_dash="dot", line_color="#8f9a8b")
    fig.update_layout(
        title="25D Risk Reversal by Expiry",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Expiry",
        yaxis_title="RR (IV pts)",
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _build_avwap_chart(avwap_df: pd.DataFrame, anchor_ts: pd.Timestamp) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=avwap_df["ts"],
            open=avwap_df["open"],
            high=avwap_df["high"],
            low=avwap_df["low"],
            close=avwap_df["close"],
            name="BTCUSDT Perp",
            increasing_line_color="#dfe7d8",
            decreasing_line_color="#8f9a8b",
            showlegend=False,
        )
    )
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["avwap"], mode="lines", name="Anchored VWAP", line=dict(color="#3b82f6", width=2))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_1_up"], mode="lines", name="+1sigma", line=dict(color="#59705a", dash="dot"))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_1_dn"], mode="lines", name="-1sigma", line=dict(color="#59705a", dash="dot"))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_2_up"], mode="lines", name="+2sigma", line=dict(color="#f472b6", dash="dash"))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_2_dn"], mode="lines", name="-2sigma", line=dict(color="#f472b6", dash="dash"))
    fig.add_vline(x=anchor_ts, line_color="#e4eadf", line_dash="dot")
    fig.update_layout(
        title=f"Anchored VWAP Dashboard ({anchor_ts.strftime('%Y-%m-%d %H:%M UTC')})",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Time",
        yaxis_title="BTCUSDT Perp",
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _scatter(df: pd.DataFrame, x: str, y: str, color: str, title: str, x_label: str, y_label: str):
    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=color,
        hover_name="symbol",
        hover_data={
            "momentum_score": ":.1f",
            "htf_momentum_score": ":.1f",
            "overextension_score": ":.1f",
            "alpha_score": ":.1f",
            "htf_alpha_score": ":.1f",
            "vol_adjusted_score": ":.1f",
            "relative_strength_score": ":.1f",
            "htf_relative_strength_score": ":.1f",
            "volume_score": ":.1f",
            "trend_score": ":.1f",
            "htf_trend_score": ":.1f",
            "oi_score": ":.1f",
            "funding_quality_score": ":.1f",
            "funding_trend_quality_score": ":.1f",
            "rs_1h": ":.2%",
            "rs_4h": ":.2%",
            "rs_24h": ":.2%",
            "alpha_4h": ":.2%",
            "alpha_24h": ":.2%",
            "alpha_72h": ":.2%",
            "vol_adj_24h": ":.2f",
            "vol_adj_72h": ":.2f",
            "volume_ratio_1h": ":.2f",
            "volume_z_1h": ":.2f",
            "oi_change_1h": ":.2%",
            "funding_rate": ":.5f",
            "funding_cumulative_7d": ":.5f",
            "funding_trend": ":.5f",
        },
        labels={x: x_label, y: y_label, color: color.replace("_", " ").title()},
        title=title,
        color_continuous_scale=[
            [0.0, "#293528"],
            [0.35, "#59705a"],
            [0.7, "#9fab95"],
            [1.0, "#e4eadf"],
        ],
        template="plotly_dark",
        height=650,
    )
    fig.update_traces(marker=dict(size=8, opacity=0.86))
    fig.update_layout(
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        title_font_size=17,
        coloraxis_colorbar_title=color.replace("_", " ").title(),
        margin=dict(l=30, r=20, t=60, b=30),
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
def _regime_scatter(df: pd.DataFrame, x: str, y: str, color: str, title: str, x_label: str, y_label: str):
    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=color,
        hover_name="symbol",
        hover_data={col: True for col in df.columns if col in {
            "ignition_state",
            "ignition_tf",
            "ltf_ignition_score",
            "htf_expansion_direction",
            "htf_expansion_score",
            "best_setup_score",
            "atr_percentile",
            "atr_roc",
            "volume_zscore",
            "oi_zscore",
            "taker_imbalance",
            "cvd_3bar_slope",
            "basis_bp",
            "basis_delta_3bar_bp",
            "bars_since_trigger",
            "tf_alignment_score",
            "breakout_distance_atr",
            "rs_vs_btc",
            "rs_vs_eth",
        }},
        labels={x: x_label, y: y_label, color: color.replace("_", " ").title()},
        title=title,
        color_continuous_scale=[
            [0.0, "#293528"],
            [0.35, "#59705a"],
            [0.7, "#9fab95"],
            [1.0, "#e4eadf"],
        ],
        template="plotly_dark",
        height=520,
    )
    fig.update_traces(marker=dict(size=8, opacity=0.86))
    fig.update_layout(
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        title_font_size=17,
        margin=dict(l=30, r=20, t=60, b=30),
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig
