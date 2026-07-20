"""GEX, pins, RR, IV term, block-flow gamma, cockpit bundle."""

import numpy as np
import pandas as pd
import streamlit as st
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import (
    BLOCK_FLOW_PRIMARY_DAYS,
    BLOCK_FLOW_RETENTION_DAYS,
    BLOCK_RFQ_WEIGHT,
    BTC_OPTIONS_HISTORY_PATH,
    BTC_OPTIONS_MAX_CONTRACTS,
    BTC_OPTIONS_MAX_DAYS,
    CACHE_TTL,
    FRONT_DAY_HOURS,
    FRONT_WEEK_DAYS,
    MAX_WORKERS,
    PIN_DISTANCE_PCT,
    PIN_MAX_HOURS,
)
from .net import _get_deribit
from .utils import _display_expiry_label, _safe_float, _utc_now_naive
from .data_binance import (
    _fetch_binance_btc_open_interest_hist,
    _fetch_binance_btc_perp_klines,
    _fetch_binance_btc_perp_snapshot,
)
from .data_equities import _build_ibit_context
from .data_deribit import (
    _read_deribit_block_trades,
    _safe_fetch_deribit_option_ticker,
    _update_deribit_block_trade_store,
)


def _avwap_fetch_plan(anchor_mode: str) -> tuple[str, int]:
    """Kline interval/limit that can actually reach the requested anchor.

    The old fixed 576x5m fetch held 48 hours - a weekly open was
    unreachable from Wednesday onward and a monthly open almost always,
    so those anchor modes silently degraded to a rolling-window anchor.
    """
    if anchor_mode == "Monthly Open":
        return "15m", 3100  # ~32 days of 15m bars
    return "5m", 2400  # ~8.3 days: covers the weekly open + prior-24h modes
