"""Pure pandas/numpy feature math. No I/O, no Streamlit."""

import numpy as np
import pandas as pd
from typing import Optional

from .config import ATR_PERCENTILE_LOOKBACK, ATR_PERIOD, VOLUME_LOOKBACK


def _vwap(df: pd.DataFrame, n: int) -> float:
    tail = df.tail(n)
    tp = (tail["high"] + tail["low"] + tail["close"]) / 3.0
    volume = float(tail["vol"].sum())
    if volume <= 0:
        return float(tail["close"].iloc[-1])
    return float((tp * tail["vol"]).sum() / volume)
def _vwap_zscore(df: pd.DataFrame, n: int) -> float:
    tail = df.tail(n)
    vwap = _vwap(df, n)
    std = float(tail["close"].std())
    if std < 1e-12:
        return 0.0
    return float((tail["close"].iloc[-1] - vwap) / std)
def _ema(series: pd.Series, span: int) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])
def _return_n(series: pd.Series, n: int) -> float:
    if len(series) <= n:
        return 0.0
    prev = float(series.iloc[-1 - n])
    if abs(prev) < 1e-12:
        return 0.0
    return float(series.iloc[-1] / prev - 1.0)
def _aligned_return_frame(asset_close: pd.Series, btc_close: pd.Series) -> pd.DataFrame:
    return pd.concat(
        [
            asset_close.pct_change().rename("asset"),
            btc_close.pct_change().rename("btc"),
        ],
        axis=1,
        join="inner",
    ).dropna()
def _estimate_beta(aligned_returns: pd.DataFrame, beta_lookback: int = 72) -> float:
    if len(aligned_returns) < 12:
        return 1.0

    window = aligned_returns.tail(min(beta_lookback, len(aligned_returns)))
    btc_var = float(window["btc"].var())
    return 1.0 if btc_var < 1e-12 else float(window["asset"].cov(window["btc"]) / btc_var)
def _alpha_from_beta(asset_close: pd.Series, btc_close: pd.Series, n: int, beta: float) -> float:
    return _return_n(asset_close, n) - beta * _return_n(btc_close, n)
def _beta_adjusted_alpha(asset_close: pd.Series, btc_close: pd.Series, n: int, beta_lookback: int = 72) -> float:
    merged = _aligned_return_frame(asset_close, btc_close)
    beta = _estimate_beta(merged, beta_lookback)
    return _alpha_from_beta(asset_close, btc_close, n, beta)
def _vol_adjusted_return(series: pd.Series, n: int) -> float:
    returns = series.pct_change().dropna()
    if len(returns) < max(12, n):
        return 0.0
    std = float(returns.tail(max(24, n)).std())
    if std < 1e-12:
        return 0.0
    return float(_return_n(series, n) / (std * np.sqrt(n)))
def _volume_ratio(series: pd.Series, lookback: int = VOLUME_LOOKBACK) -> float:
    tail = series.tail(lookback + 1)
    if len(tail) < lookback + 1:
        return 0.0
    baseline = float(tail.iloc[:-1].mean())
    if baseline <= 0:
        return 0.0
    return float(tail.iloc[-1] / baseline)
def _volume_zscore(series: pd.Series, lookback: int = VOLUME_LOOKBACK) -> float:
    tail = series.tail(lookback + 1)
    if len(tail) < lookback + 1:
        return 0.0
    baseline = tail.iloc[:-1]
    std = float(baseline.std())
    if std < 1e-12:
        return 0.0
    return float((tail.iloc[-1] - float(baseline.mean())) / std)
def _rolling_zscore(series: pd.Series, lookback: int = VOLUME_LOOKBACK) -> pd.Series:
    baseline_mean = series.shift(1).rolling(lookback).mean()
    baseline_std = series.shift(1).rolling(lookback).std()
    return ((series - baseline_mean) / (baseline_std + 1e-10)).replace([np.inf, -np.inf], 0.0).fillna(0.0)
