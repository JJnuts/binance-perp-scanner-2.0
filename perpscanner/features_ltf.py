"""Low-timeframe ignition metrics per symbol/interval."""

import numpy as np
import pandas as pd
from typing import Optional

from .config import (
    ATR_ROC_LOOKBACK,
    BASIS_CONFIRM_BP,
    COMPRESSION_RECENT_BARS,
    CONFLUENCE_TRIGGER_THRESHOLD,
    CONFLUENCE_WEIGHTS,
    FRESH_TRIGGER_BARS,
    RANGE_LOOKBACK_BARS,
    RS_LOOKBACK_BARS,
    TAKER_IMBALANCE_THRESHOLD,
    VOLUME_LOOKBACK,
    VWAP_SLOW,
)
from .regime import RegimeThresholds
from .indicators import (
    _atr_series,
    _bars_since_latest_true,
    _basis_context,
    _compression_score,
    _latest_trigger_transition,
    _oi_zscore,
    _range_context,
    _return_n,
    _rolling_percentile,
    _rolling_vwap_series,
    _rolling_zscore,
    _score_from_threshold,
)


def _direction_from_state(value: object) -> str:
    text = str(value)
    if text.startswith("Long"):
        return "Long"
    if text.startswith("Short"):
        return "Short"
    if text == "Compression":
        return "Compression"
    return "Neutral"
