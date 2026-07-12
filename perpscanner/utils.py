"""Small shared helpers: time, formatting, bar-closing filters."""

import numpy as np
import pandas as pd


def _classify_volume_temperature(zscore: float) -> str:
    if zscore >= 2.0:
        return "Overheating"
    if zscore >= 1.0:
        return "Heating"
    if zscore <= -1.0:
        return "Cooling"
    return "Neutral"
def _interval_to_timedelta(interval: str) -> pd.Timedelta:
    interval = interval.strip().lower()
    if interval.endswith("d"):
        return pd.Timedelta(days=int(interval[:-1]))
    if interval.endswith("h"):
        return pd.Timedelta(hours=int(interval[:-1]))
    if interval.endswith("m"):
        return pd.Timedelta(minutes=int(interval[:-1]))
    raise ValueError(f"Unsupported interval: {interval}")
def _epoch_ms_now() -> int:
    return int(pd.Timestamp.now(tz="UTC").value // 10**6)
def _drop_unclosed_by_close_ts(df: pd.DataFrame, close_col: str = "close_ts") -> pd.DataFrame:
    """Keep only bars whose exchange-reported close timestamp has passed.

    Signals must never be computed on the in-progress candle: its volume,
    range, and taker fields are partial, which depresses z-scores early in
    the bar and makes triggers flicker at rollover.
    """
    if df.empty or close_col not in df.columns:
        return df
    closed = pd.to_numeric(df[close_col], errors="coerce").fillna(0).astype(np.int64) <= _epoch_ms_now()
    return df[closed]
def _drop_unclosed_by_interval(df: pd.DataFrame, duration: pd.Timedelta, ts_col: str = "ts") -> pd.DataFrame:
    """Keep only bars whose open time + interval duration has fully elapsed."""
    if df.empty or ts_col not in df.columns:
        return df
    return df[df[ts_col] + duration <= _utc_now_naive()]
def _bybit_interval_to_timedelta(interval: str) -> pd.Timedelta:
    interval = str(interval).strip().upper()
    if interval == "D":
        return pd.Timedelta(days=1)
    if interval == "W":
        return pd.Timedelta(weeks=1)
    if interval == "M":
        return pd.Timedelta(days=30)
    return pd.Timedelta(minutes=int(interval))
def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default
def _utc_now_naive() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)
def _format_gex_billions(gex_millions: float, signed: bool = False) -> str:
    value = _safe_float(gex_millions) / 1000.0
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.2f}B"
def _format_human_count(value: float, signed: bool = False, decimals: int = 2) -> str:
    number = _safe_float(value)
    abs_number = abs(number)
    sign = ""
    if signed and number > 0:
        sign = "+"
    if abs_number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:.{decimals}f}B"
    if abs_number >= 1_000_000:
        return f"{sign}{number / 1_000_000:.{decimals}f}M"
    if abs_number >= 1_000:
        return f"{sign}{number / 1_000:.{decimals}f}K"
    return f"{sign}{number:,.0f}"
def _format_expiry_distance(hours_to_expiry: float) -> str:
    hours = max(_safe_float(hours_to_expiry), 0.0)
    if hours < 1.0:
        return "<1h"
    if hours < 36.0:
        return f"{hours:.0f}h"
    return f"{hours / 24.0:.1f}d"
def _display_expiry_label(expiry_label: object, hours_to_expiry: float) -> str:
    label = str(expiry_label or "n/a")
    return f"{label} ({_format_expiry_distance(hours_to_expiry)})"