def _choose_anchor_timestamp(df: pd.DataFrame, anchor_mode: str) -> pd.Timestamp:
    ts = df["ts"]
    latest = ts.iloc[-1]
    if anchor_mode == "Monthly Open":
        candidate = latest.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        valid = df[df["ts"] >= candidate]
        return valid["ts"].iloc[0] if not valid.empty else ts.iloc[0]
    if anchor_mode == "Prior 24H High":
        window = df.iloc[:-1].tail(min(288, max(50, len(df) // 2)))
        return window.loc[window["high"].idxmax(), "ts"] if not window.empty else ts.iloc[0]
    if anchor_mode == "Prior 24H Low":
        window = df.iloc[:-1].tail(min(288, max(50, len(df) // 2)))
        return window.loc[window["low"].idxmin(), "ts"] if not window.empty else ts.iloc[0]

    weekly_candidate = latest.normalize() - pd.Timedelta(days=latest.weekday())
    valid = df[df["ts"] >= weekly_candidate]
    return valid["ts"].iloc[0] if not valid.empty else ts.iloc[0]
def _build_anchored_vwap_frame(df: pd.DataFrame, anchor_mode: str) -> tuple[pd.DataFrame, pd.Timestamp]:
    out = df.sort_values("ts").copy()
    anchor_ts = _choose_anchor_timestamp(out, anchor_mode)
    anchor_mask = out["ts"] >= anchor_ts
    subset = out.loc[anchor_mask].copy()
    tp = (subset["high"] + subset["low"] + subset["close"]) / 3.0
    cum_vol = subset["base_vol"].cumsum().replace(0.0, np.nan)
    subset["avwap"] = (tp * subset["base_vol"]).cumsum() / cum_vol
    subset["anchor_std"] = subset["close"].expanding().std().fillna(0.0)
    subset["band_1_up"] = subset["avwap"] + subset["anchor_std"]
    subset["band_1_dn"] = subset["avwap"] - subset["anchor_std"]
    subset["band_2_up"] = subset["avwap"] + 2.0 * subset["anchor_std"]
    subset["band_2_dn"] = subset["avwap"] - 2.0 * subset["anchor_std"]
    subset["volume_z"] = (
        subset["quote_vol"]
        .rolling(20, min_periods=8)
        .apply(
            lambda values: 0.0
            if float(np.std(values[:-1])) < 1e-12
            else (values[-1] - float(np.mean(values[:-1]))) / float(np.std(values[:-1])),
            raw=True,
        )
        .fillna(0.0)
    )
    subset["taker_imbalance"] = np.where(
        subset["quote_vol"] > 0,
        ((2.0 * subset["taker_buy_quote"]) - subset["quote_vol"]) / subset["quote_vol"],
        0.0,
    )
    return subset, anchor_ts
def _detect_recent_sweeps(price_df: pd.DataFrame, oi_hist: pd.DataFrame) -> pd.DataFrame:
    df = price_df.copy()
    if not oi_hist.empty:
        df = pd.merge_asof(df.sort_values("ts"), oi_hist.sort_values("ts"), on="ts", direction="backward")
        df["oi_value_change"] = df["oi_value"].pct_change().fillna(0.0)
    else:
        df["oi_value_change"] = 0.0
    df["prev_high_20"] = df["high"].shift(1).rolling(20).max()
    df["prev_low_20"] = df["low"].shift(1).rolling(20).min()
    df["body"] = (df["close"] - df["open"]).abs()
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    upside = (
        (df["high"] > df["prev_high_20"])
        & (df["close"] < df["prev_high_20"])
        & (df["upper_wick"] > (df["body"] * 1.2))
        & (df["volume_z"] > 1.2)
    )
    downside = (
        (df["low"] < df["prev_low_20"])
        & (df["close"] > df["prev_low_20"])
        & (df["lower_wick"] > (df["body"] * 1.2))
        & (df["volume_z"] > 1.2)
    )
    sweeps = df[upside | downside].copy()
    if sweeps.empty:
        return pd.DataFrame(columns=["ts", "direction", "close", "volume_z", "taker_imbalance", "oi_value_change", "broken_level"])
    sweeps["direction"] = np.where(upside.loc[sweeps.index], "Up-sweep / stop-run risk", "Down-sweep / squeeze risk")
    sweeps["broken_level"] = np.where(upside.loc[sweeps.index], sweeps["prev_high_20"], sweeps["prev_low_20"])
    return sweeps[["ts", "direction", "close", "volume_z", "taker_imbalance", "oi_value_change", "broken_level"]].tail(8)
def _find_gamma_flip(strike_df: pd.DataFrame) -> Optional[float]:
    if strike_df.empty:
        return None
    cumulative = strike_df["signed_gex"].cumsum()
    sign = np.sign(cumulative.replace(0.0, np.nan)).ffill().bfill()
    prev_sign = sign.shift(1)
    flip_points = prev_sign.notna() & sign.ne(prev_sign)
    candidates = strike_df.loc[flip_points]
    if not candidates.empty:
        return float(candidates.iloc[0]["strike"])
    idx = (cumulative.abs()).idxmin()
    return float(strike_df.loc[idx, "strike"])
def _strike_map_for_options(options_df: pd.DataFrame) -> pd.DataFrame:
    if options_df.empty:
        return pd.DataFrame(columns=["strike", "call_gex", "put_gex", "signed_gex", "abs_gex", "total_oi", "avg_iv"])
    return (
        options_df.groupby("strike", as_index=False)
        .agg(
            call_gex=("call_gex", "sum"),
            put_gex=("put_gex", "sum"),
            signed_gex=("signed_gex", "sum"),
            abs_gex=("gex_abs", "sum"),
            total_oi=("open_interest", "sum"),
            avg_iv=("effective_iv", "mean"),
        )
        .sort_values("strike")
    )
def _top_expiry_hover_lines(group: pd.DataFrame, value_col: str, total: float) -> str:
    if group.empty or total <= 0:
        return "No material expiry concentration"
    lines = []
    for _, row in group.sort_values(value_col, ascending=False).head(3).iterrows():
        value = _safe_float(row.get(value_col))
        if value <= 0:
            continue
        share = value / max(total, 1e-12)
        lines.append(
            f"{_display_expiry_label(row.get('expiry_label'), _safe_float(row.get('hours_to_expiry')))}: "
            f"{value / 1000.0:.2f}B ({share:.0%})"
        )
    return "<br>".join(lines) if lines else "No material expiry concentration"
def _strike_expiry_context(options_df: pd.DataFrame, strike_map: pd.DataFrame, spot: float) -> pd.DataFrame:
    if options_df.empty or strike_map.empty:
        return strike_map.copy()

    expiry_groups = (
        options_df.groupby(["strike", "expiry_label", "expiration_ts"], as_index=False)
        .agg(
            call_gex=("call_gex", "sum"),
            put_gex=("put_gex", "sum"),
            signed_gex=("signed_gex", "sum"),
            abs_gex=("gex_abs", "sum"),
            total_oi=("open_interest", "sum"),
            hours_to_expiry=("hours_to_expiry", "min"),
        )
        .sort_values(["strike", "expiration_ts"])
    )

    rows: list[dict[str, object]] = []
    for strike, group in expiry_groups.groupby("strike", sort=True):
        total_abs = _safe_float(group["abs_gex"].sum())
        call_total = _safe_float(group["call_gex"].sum())
        put_total = _safe_float(group["put_gex"].sum())
        dominant = group.sort_values("abs_gex", ascending=False).iloc[0] if total_abs > 0 else group.iloc[0]
        front_24h = _safe_float(group.loc[group["hours_to_expiry"] <= FRONT_DAY_HOURS, "abs_gex"].sum())
        front_7d = _safe_float(group.loc[group["hours_to_expiry"] <= FRONT_WEEK_DAYS * 24, "abs_gex"].sum())
        rows.append(
            {
                "strike": _safe_float(strike),
                "call_expiry_hover": _top_expiry_hover_lines(group, "call_gex", call_total),
                "put_expiry_hover": _top_expiry_hover_lines(group, "put_gex", put_total),
                "dominant_expiry": _display_expiry_label(dominant.get("expiry_label"), _safe_float(dominant.get("hours_to_expiry"))),
                "dominant_expiry_share": _safe_float(dominant.get("abs_gex")) / max(total_abs, 1e-12),
                "front_24h_share": front_24h / max(total_abs, 1e-12),
                "front_7d_share": front_7d / max(total_abs, 1e-12),
                "distance_pct": ((float(strike) / spot) - 1.0) * 100.0 if spot > 0 else 0.0,
            }
        )

    return strike_map.merge(pd.DataFrame(rows), on="strike", how="left")
def _strike_expiry_breakdown_table(options_df: pd.DataFrame, spot: float, limit: int = 18) -> pd.DataFrame:
    if options_df.empty:
        return pd.DataFrame()
    nearby = options_df[(options_df["strike"] >= spot * 0.85) & (options_df["strike"] <= spot * 1.15)].copy()
    if nearby.empty:
        nearby = options_df.copy()

    expiry_groups = (
        nearby.groupby(["strike", "expiry_label", "expiration_ts"], as_index=False)
        .agg(
            call_gex=("call_gex", "sum"),
            put_gex=("put_gex", "sum"),
            signed_gex=("signed_gex", "sum"),
            abs_gex=("gex_abs", "sum"),
            total_oi=("open_interest", "sum"),
            hours_to_expiry=("hours_to_expiry", "min"),
        )
        .sort_values(["strike", "expiration_ts"])
    )

    rows: list[dict[str, object]] = []
    for strike, group in expiry_groups.groupby("strike", sort=True):
        total_abs = _safe_float(group["abs_gex"].sum())
        if total_abs <= 0:
            continue
        call_total = _safe_float(group["call_gex"].sum())
        put_total = _safe_float(group["put_gex"].sum())
        call_top = group.sort_values("call_gex", ascending=False).iloc[0]
        put_top = group.sort_values("put_gex", ascending=False).iloc[0]
        dominant = group.sort_values("abs_gex", ascending=False).iloc[0]
        rows.append(
            {
                "strike": _safe_float(strike),
                "distance_pct": ((float(strike) / spot) - 1.0) * 100.0 if spot > 0 else 0.0,
                "total_gex_b": total_abs / 1000.0,
                "call_gex_b": call_total / 1000.0,
                "put_gex_b": put_total / 1000.0,
                "net_gex_b": _safe_float(group["signed_gex"].sum()) / 1000.0,
                "dominant_expiry": _display_expiry_label(dominant.get("expiry_label"), _safe_float(dominant.get("hours_to_expiry"))),
                "dominant_share": _safe_float(dominant.get("abs_gex")) / max(total_abs, 1e-12),
                "top_call_expiry": _display_expiry_label(call_top.get("expiry_label"), _safe_float(call_top.get("hours_to_expiry"))) if call_total > 0 else "n/a",
                "top_put_expiry": _display_expiry_label(put_top.get("expiry_label"), _safe_float(put_top.get("hours_to_expiry"))) if put_total > 0 else "n/a",
                "front_24h_share": _safe_float(group.loc[group["hours_to_expiry"] <= FRONT_DAY_HOURS, "abs_gex"].sum()) / max(total_abs, 1e-12),
                "front_7d_share": _safe_float(group.loc[group["hours_to_expiry"] <= FRONT_WEEK_DAYS * 24, "abs_gex"].sum()) / max(total_abs, 1e-12),
                "total_oi": _safe_float(group["total_oi"].sum()),
            }
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("total_gex_b", ascending=False).head(limit)
def _front_gex_summary(options_df: pd.DataFrame, spot: float, max_hours: float, label: str) -> dict[str, object]:
    front = options_df[(options_df["hours_to_expiry"] > 0) & (options_df["hours_to_expiry"] <= max_hours)].copy()
    if front.empty:
        return {
            "label": label,
            "contracts": 0,
            "abs_gex": 0.0,
            "signed_gex": 0.0,
            "call_gex": 0.0,
            "put_gex": 0.0,
            "top_strike": 0.0,
            "top_distance_pct": 0.0,
            "top_abs_gex": 0.0,
            "zero_crossing": None,
            "strike_map": _strike_map_for_options(front),
        }
    strike_map = _strike_map_for_options(front)
    top = strike_map.sort_values("abs_gex", ascending=False).iloc[0]
    top_strike = _safe_float(top.get("strike"))
    return {
        "label": label,
        "contracts": int(len(front)),
        "abs_gex": float(front["gex_abs"].sum()),
        "signed_gex": float(front["signed_gex"].sum()),
        "call_gex": float(front["call_gex"].sum()),
        "put_gex": float(front["put_gex"].sum()),
        "top_strike": top_strike,
        "top_distance_pct": ((top_strike / spot) - 1.0) * 100.0 if spot > 0 else 0.0,
        "top_abs_gex": _safe_float(top.get("abs_gex")),
        # cumulative-GEX zero-crossing within THIS expiry window only.
        # Same proxy caveat as the full-chain flip: current signed GEX summed
        # by strike, not a repriced-chain gamma flip.
        "zero_crossing": _find_gamma_flip(strike_map.sort_values("strike").reset_index(drop=True)),
        "strike_map": strike_map,
    }
def _pin_candidate(options_df: pd.DataFrame, spot: float) -> dict[str, object]:
    front = options_df[(options_df["hours_to_expiry"] > 0) & (options_df["hours_to_expiry"] <= PIN_MAX_HOURS)].copy()
    if front.empty:
        return {"pin_score": 0.0, "pin_strike": 0.0, "pin_expiry": "", "hours_to_expiry": 0.0, "distance_pct": 0.0, "pin_copy": "No front-24h pin candidate is visible."}
    pin_map = (
        front.groupby(["expiration_ts", "expiry_label", "strike"], as_index=False)
        .agg(abs_gex=("gex_abs", "sum"), total_oi=("open_interest", "sum"))
        .assign(distance_pct=lambda df: ((df["strike"] / spot) - 1.0).abs() * 100.0)
    )
    nearby = pin_map[pin_map["distance_pct"] <= PIN_DISTANCE_PCT].copy()
    if nearby.empty:
        return {"pin_score": 0.0, "pin_strike": 0.0, "pin_expiry": "", "hours_to_expiry": 0.0, "distance_pct": 0.0, "pin_copy": "No high-GEX strike is close enough to spot for a clean pin read."}
    total_front_gex = float(front["gex_abs"].sum())
    nearby["concentration"] = nearby["abs_gex"] / max(total_front_gex, 1e-12)
    nearby["hours_to_expiry"] = (nearby["expiration_ts"] - _utc_now_naive()).dt.total_seconds() / 3600.0
    nearby["pin_score"] = (
        ((PIN_DISTANCE_PCT - nearby["distance_pct"]).clip(lower=0.0) / PIN_DISTANCE_PCT) * 52.0
        + nearby["concentration"].clip(upper=0.50) * 76.0
        + ((PIN_MAX_HOURS - nearby["hours_to_expiry"]).clip(lower=0.0) / PIN_MAX_HOURS) * 10.0
    ).clip(0.0, 100.0)
    top = nearby.sort_values("pin_score", ascending=False).iloc[0]
    strike = _safe_float(top.get("strike"))
    hours = max(_safe_float(top.get("hours_to_expiry")), 0.0)
    score = _safe_float(top.get("pin_score"))
    return {
        "pin_score": score,
        "pin_strike": strike,
        "pin_expiry": str(top.get("expiry_label", "")),
        "hours_to_expiry": hours,
        "distance_pct": _safe_float(top.get("distance_pct")),
        "pin_copy": f"PIN CANDIDATE: {strike:,.0f}, expires in {hours:.1f}h, score {score:.0f}/100.",
    }
def _risk_reversal_by_expiry(options_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (expiry_label, expiration_ts), group in options_df.groupby(["expiry_label", "expiration_ts"], sort=False):
        calls = group[group["option_type"] == "call"].copy()
        puts = group[group["option_type"] == "put"].copy()
        if calls.empty or puts.empty:
            continue
        call = calls.iloc[(calls["delta"] - 0.25).abs().argsort().iloc[0]]
        put = puts.iloc[(puts["delta"] + 0.25).abs().argsort().iloc[0]]
        call_iv = _safe_float(call.get("effective_iv"))
        put_iv = _safe_float(put.get("effective_iv"))
        rows.append(
            {
                "expiry_label": expiry_label,
                "expiration_ts": expiration_ts,
                "call_25d_iv": call_iv,
                "put_25d_iv": put_iv,
                "risk_reversal": call_iv - put_iv,
                "call_strike": _safe_float(call.get("strike")),
                "put_strike": _safe_float(put.get("strike")),
            }
        )
    return pd.DataFrame(rows).sort_values("expiration_ts") if rows else pd.DataFrame()
def _iv_term_structure(atm_iv: pd.DataFrame) -> dict[str, object]:
    if atm_iv.empty or len(atm_iv) < 2:
        return {"front_iv": 0.0, "back_iv": 0.0, "iv_ratio": 0.0, "term_regime": "Unavailable", "term_copy": "IV term structure is unavailable."}
    ordered = atm_iv.sort_values("expiration_ts")
    front_iv = _safe_float(ordered["effective_iv"].iloc[0])
    back_iv = _safe_float(ordered["effective_iv"].iloc[-1])
    ratio = front_iv / back_iv if back_iv > 0 else 0.0
    if ratio >= 1.08:
        regime = "Backwardation"
        copy = "Front IV is above back IV, which is a stress regime."
    elif ratio <= 0.92:
        regime = "Contango"
        copy = "Front IV is below back IV, which is a calmer term-structure regime."
    else:
        regime = "Flat"
        copy = "Front and back IV are close, so term structure is not sending a strong stress signal."
    return {"front_iv": front_iv, "back_iv": back_iv, "iv_ratio": ratio, "term_regime": regime, "term_copy": copy}
def _pressure_forecast(options_df: pd.DataFrame, iv_change_points: float = 0.0) -> dict[str, object]:
    near = options_df[(options_df["hours_to_expiry"] > 0) & (options_df["hours_to_expiry"] <= 168) & (options_df["moneyness_pct"].abs() <= 5.0)].copy()
    if near.empty:
        return {"charm_proxy": 0.0, "vanna_proxy": 0.0, "pressure_bias": "Neutral", "pressure_copy": "Estimated charm/vanna pressure is unavailable from the current chain."}
    hours = near["hours_to_expiry"].clip(lower=1.0)
    charm_proxy = float((near["delta"] * near["open_interest"] * near["contract_size"] * (8.0 / hours)).sum())
    vanna_proxy = float((near["vega"] * np.sign(near["delta"]) * iv_change_points).sum())
    combined = vanna_proxy - charm_proxy
    if combined > 0:
        bias = "Upside pressure"
    elif combined < 0:
        bias = "Downside pressure"
    else:
        bias = "Neutral"
    return {
        "charm_proxy": charm_proxy,
        "vanna_proxy": vanna_proxy,
        "pressure_bias": bias,
        "pressure_copy": f"8h chain delta-drift proxy: {bias}. Charm proxy {charm_proxy:+.2f}, vanna proxy {vanna_proxy:+.2f}. Computed from unsigned OI with an assumed positioning convention — dealer inventory direction is inferred, not observed.",
    }
def _read_options_history() -> pd.DataFrame:
    try:
        if BTC_OPTIONS_HISTORY_PATH.exists():
            return pd.read_csv(BTC_OPTIONS_HISTORY_PATH, parse_dates=["ts"])
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()
def _update_options_history(row: dict[str, object]) -> pd.DataFrame:
    history = _read_options_history()
    row_df = pd.DataFrame([row])
    out = pd.concat([history, row_df], ignore_index=True) if not history.empty else row_df
    out["ts"] = pd.to_datetime(out["ts"])
    cutoff = _utc_now_naive() - pd.Timedelta(days=45)
    out = out[out["ts"] >= cutoff].drop_duplicates(subset=["ts"], keep="last").sort_values("ts")
    try:
        BTC_OPTIONS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(BTC_OPTIONS_HISTORY_PATH, index=False)
    except Exception:
        pass
    return out
def _history_comparison(history: pd.DataFrame, current: dict[str, object]) -> dict[str, object]:
    if history.empty or len(history) < 4:
        return {"history_rows": int(len(history)), "gex_vs_30d_median": 0.0, "oi_24h_delta": 0.0, "rr_zscore": 0.0, "iv_change_points": 0.0}
    latest_ts = pd.to_datetime(current["ts"])
    prior_24h = history[history["ts"] <= latest_ts - pd.Timedelta(hours=24)]
    # Snapshots land every cache refresh (~minutes apart), so tail(30) of
    # raw rows is a couple of HOURS, not 30 days. Collapse to one snapshot
    # per day first so the median actually spans up to 30 days.
    gex_median = 0.0
    if "total_abs_gex" in history:
        daily_gex = (
            history.assign(day=history["ts"].dt.normalize())
            .groupby("day")["total_abs_gex"]
            .last()
        )
        gex_median = float(daily_gex.tail(30).median())
    oi_delta = 0.0
    iv_change = 0.0
    if not prior_24h.empty:
        prior = prior_24h.iloc[-1]
        oi_delta = _safe_float(current.get("total_oi")) - _safe_float(prior.get("total_oi"))
        iv_change = _safe_float(current.get("front_iv")) - _safe_float(prior.get("front_iv"))
    rr_series = history["front_rr"].dropna() if "front_rr" in history else pd.Series(dtype=float)
    rr_z = 0.0
    if len(rr_series) >= 5:
        rr_std = float(rr_series.std())
        rr_z = 0.0 if rr_std < 1e-12 else (_safe_float(current.get("front_rr")) - float(rr_series.mean())) / rr_std
    return {
        "history_rows": int(len(history)),
        "gex_vs_30d_median": (_safe_float(current.get("total_abs_gex")) / gex_median - 1.0) if gex_median > 0 else 0.0,
        "oi_24h_delta": oi_delta,
        "rr_zscore": rr_z,
        "iv_change_points": iv_change,
    }
def _block_flow_gamma_map(options_df: pd.DataFrame, days: int = BLOCK_FLOW_PRIMARY_DAYS) -> dict[str, object]:
    trades = _read_deribit_block_trades(days)
    columns = [
        "strike",
        "block_adjusted_gex",
        "block_abs_gex",
        "block_trades",
        "block_count",
        "rfq_trades",
        "matched_legs",
        "direction_buy_legs",
        "direction_sell_legs",
    ]
    empty = pd.DataFrame(columns=columns)
    if trades.empty or options_df.empty:
        return {
            "strike_map": empty,
            "trades": trades,
            "total_block_gex": 0.0,
            "total_abs_block_gex": 0.0,
            "matched_legs": 0,
            "unmatched_legs": int(len(trades)),
            "stored_trades": int(len(trades)),
            "blocks": 0,
            "rfq_trades": 0,
            "window_days": days,
            "status": "No block flow stored yet.",
        }

    greeks = options_df[
        ["instrument_name", "strike", "option_type", "gamma", "contract_size", "underlying_price"]
    ].copy()
    merged = trades.merge(greeks, on="instrument_name", how="left", indicator=True)
    matched = merged[merged["_merge"] == "both"].copy()
    if matched.empty:
        return {
            "strike_map": empty,
            "trades": trades,
            "total_block_gex": 0.0,
            "total_abs_block_gex": 0.0,
            "matched_legs": 0,
            "unmatched_legs": int(len(trades)),
            "stored_trades": int(len(trades)),
            "blocks": int(trades["block_trade_id"].nunique()) if "block_trade_id" in trades else 0,
            "rfq_trades": int(trades["block_rfq_id"].notna().sum()) if "block_rfq_id" in trades else 0,
            "window_days": days,
            "status": "Stored block flow did not match the current live option-greeks universe.",
        }

    matched["dealer_sign"] = np.where(matched["direction"].str.lower() == "buy", -1.0, 1.0)
    matched["rfq_weight"] = np.where(matched["block_rfq_id"].notna() & (matched["block_rfq_id"].astype(str) != ""), BLOCK_RFQ_WEIGHT, 1.0)
    matched["underlying_for_gex"] = matched["underlying_price"].replace(0.0, np.nan).fillna(matched["index_price"])
    # Same per-1%-move convention as options_df["gex_abs"] so block flow
    # stays directly comparable with the chain-wide strike map.
    matched["leg_gex_abs"] = (
        matched["gamma"].abs()
        * matched["amount"].abs()
        * matched["underlying_for_gex"].replace(0.0, np.nan).fillna(0.0)
        * matched["underlying_for_gex"].replace(0.0, np.nan).fillna(0.0)
        * matched["contract_size"].replace(0.0, 1.0).fillna(1.0)
        * 0.01
        / 1_000_000.0
    )
    matched["block_adjusted_gex"] = matched["dealer_sign"] * matched["leg_gex_abs"] * matched["rfq_weight"]
    matched["is_rfq"] = matched["block_rfq_id"].notna() & (matched["block_rfq_id"].astype(str) != "")
    grouped = (
        matched.groupby("strike", as_index=False)
        .agg(
            block_adjusted_gex=("block_adjusted_gex", "sum"),
            block_abs_gex=("leg_gex_abs", "sum"),
            block_trades=("trade_id", "nunique"),
            block_count=("block_trade_id", "nunique"),
            rfq_trades=("is_rfq", "sum"),
            matched_legs=("trade_id", "count"),
            direction_buy_legs=("direction", lambda values: int((values.str.lower() == "buy").sum())),
            direction_sell_legs=("direction", lambda values: int((values.str.lower() == "sell").sum())),
        )
        .sort_values("strike")
    )
    total_block_gex = float(grouped["block_adjusted_gex"].sum())
    total_abs_block_gex = float(grouped["block_abs_gex"].sum())
    status = (
        f"{int(len(matched))} matched block legs across {int(matched['block_trade_id'].nunique())} blocks "
        f"in the last {days}D. RFQ-tagged legs get {BLOCK_RFQ_WEIGHT:.1f}x confidence weight."
    )
    return {
        "strike_map": grouped[columns],
        "trades": matched,
        "total_block_gex": total_block_gex,
        "total_abs_block_gex": total_abs_block_gex,
        "matched_legs": int(len(matched)),
        "unmatched_legs": int((merged["_merge"] != "both").sum()),
        "stored_trades": int(len(trades)),
        "blocks": int(matched["block_trade_id"].nunique()),
        "rfq_trades": int(matched["is_rfq"].sum()),
        "window_days": days,
        "status": status,
    }
def _merge_block_flow_into_strikes(strike_map: pd.DataFrame, block_map: pd.DataFrame) -> pd.DataFrame:
    out = strike_map.copy()
    if block_map.empty:
        out["block_adjusted_gex"] = 0.0
        out["block_abs_gex"] = 0.0
        out["block_count"] = 0
        out["rfq_trades"] = 0
    else:
        out = out.merge(
            block_map[["strike", "block_adjusted_gex", "block_abs_gex", "block_count", "rfq_trades"]],
            on="strike",
            how="left",
        )
        for col in ["block_adjusted_gex", "block_abs_gex", "block_count", "rfq_trades"]:
            out[col] = out[col].fillna(0.0)
    out["block_agreement"] = np.where(
        out["block_abs_gex"] <= 0,
        "No block read",
        np.where(np.sign(out["signed_gex"]) == np.sign(out["block_adjusted_gex"]), "Agrees", "Disagrees"),
    )
    return out
def _block_flow_disagreement_rows(strike_map: pd.DataFrame, spot: float, limit: int = 3) -> pd.DataFrame:
    if strike_map.empty or "block_agreement" not in strike_map.columns:
        return pd.DataFrame()
    nearby = strike_map[
        (strike_map["block_agreement"] == "Disagrees")
        & (strike_map["strike"] >= spot * 0.80)
        & (strike_map["strike"] <= spot * 1.20)
    ].copy()
    if nearby.empty:
        return nearby
    nearby["distance_pct"] = ((nearby["strike"] / spot) - 1.0) * 100.0
    nearby["importance"] = nearby["block_abs_gex"].abs() * (1.0 + nearby["rfq_trades"].clip(lower=0.0))
    return nearby.sort_values("importance", ascending=False).head(limit)
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def build_btc_options_cockpit(anchor_mode: str) -> dict[str, object]:
    instruments_raw = _get_deribit("public/get_instruments", params={"currency": "BTC", "kind": "option", "expired": "false"}, timeout=20)
    summaries_raw = _get_deribit("public/get_book_summary_by_currency", params={"currency": "BTC", "kind": "option"}, timeout=20)

    instruments = pd.DataFrame(instruments_raw)
    summaries = pd.DataFrame(summaries_raw)
    if instruments.empty or summaries.empty:
        return {"error": "No Deribit BTC options data returned."}

    instruments = instruments.rename(columns={"option_type": "option_type_meta"})
    merged = summaries.merge(
        instruments[
            [
                "instrument_name",
                "expiration_timestamp",
                "strike",
                "option_type_meta",
                "contract_size",
            ]
        ],
        on="instrument_name",
        how="left",
    )
    now_ms = pd.Timestamp.now(tz="UTC").value // 10**6
    max_expiry_ms = now_ms + int(pd.Timedelta(days=BTC_OPTIONS_MAX_DAYS).total_seconds() * 1000)
    merged = merged[
        (merged["expiration_timestamp"].fillna(0).astype(np.int64) >= now_ms)
        & (merged["expiration_timestamp"].fillna(0).astype(np.int64) <= max_expiry_ms)
        & (merged["open_interest"].fillna(0).astype(float) > 0)
    ].copy()
    merged["expiration_ts"] = pd.to_datetime(merged["expiration_timestamp"].astype(np.int64), unit="ms", utc=True).dt.tz_localize(None)
    merged["expiry_label"] = merged["expiration_ts"].dt.strftime("%d-%b")
    merged["strike"] = merged["strike"].astype(float)
    merged["open_interest"] = merged["open_interest"].astype(float)
    merged["contract_size"] = merged["contract_size"].fillna(1.0).astype(float)
    if "volume_usd" not in merged.columns:
        merged["volume_usd"] = 0.0
    merged["volume_usd"] = merged["volume_usd"].fillna(0.0).astype(float)
    merged["option_type"] = (
        merged["option_type_meta"]
        .fillna(merged["instrument_name"].astype(str).str.split("-").str[-1].map({"C": "call", "P": "put"}))
        .astype(str)
        .str.lower()
    )
    merged = merged.sort_values("open_interest", ascending=False).head(BTC_OPTIONS_MAX_CONTRACTS)

    tickers: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, 24)) as ex:
        futures = {ex.submit(_safe_fetch_deribit_option_ticker, name): name for name in merged["instrument_name"]}
        for fut in as_completed(futures):
            payload = fut.result()
            if payload:
                tickers.append(payload)
    ticker_df = pd.DataFrame(tickers)
    if ticker_df.empty:
        return {"error": "No Deribit option tickers with greeks could be fetched."}

    options_df = merged.merge(ticker_df, on="instrument_name", how="inner")
    if options_df.empty:
        return {"error": "Options universe could not be merged with Deribit greeks."}

    for col in ["underlying_price", "mark_iv", "bid_iv", "ask_iv", "delta", "gamma", "vega", "theta", "last_price"]:
        if col not in options_df.columns:
            options_df[col] = 0.0

    options_df["effective_iv"] = options_df["mark_iv"]
    zero_iv = options_df["effective_iv"].abs() < 1e-12
    options_df.loc[zero_iv, "effective_iv"] = (
        (options_df.loc[zero_iv, "bid_iv"] + options_df.loc[zero_iv, "ask_iv"]) / 2.0
    )

    perp_snapshot = _fetch_binance_btc_perp_snapshot()
    ibit_context = _build_ibit_context()
    fallback_spot = _safe_float(perp_snapshot.get("index_price")) or _safe_float(perp_snapshot.get("mark_price"))
    spot_series = options_df["underlying_price"].replace(0.0, np.nan)
    spot = float(spot_series.median()) if not spot_series.dropna().empty else fallback_spot
    if spot <= 0.0:
        return {"error": "Could not determine a BTC spot reference from Deribit or Binance."}

    # Dollar gamma per 1% underlying move, in millions:
    # gamma (per $1) x OI x S^2 x 0.01. Without the 0.01 the headline
    # numbers run ~100x the per-1% convention used by every external GEX
    # source, which made them impossible to sanity-check against anyone.
    options_df["gex_abs"] = (
        options_df["gamma"].abs()
        * options_df["open_interest"]
        * options_df["underlying_price"].replace(0.0, spot).fillna(spot)
        * options_df["underlying_price"].replace(0.0, spot).fillna(spot)
        * options_df["contract_size"]
        * 0.01
        / 1_000_000.0
    )
    options_df["call_gex"] = np.where(options_df["option_type"] == "call", options_df["gex_abs"], 0.0)
    options_df["put_gex"] = np.where(options_df["option_type"] == "put", options_df["gex_abs"], 0.0)
    options_df["signed_gex"] = np.where(options_df["option_type"] == "call", options_df["gex_abs"], -options_df["gex_abs"])
    now_ts = _utc_now_naive()
    options_df["hours_to_expiry"] = (options_df["expiration_ts"] - now_ts).dt.total_seconds() / 3600.0
    options_df["days_to_expiry"] = options_df["hours_to_expiry"] / 24.0
    options_df["moneyness_pct"] = ((options_df["strike"] / spot) - 1.0) * 100.0

    strike_map = _strike_map_for_options(options_df)
    block_store_update = _update_deribit_block_trade_store()
    block_flow_7d = _block_flow_gamma_map(options_df, BLOCK_FLOW_PRIMARY_DAYS)
    block_flow_30d = _block_flow_gamma_map(options_df, BLOCK_FLOW_RETENTION_DAYS)
    strike_map = _merge_block_flow_into_strikes(strike_map, block_flow_7d.get("strike_map", pd.DataFrame()))
    block_disagreements = _block_flow_disagreement_rows(strike_map, spot)
    strike_expiry_context = _strike_expiry_context(options_df, strike_map, spot)
    strike_expiry_breakdown = _strike_expiry_breakdown_table(options_df, spot)
    expiry_map = (
        options_df.groupby(["expiry_label", "expiration_ts"], as_index=False)
        .agg(abs_gex=("gex_abs", "sum"), signed_gex=("signed_gex", "sum"), total_oi=("open_interest", "sum"), avg_iv=("effective_iv", "mean"))
        .sort_values("expiration_ts")
    )
    atm_iv = (
        options_df.assign(distance=(options_df["strike"] - spot).abs())
        .sort_values(["expiration_ts", "distance"])
        .groupby("expiry_label", as_index=False)
        .first()[["expiry_label", "expiration_ts", "effective_iv"]]
        .sort_values("expiration_ts")
    )

    nearby_strikes = strike_map[(strike_map["strike"] >= spot * 0.85) & (strike_map["strike"] <= spot * 1.15)].copy()
    if nearby_strikes.empty:
        nearby_strikes = strike_map.copy()
    support_levels = (
        nearby_strikes[nearby_strikes["strike"] < spot]
        .sort_values(["put_gex", "total_oi"], ascending=False)
        .head(3)
        .assign(distance_pct=lambda df: ((df["strike"] / spot) - 1.0) * 100.0)
    )
    resistance_levels = (
        nearby_strikes[nearby_strikes["strike"] > spot]
        .sort_values(["call_gex", "total_oi"], ascending=False)
        .head(3)
        .assign(distance_pct=lambda df: ((df["strike"] / spot) - 1.0) * 100.0)
    )
    gamma_flip = _find_gamma_flip(strike_map)
    total_abs_gex = float(options_df["gex_abs"].sum())
    total_signed_gex = float(options_df["signed_gex"].sum())
    top5_concentration = float(options_df["gex_abs"].nlargest(5).sum() / total_abs_gex) if total_abs_gex > 0 else 0.0
    front_24h = _front_gex_summary(options_df, spot, FRONT_DAY_HOURS, "Front 24H")
    front_7d = _front_gex_summary(options_df, spot, FRONT_WEEK_DAYS * 24, "Front 7D")
    pin = _pin_candidate(options_df, spot)
    risk_reversal = _risk_reversal_by_expiry(options_df)
    iv_term = _iv_term_structure(atm_iv)
    front_rr = _safe_float(risk_reversal["risk_reversal"].iloc[0]) if not risk_reversal.empty else 0.0
    history_row = {
        "ts": now_ts,
        "spot": spot,
        "total_abs_gex": total_abs_gex,
        "total_signed_gex": total_signed_gex,
        "front_24h_abs_gex": front_24h["abs_gex"],
        "front_7d_abs_gex": front_7d["abs_gex"],
        "front_rr": front_rr,
        "front_iv": iv_term["front_iv"],
        "total_oi": float(options_df["open_interest"].sum()),
    }
    history = _update_options_history(history_row)
    history_comparison = _history_comparison(history, history_row)
    pressure_forecast = _pressure_forecast(options_df, _safe_float(history_comparison.get("iv_change_points")))

    avwap_interval, avwap_limit = _avwap_fetch_plan(anchor_mode)
    klines = _fetch_binance_btc_perp_klines(avwap_interval, avwap_limit)
    oi_hist = _fetch_binance_btc_open_interest_hist()
    avwap_df, anchor_ts = _build_anchored_vwap_frame(klines, anchor_mode)
    sweeps = _detect_recent_sweeps(avwap_df, oi_hist)

    oi_latest_value = float(oi_hist["oi_value"].iloc[-1]) if not oi_hist.empty else 0.0
    oi_change_1h = float(oi_hist["oi_value"].pct_change(12).iloc[-1]) if len(oi_hist) > 12 else 0.0

    return {
        "spot": spot,
        "options_df": options_df,
        "strike_map": strike_map,
        "block_store_update": block_store_update,
        "block_flow_7d": block_flow_7d,
        "block_flow_30d": block_flow_30d,
        "block_disagreements": block_disagreements,
        "strike_expiry_context": strike_expiry_context,
        "strike_expiry_breakdown": strike_expiry_breakdown,
        "expiry_map": expiry_map,
        "atm_iv": atm_iv,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "gamma_flip": gamma_flip,
        "total_abs_gex": total_abs_gex,
        "total_signed_gex": total_signed_gex,
        "call_gex": float(options_df["call_gex"].sum()),
        "put_gex": float(options_df["put_gex"].sum()),
        "top5_concentration": top5_concentration,
        "front_24h": front_24h,
        "front_7d": front_7d,
        "pin": pin,
        "risk_reversal": risk_reversal,
        "iv_term": iv_term,
        "pressure_forecast": pressure_forecast,
        "history": history,
        "history_comparison": history_comparison,
        "perp_snapshot": perp_snapshot,
        "ibit_context": ibit_context,
        "anchor_ts": anchor_ts,
        "avwap_df": avwap_df,
        "sweeps": sweeps,
        "oi_latest_value": oi_latest_value,
        "oi_change_1h": oi_change_1h,
    }
