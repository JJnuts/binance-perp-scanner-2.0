"""Daily swing context and BTC daily regime."""

import numpy as np
import pandas as pd
from typing import Optional

from .indicators import (
    _atr_series,
    _basis_context,
    _consecutive_true_tail,
    _ema,
    _pivot_levels,
    _return_n,
    _rolling_percentile,
)


def _daily_swing_context(daily_df: Optional[pd.DataFrame], daily_oi: pd.DataFrame, spot_daily: Optional[pd.DataFrame]) -> dict[str, object]:
    defaults: dict[str, object] = {
        "daily_close_above_prior_high": False,
        "daily_close_below_prior_low": False,
        "daily_atr_percentile": 50.0,
        "daily_volume_ratio": 0.0,
        "daily_volume_persistence_days": 0,
        "daily_oi_persistence_days": 0,
        "daily_basis_bp": 0.0,
        "daily_basis_delta_3d_bp": 0.0,
        "daily_swing_high": 0.0,
        "daily_swing_low": 0.0,
        "daily_reclaim_high": False,
        "daily_reject_high": False,
        "daily_hold_above_swing_high": False,
        "daily_lose_swing_low": False,
        "daily_structure_score": 0.0,
        "daily_long_confirmed": False,
        "daily_short_confirmed": False,
    }
    if daily_df is None or daily_df.empty or len(daily_df) < 25:
        return defaults

    df = daily_df.copy()
    close = df["close"]
    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    prior_high = float(df["high"].iloc[-2])
    prior_low = float(df["low"].iloc[-2])
    atr = _atr_series(df)
    atr_percentile = float(_rolling_percentile(atr).iloc[-1]) if not atr.empty else 50.0
    volume_baseline = float(df["quote_vol"].iloc[-21:-1].mean()) if len(df) > 21 else 0.0
    volume_ratio = float(df["quote_vol"].iloc[-1] / volume_baseline) if volume_baseline > 0 else 0.0
    volume_persistence = _consecutive_true_tail(df["quote_vol"] > df["quote_vol"].shift(1).rolling(20).mean())

    pivot_highs = _pivot_levels(df["high"], "high")
    pivot_lows = _pivot_levels(df["low"], "low")
    swing_high = max(pivot_highs) if pivot_highs else float(df["high"].iloc[-15:-1].max())
    swing_low = min(pivot_lows) if pivot_lows else float(df["low"].iloc[-15:-1].min())
    reclaim_high = price > swing_high and prev_close <= swing_high and volume_ratio >= 1.05
    reject_high = float(df["high"].iloc[-1]) >= swing_high and price < swing_high and volume_ratio >= 1.05
    hold_above_swing_high = price > swing_high and float(df["low"].iloc[-1]) > swing_high
    lose_swing_low = price < swing_low and prev_close >= swing_low

    oi_persistence = 0
    if isinstance(daily_oi, pd.DataFrame) and not daily_oi.empty and len(daily_oi) > 3:
        oi_persistence = _consecutive_true_tail(daily_oi["oi_value"].diff() > 0)

    basis_bp, basis_delta_3d_bp, basis_available = _basis_context(df, spot_daily)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    daily_close_above_prior_high = price > prior_high
    daily_close_below_prior_low = price < prior_low
    daily_long_score = float(
        np.clip(
            25.0 * float(daily_close_above_prior_high)
            + 20.0 * float(reclaim_high or hold_above_swing_high)
            + 15.0 * float(price > ema20)
            + 15.0 * float(ema20 > ema50)
            + 15.0 * np.clip(volume_ratio / 1.5, 0.0, 1.0)
            + 10.0 * np.clip(oi_persistence / 3.0, 0.0, 1.0),
            0.0,
            100.0,
        )
    )
    daily_short_score = float(
        np.clip(
            25.0 * float(daily_close_below_prior_low)
            + 20.0 * float(reject_high or lose_swing_low)
            + 15.0 * float(price < ema20)
            + 15.0 * float(ema20 < ema50)
            + 15.0 * np.clip(volume_ratio / 1.5, 0.0, 1.0)
            + 10.0 * np.clip(oi_persistence / 3.0, 0.0, 1.0),
            0.0,
            100.0,
        )
    )
    return {
        "daily_close_above_prior_high": bool(daily_close_above_prior_high),
        "daily_close_below_prior_low": bool(daily_close_below_prior_low),
        "daily_atr_percentile": atr_percentile,
        "daily_volume_ratio": volume_ratio,
        "daily_volume_persistence_days": int(volume_persistence),
        "daily_oi_persistence_days": int(oi_persistence),
        "daily_basis_bp": basis_bp if basis_available else 0.0,
        "daily_basis_delta_3d_bp": basis_delta_3d_bp if basis_available else 0.0,
        "daily_swing_high": swing_high,
        "daily_swing_low": swing_low,
        "daily_reclaim_high": bool(reclaim_high),
        "daily_reject_high": bool(reject_high),
        "daily_hold_above_swing_high": bool(hold_above_swing_high),
        "daily_lose_swing_low": bool(lose_swing_low),
        "daily_structure_score": max(daily_long_score, daily_short_score),
        "daily_long_score": daily_long_score,
        "daily_short_score": daily_short_score,
        "daily_long_confirmed": bool(daily_long_score >= 55.0 and (daily_close_above_prior_high or reclaim_high or hold_above_swing_high)),
        "daily_short_confirmed": bool(daily_short_score >= 55.0 and (daily_close_below_prior_low or reject_high or lose_swing_low)),
    }
def _btc_daily_regime(daily_df: Optional[pd.DataFrame]) -> dict[str, object]:
    if daily_df is None or daily_df.empty or len(daily_df) < 55:
        return {"btc_daily_regime": "Unknown", "btc_daily_regime_score": 50.0, "btc_long_multiplier": 1.0, "btc_short_multiplier": 1.0}
    close = daily_df["close"]
    price = float(close.iloc[-1])
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ret_7d = _return_n(close, 7)
    prior_high = float(daily_df["high"].iloc[-2])
    prior_low = float(daily_df["low"].iloc[-2])
    score = float(
        np.clip(
            25.0 * float(price > ema20)
            + 25.0 * float(ema20 > ema50)
            + 20.0 * float(ret_7d > 0)
            + 15.0 * float(price > prior_high)
            + 15.0 * float(price > prior_low),
            0.0,
            100.0,
        )
    )
    if score >= 70:
        regime = "Bull trend"
    elif score <= 35:
        regime = "Drawdown"
    else:
        regime = "Range"
    return {
        "btc_daily_regime": regime,
        "btc_daily_regime_score": score,
        "btc_long_multiplier": 1.10 if score >= 70 else 0.82 if score <= 35 else 1.0,
        "btc_short_multiplier": 1.10 if score <= 35 else 0.88 if score >= 70 else 1.0,
    }