def _atr_series(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
def _rolling_percentile(series: pd.Series, lookback: int = ATR_PERCENTILE_LOOKBACK) -> pd.Series:
    def _percentile(values: np.ndarray) -> float:
        current = values[-1]
        if np.isnan(current):
            return 50.0
        valid = values[~np.isnan(values)]
        if len(valid) < 5:
            return 50.0
        return float((valid <= current).mean() * 100.0)

    return series.rolling(lookback, min_periods=max(20, ATR_PERIOD + 2)).apply(_percentile, raw=True).fillna(50.0)
def _score_from_threshold(value: float, threshold: float, cap: float) -> float:
    if threshold <= 0:
        return 0.0
    return float(np.clip((value / threshold) * cap, 0.0, 100.0))
def _compression_score(atr_percentile: float, volume_zscore: float, oi_zscore: float) -> float:
    atr_component = np.clip((35.0 - atr_percentile) / 35.0, 0.0, 1.0)
    volume_component = np.clip((0.75 - max(volume_zscore, 0.0)) / 0.75, 0.0, 1.0)
    oi_component = np.clip((0.75 - abs(oi_zscore)) / 0.75, 0.0, 1.0)
    return float((0.55 * atr_component + 0.25 * volume_component + 0.20 * oi_component) * 100.0)
def _range_context(df: pd.DataFrame, lookback: int, atr_value: float) -> tuple[float, float, float, float]:
    if len(df) <= lookback:
        return 0.0, 0.0, 0.5, 0.0

    prior = df.iloc[-lookback - 1 : -1]
    price = float(df["close"].iloc[-1])
    range_high = float(prior["high"].max())
    range_low = float(prior["low"].min())
    range_width = max(range_high - range_low, 0.0)
    if atr_value <= 0 or not np.isfinite(atr_value):
        # No usable ATR (flat or brand-new market): ATR-normalized
        # distances are meaningless, so report no breakout / zero width
        # instead of dividing by epsilon and exploding.
        range_position = 0.5 if range_width <= 1e-12 else float(np.clip((price - range_low) / range_width, 0.0, 1.0))
        return range_high, range_low, range_position, 0.0
    denom = atr_value

    if price > range_high:
        breakout_distance = (price - range_high) / denom
    elif price < range_low:
        breakout_distance = (price - range_low) / denom
    else:
        breakout_distance = 0.0

    if range_width <= 1e-12:
        range_position = 0.5
    else:
        range_position = float(np.clip((price - range_low) / range_width, 0.0, 1.0))
    return range_high, range_low, range_position, float(range_width / denom)
def _oi_zscore(oi_df: pd.DataFrame, lookback: int = VOLUME_LOOKBACK) -> tuple[float, pd.Series]:
    if oi_df.empty or len(oi_df) < lookback + 2:
        return 0.0, pd.Series(dtype=float)
    oi_change = oi_df["oi_value"].pct_change().replace([np.inf, -np.inf], 0.0).fillna(0.0)
    z_series = _rolling_zscore(oi_change, lookback)
    return float(z_series.iloc[-1]), z_series
def _rolling_vwap_series(df: pd.DataFrame, n: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv_sum = (tp * df["vol"]).rolling(n, min_periods=2).sum()
    vol_sum = df["vol"].rolling(n, min_periods=2).sum()
    return (pv_sum / vol_sum.replace(0.0, np.nan)).fillna(df["close"])
def _bars_since_latest_true(series: pd.Series) -> int:
    truthy = series.fillna(False).astype(bool)
    if truthy.empty or not bool(truthy.any()):
        return 999
    positions = np.flatnonzero(truthy.to_numpy())
    return int(len(truthy) - 1 - positions[-1])
def _latest_trigger_transition(raw_trigger: pd.Series) -> pd.Series:
    trigger = raw_trigger.fillna(False).astype(bool)
    return trigger & ~trigger.shift(1, fill_value=False)
def _basis_context(perp_df: pd.DataFrame, spot_df: Optional[pd.DataFrame]) -> tuple[float, float, bool]:
    if spot_df is None or spot_df.empty:
        return 0.0, 0.0, False
    spot_close = spot_df["close"].reindex(perp_df.index, method="ffill")
    basis = ((perp_df["close"] - spot_close) / spot_close.replace(0.0, np.nan) * 10000.0).replace(
        [np.inf, -np.inf],
        np.nan,
    )
    if basis.dropna().empty:
        return 0.0, 0.0, False
    basis = basis.ffill().fillna(0.0)
    return float(basis.iloc[-1]), float(basis.diff(3).fillna(0.0).iloc[-1]), True
def _pivot_levels(series: pd.Series, mode: str, lookback: int = 14, wing: int = 2) -> list[float]:
    recent = series.tail(lookback + wing).dropna()
    levels: list[float] = []
    if len(recent) < wing * 2 + 1:
        return levels
    values = recent.to_numpy()
    for idx in range(wing, len(values) - wing):
        window = values[idx - wing : idx + wing + 1]
        current = values[idx]
        if mode == "high" and current == float(np.max(window)) and current > float(np.max(np.delete(window, wing))):
            levels.append(float(current))
        if mode == "low" and current == float(np.min(window)) and current < float(np.min(np.delete(window, wing))):
            levels.append(float(current))
    return levels[-3:]
def _consecutive_true_tail(series: pd.Series) -> int:
    count = 0
    for value in reversed(series.fillna(False).astype(bool).tolist()):
        if not value:
            break
        count += 1
    return count
def _tanh_scale(value: float, scale: float) -> float:
    return float(np.tanh(value * scale))
def _percentile_score(series: pd.Series, ascending: bool = True) -> pd.Series:
    ranked = series.rank(method="average", pct=True)
    if not ascending:
        ranked = 1.0 - ranked
    return (ranked.fillna(0.5) * 100.0).clip(0.0, 100.0)
def _funding_quality_score(funding_z: pd.Series, funding_rate: pd.Series) -> pd.Series:
    neutrality = 1.0 - (funding_z.abs().clip(upper=3.0) / 3.0)
    mild_positive = ((funding_rate >= 0) & (funding_rate <= 0.0008)).astype(float) * 0.15
    crowding_penalty = (funding_z > 2.0).astype(float) * 0.2
    raw = (neutrality + mild_positive - crowding_penalty).clip(lower=0.0, upper=1.0)
    return raw * 100.0
def _funding_trend_quality_score(funding_cumulative_z: pd.Series, funding_trend_z: pd.Series) -> pd.Series:
    cumulative_neutrality = 1.0 - (funding_cumulative_z.abs().clip(upper=3.0) / 3.0)
    trend_neutrality = 1.0 - (funding_trend_z.abs().clip(upper=3.0) / 3.0)
    return ((0.65 * cumulative_neutrality + 0.35 * trend_neutrality) * 100.0).clip(0.0, 100.0)