def _ltf_interval_metrics(
    symbol: str,
    interval: str,
    df: pd.DataFrame,
    oi_df: pd.DataFrame,
    btc_df: pd.DataFrame,
    eth_df: Optional[pd.DataFrame],
    spot_df: Optional[pd.DataFrame],
    thresholds: Optional[RegimeThresholds] = None,
) -> dict[str, object]:
    thr = thresholds if thresholds is not None else RegimeThresholds()
    atr = _atr_series(df)
    atr_value = float(atr.iloc[-1]) if not atr.empty and pd.notna(atr.iloc[-1]) else 0.0
    atr_percentile_series = _rolling_percentile(atr)
    atr_percentile = float(atr_percentile_series.iloc[-1]) if not atr_percentile_series.empty else 50.0
    atr_roc_series = (atr / atr.shift(ATR_ROC_LOOKBACK) - 1.0).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    atr_roc = float(atr_roc_series.iloc[-1]) if not atr_roc_series.empty else 0.0

    volume_z_series = _rolling_zscore(df["quote_vol"], VOLUME_LOOKBACK)
    volume_zscore = float(volume_z_series.iloc[-1]) if not volume_z_series.empty else 0.0
    oi_zscore, oi_z_series = _oi_zscore(oi_df, VOLUME_LOOKBACK)
    if oi_z_series.empty:
        oi_z_aligned = pd.Series(0.0, index=df.index)
    else:
        oi_z_aligned = oi_z_series.reindex(df.index, method="ffill").fillna(0.0)
    price = float(df["close"].iloc[-1])
    vwap_series = _rolling_vwap_series(df, min(VWAP_SLOW, len(df)))
    vwap = float(vwap_series.iloc[-1])
    price_distance_from_vwap_atr = (price - vwap) / max(atr_value, 1e-12)

    range_lookback = RANGE_LOOKBACK_BARS.get(interval, 24)
    range_high, range_low, range_position, range_width_atr = _range_context(df, range_lookback, atr_value)
    prior_high = df["high"].shift(1).rolling(range_lookback).max()
    prior_low = df["low"].shift(1).rolling(range_lookback).min()
    breakout_distance_series = pd.Series(0.0, index=df.index)
    atr_denom = atr.replace(0.0, np.nan).ffill().fillna(0.0).clip(lower=1e-12)
    breakout_distance_series = np.where(
        df["close"] > prior_high,
        (df["close"] - prior_high) / atr_denom,
        np.where(df["close"] < prior_low, (df["close"] - prior_low) / atr_denom, 0.0),
    )
    breakout_distance_series = pd.Series(breakout_distance_series, index=df.index).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    breakout_distance_atr = float(breakout_distance_series.iloc[-1]) if not breakout_distance_series.empty else 0.0
    break_hold_long_series = (df["close"] > prior_high) & (df["close"].shift(1) > prior_high.shift(1))
    break_hold_short_series = (df["close"] < prior_low) & (df["close"].shift(1) < prior_low.shift(1))
    break_hold_confirmed = bool(break_hold_long_series.iloc[-1] or break_hold_short_series.iloc[-1])

    lookback = RS_LOOKBACK_BARS.get(interval, 4)
    ret = _return_n(df["close"], lookback)
    btc_ret = _return_n(btc_df["close"], lookback) if btc_df is not None and not btc_df.empty else 0.0
    eth_ret = _return_n(eth_df["close"], lookback) if eth_df is not None and not eth_df.empty else 0.0
    rs_vs_btc = ret - btc_ret
    rs_vs_eth = ret - eth_ret

    recent_len = COMPRESSION_RECENT_BARS.get(interval, 12)
    compression_series = (atr_percentile_series < 20.0) & (volume_z_series < 0.5) & (oi_z_aligned.abs() < 0.5)
    compression_count_series = compression_series.astype(float).rolling(recent_len, min_periods=1).sum()
    compression_recent_bars = int(compression_count_series.iloc[-1]) if not compression_count_series.empty else 0

    atr_compression_score = _compression_score(atr_percentile, volume_zscore, oi_zscore)
    atr_expansion_score = _score_from_threshold(max(atr_roc, 0.0), thr.atr_roc, 70.0)
    volume_spike_score = _score_from_threshold(max(volume_zscore, 0.0), thr.volume_z, 80.0)
    oi_spike_score = _score_from_threshold(max(oi_zscore, 0.0), thr.oi_z, 80.0)
    range_break_score = _score_from_threshold(abs(breakout_distance_atr), 0.50, 75.0)
    rs_long_score = float(np.clip((rs_vs_btc * 800.0) + (rs_vs_eth * 500.0), 0.0, 100.0))
    rs_short_score = float(np.clip((-rs_vs_btc * 800.0) + (-rs_vs_eth * 500.0), 0.0, 100.0))

    taker_delta = ((2.0 * df["tb_quote"]) - df["quote_vol"]).fillna(0.0)
    taker_imbalance_series = (taker_delta / df["quote_vol"].replace(0.0, np.nan)).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    taker_imbalance = float(taker_imbalance_series.iloc[-1]) if not taker_imbalance_series.empty else 0.0
    cvd = taker_delta.cumsum()
    cvd_3bar_slope_series = cvd.diff(3).fillna(0.0)
    cvd_3bar_slope = float(cvd_3bar_slope_series.iloc[-1]) if not cvd_3bar_slope_series.empty else 0.0
    taker_long_confirmed = taker_imbalance > TAKER_IMBALANCE_THRESHOLD and cvd_3bar_slope > 0.0
    taker_short_confirmed = taker_imbalance < -TAKER_IMBALANCE_THRESHOLD and cvd_3bar_slope < 0.0
    taker_long_series = (taker_imbalance_series > TAKER_IMBALANCE_THRESHOLD) & (cvd_3bar_slope_series > 0.0)
    taker_short_series = (taker_imbalance_series < -TAKER_IMBALANCE_THRESHOLD) & (cvd_3bar_slope_series < 0.0)

    basis_bp, basis_delta_3bar_bp, basis_available = _basis_context(df, spot_df)
    if basis_available:
        spot_close = spot_df["close"].reindex(df.index, method="ffill")
        basis_series = ((df["close"] - spot_close) / spot_close.replace(0.0, np.nan) * 10000.0).replace(
            [np.inf, -np.inf],
            0.0,
        ).fillna(0.0)
        basis_delta_series = basis_series.diff(3).fillna(0.0)
        basis_long_series = (basis_series > BASIS_CONFIRM_BP) & (basis_delta_series > 0.0)
        basis_short_series = (basis_series < -BASIS_CONFIRM_BP) & (basis_delta_series < 0.0)
    else:
        basis_long_series = pd.Series(True, index=df.index)
        basis_short_series = pd.Series(True, index=df.index)
    basis_long_confirmed = bool(basis_long_series.iloc[-1]) if not basis_long_series.empty else False
    basis_short_confirmed = bool(basis_short_series.iloc[-1]) if not basis_short_series.empty else False

    expansion_ready_series = (atr_roc_series > thr.atr_roc) & (compression_count_series > 0)
    volume_spike_series = volume_z_series > thr.volume_z
    oi_spike_series = oi_z_aligned > thr.oi_z

    # Confluence-plus-veto trigger. The structural conditions (right side
    # of VWAP, break-and-hold of the prior range) are hard vetoes; the
    # remaining confirmations are scored so a strong majority can fire
    # without demanding all five at once (the old 7-way AND had a joint
    # probability so low it mostly fired during market-wide moves).
    long_veto_series = (df["close"] > vwap_series) & break_hold_long_series
    short_veto_series = (df["close"] < vwap_series) & break_hold_short_series
    long_confluence_series = (
        CONFLUENCE_WEIGHTS["expansion"] * expansion_ready_series.astype(float)
        + CONFLUENCE_WEIGHTS["volume"] * volume_spike_series.astype(float)
        + CONFLUENCE_WEIGHTS["oi"] * oi_spike_series.astype(float)
        + CONFLUENCE_WEIGHTS["taker"] * taker_long_series.astype(float)
        + CONFLUENCE_WEIGHTS["basis"] * basis_long_series.astype(float)
    )
    short_confluence_series = (
        CONFLUENCE_WEIGHTS["expansion"] * expansion_ready_series.astype(float)
        + CONFLUENCE_WEIGHTS["volume"] * volume_spike_series.astype(float)
        + CONFLUENCE_WEIGHTS["oi"] * oi_spike_series.astype(float)
        + CONFLUENCE_WEIGHTS["taker"] * taker_short_series.astype(float)
        + CONFLUENCE_WEIGHTS["basis"] * basis_short_series.astype(float)
    )
    confluence_long = float(long_confluence_series.iloc[-1]) if not long_confluence_series.empty else 0.0
    confluence_short = float(short_confluence_series.iloc[-1]) if not short_confluence_series.empty else 0.0
    raw_long_trigger_series = long_veto_series & (long_confluence_series >= CONFLUENCE_TRIGGER_THRESHOLD)
    raw_short_trigger_series = short_veto_series & (short_confluence_series >= CONFLUENCE_TRIGGER_THRESHOLD)
    long_trigger_transition = _latest_trigger_transition(raw_long_trigger_series)
    short_trigger_transition = _latest_trigger_transition(raw_short_trigger_series)
    long_bars_since_trigger = _bars_since_latest_true(long_trigger_transition)
    short_bars_since_trigger = _bars_since_latest_true(short_trigger_transition)
    fresh_limit = FRESH_TRIGGER_BARS.get(interval, 2)
    long_trigger = bool(raw_long_trigger_series.iloc[-1]) and long_bars_since_trigger <= fresh_limit
    short_trigger = bool(raw_short_trigger_series.iloc[-1]) and short_bars_since_trigger <= fresh_limit
    if long_bars_since_trigger <= short_bars_since_trigger:
        bars_since_trigger = long_bars_since_trigger
        trigger_direction = "Long" if long_bars_since_trigger < 999 else "None"
    else:
        bars_since_trigger = short_bars_since_trigger
        trigger_direction = "Short" if short_bars_since_trigger < 999 else "None"
    trigger_fresh = bool((long_trigger and trigger_direction == "Long") or (short_trigger and trigger_direction == "Short"))

    long_score = (
        0.20 * min(100.0, compression_recent_bars * 18.0)
        + 0.20 * atr_expansion_score
        + 0.20 * volume_spike_score
        + 0.15 * oi_spike_score
        + 0.15 * range_break_score
        + 0.10 * rs_long_score
    )
    short_score = (
        0.20 * min(100.0, compression_recent_bars * 18.0)
        + 0.20 * atr_expansion_score
        + 0.20 * volume_spike_score
        + 0.15 * oi_spike_score
        + 0.15 * range_break_score
        + 0.10 * rs_short_score
    )

    if long_trigger:
        ignition_state = "Long ignition"
        ignition_score = max(long_score, 75.0)
    elif short_trigger:
        ignition_state = "Short ignition"
        ignition_score = max(short_score, 75.0)
    elif compression_recent_bars > 0 and atr_percentile < 25.0:
        ignition_state = "Compression"
        ignition_score = max(atr_compression_score * 0.55, long_score, short_score)
    elif long_score >= short_score and long_score >= 45.0:
        ignition_state = "Long watch"
        ignition_score = long_score
    elif short_score > long_score and short_score >= 45.0:
        ignition_state = "Short watch"
        ignition_score = short_score
    else:
        ignition_state = "Neutral"
        ignition_score = max(long_score, short_score)

    return {
        "symbol": symbol,
        "timeframe": interval,
        "ignition_state": ignition_state,
        "ignition_score": float(np.clip(ignition_score, 0.0, 100.0)),
        "long_ignition_score": float(np.clip(long_score, 0.0, 100.0)),
        "short_ignition_score": float(np.clip(short_score, 0.0, 100.0)),
        "atr_value": atr_value,
        "atr_percentile": atr_percentile,
        "atr_compression_score": atr_compression_score,
        "atr_roc": atr_roc,
        "atr_expansion_score": atr_expansion_score,
        "volume_zscore": volume_zscore,
        "oi_zscore": oi_zscore,
        "taker_imbalance": taker_imbalance,
        "cvd_3bar_slope": cvd_3bar_slope,
        "taker_long_confirmed": taker_long_confirmed,
        "taker_short_confirmed": taker_short_confirmed,
        "basis_bp": basis_bp,
        "basis_delta_3bar_bp": basis_delta_3bar_bp,
        "basis_available": basis_available,
        "basis_long_confirmed": basis_long_confirmed,
        "basis_short_confirmed": basis_short_confirmed,
        "price_distance_from_vwap_atr": price_distance_from_vwap_atr,
        "breakout_distance_atr": breakout_distance_atr,
        "break_hold_confirmed": break_hold_confirmed,
        "range_position": range_position,
        "range_width_atr": range_width_atr,
        "compression_recent_bars": compression_recent_bars,
        "bars_since_trigger": bars_since_trigger,
        "trigger_fresh": trigger_fresh,
        "trigger_direction": trigger_direction,
        "confluence_long": confluence_long,
        "confluence_short": confluence_short,
        "regime": thr.regime,
        "volume_z_gate": thr.volume_z,
        "oi_z_gate": thr.oi_z,
        "atr_roc_gate": thr.atr_roc,
        "rs_vs_btc": rs_vs_btc,
        "rs_vs_eth": rs_vs_eth,
    }
