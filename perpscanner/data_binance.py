"""Binance USDT-M futures + spot market data fetchers."""

import threading

import numpy as np
import pandas as pd
import streamlit as st
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import (
    BINANCE_SPOT_BASE,
    BTC_OPTIONS_KLINE_LIMIT,
    BTC_SYMBOL,
    CACHE_TTL,
    CANDLE_LIMIT,
    EMA_SLOW,
    FUNDING_LIMIT,
    HTF_DAILY_LIMIT,
    HTF_DAILY_OI_LIMIT,
    INTERVAL,
    LTF_CACHE_TTL,
    LTF_INTERVALS,
    LTF_KLINE_LIMITS,
    LTF_OI_LIMIT,
    MAX_WORKERS,
    OI_PERIOD,
    OI_TREND_LOOKBACK,
    PREMIUM_HISTORY_MAX_AGE_S,
    PREMIUM_ROC_MIN_AGE_S,
    WS_LTF_ENABLED,
)
from .net import _get_json, _get_json_url
from .utils import _drop_unclosed_by_close_ts, _safe_float, _utc_now_naive
from .ws_feed import WSKlineFeed, get_shared_feed


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_usdt_perpetuals() -> list[str]:
    payload = _get_json("/fapi/v1/exchangeInfo")
    return sorted(
        [
            s["symbol"]
            for s in payload["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["contractType"] == "PERPETUAL"
            and s["status"] == "TRADING"
        ]
    )
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_ticker_stats() -> dict[str, dict[str, float]]:
    raw = _get_json("/fapi/v1/ticker/24hr", timeout=20)
    out = {}
    for item in raw:
        symbol = str(item.get("symbol", ""))
        if not symbol.endswith("USDT"):
            continue
        out[symbol] = {
            "quote_volume_24h": float(item.get("quoteVolume", 0.0) or 0.0),
            "trades_24h": float(item.get("count", 0.0) or 0.0),
            "price_change_pct_24h": float(item.get("priceChangePercent", 0.0) or 0.0),
        }
    return out
def _fetch_klines_interval(symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    raw = _get_json("/fapi/v1/klines", params=params, timeout=12)
    if not isinstance(raw, list) or len(raw) < EMA_SLOW + 5:
        return None

    df = pd.DataFrame(
        raw,
        columns=[
            "ts",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "close_ts",
            "quote_vol",
            "trades",
            "tb_base",
            "tb_quote",
            "_",
        ],
    )
    for col in ("open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"):
        df[col] = df[col].astype(float)

    df = _drop_unclosed_by_close_ts(df)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")[["open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"]]
def _fetch_klines(symbol: str) -> Optional[pd.DataFrame]:
    return _fetch_klines_interval(symbol, INTERVAL, CANDLE_LIMIT)
def _fetch_spot_klines_interval(symbol: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    raw = _get_json_url(f"{BINANCE_SPOT_BASE}/api/v3/klines", params=params, timeout=12)
    if not isinstance(raw, list) or len(raw) < EMA_SLOW + 5:
        return None

    df = pd.DataFrame(
        raw,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "close_time",
            "quote_vol",
            "trades",
            "tb_base",
            "tb_quote",
            "ignore",
        ],
    )
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms")
    for col in ("open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"):
        df[col] = df[col].astype(float)
    df = _drop_unclosed_by_close_ts(df, close_col="close_time")
    return df.set_index("ts")[["open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"]]
def _fetch_ltf_spot_symbol_context(symbol: str) -> tuple[str, dict[str, pd.DataFrame]]:
    klines: dict[str, pd.DataFrame] = {}
    for interval in LTF_INTERVALS:
        try:
            df = _fetch_spot_klines_interval(symbol, interval, LTF_KLINE_LIMITS[interval])
            if df is not None and not df.empty:
                klines[interval] = df
        except Exception:
            continue
    return symbol, klines
@st.cache_data(ttl=LTF_CACHE_TTL, show_spinner=False)
def fetch_ltf_spot_contexts(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_ltf_spot_symbol_context, symbol): symbol for symbol in symbols}
        for fut in as_completed(futures):
            symbol, klines = fut.result()
            if klines:
                out[symbol] = {"klines": klines}
    return out
def _fetch_open_interest_hist_frame(symbol: str, period: str, limit: int) -> pd.DataFrame:
    params = {"symbol": symbol, "period": period, "limit": limit}
    raw = _get_json("/futures/data/openInterestHist", params=params, timeout=12)
    if not isinstance(raw, list) or len(raw) < 2:
        return pd.DataFrame(columns=["ts", "oi_value"])

    df = pd.DataFrame(raw)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["oi_value"] = df["sumOpenInterestValue"].astype(float)
    return df.set_index("ts")[["oi_value"]].sort_index()
def _oi_context_from_frame(df: Optional[pd.DataFrame]) -> tuple[float, float, float]:
    """(latest OI value, 1-bar change, full-window trend) from an OI frame.

    The 1-bar change keeps the existing oi_change semantics; the trend is
    the signed slope over the whole fetched window (~OI_TREND_LOOKBACK
    hours), because a single bar of OI change is mostly noise while a
    multi-hour unwind is the actual downside tell.
    """
    if df is None or df.empty or len(df) < 2:
        return 0.0, 0.0, 0.0
    latest_value = float(df["oi_value"].iloc[-1])
    prev_value = float(df["oi_value"].iloc[-2])
    first_value = float(df["oi_value"].iloc[0])
    oi_change = (latest_value / prev_value) - 1.0 if prev_value > 0 else 0.0
    oi_trend = (latest_value / first_value) - 1.0 if first_value > 0 else 0.0
    return latest_value, oi_change, oi_trend
def _fetch_open_interest_hist(symbol: str) -> tuple[float, float, float]:
    return _oi_context_from_frame(_fetch_open_interest_hist_frame(symbol, OI_PERIOD, OI_TREND_LOOKBACK))
def _fetch_funding_history(symbol: str) -> tuple[float, float, float]:
    params = {"symbol": symbol, "limit": FUNDING_LIMIT}
    raw = _get_json("/fapi/v1/fundingRate", params=params, timeout=12)
    if not isinstance(raw, list) or not raw:
        return 0.0, 0.0, 0.0

    rates = [float(item.get("fundingRate", 0.0) or 0.0) for item in raw]
    latest = rates[-1]
    cumulative = float(sum(rates[-21:]))
    baseline = float(np.mean(rates[:-3])) if len(rates) > 3 else latest
    trend = latest - baseline
    return latest, cumulative, trend


# --- Premium (perp mark vs index) factor --------------------------------
# One bulk premiumIndex call covers every symbol, so the whole factor
# costs a single request per scan. The endpoint has no history, so
# rate-of-change comes from an in-process buffer of scan snapshots (same
# lifecycle as the ws feed: survives Streamlit reruns, resets on app
# restart and reads 0 until it warms - about two scans).
_PREMIUM_HISTORY: list[tuple[pd.Timestamp, dict[str, float]]] = []
_PREMIUM_HISTORY_LOCK = threading.Lock()


def _premium_snapshot_from_raw(raw: object) -> dict[str, float]:
    """Symbol -> premium in basis points, from the bulk premiumIndex payload."""
    out: dict[str, float] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", ""))
        index_price = _safe_float(item.get("indexPrice"))
        mark_price = _safe_float(item.get("markPrice"))
        if not symbol or index_price <= 0.0:
            continue
        out[symbol] = (mark_price - index_price) / index_price * 10000.0
    return out


def _premium_roc_from_history(
    history: list[tuple[pd.Timestamp, dict[str, float]]],
    snapshot: dict[str, float],
    now: pd.Timestamp,
    min_age_s: float = PREMIUM_ROC_MIN_AGE_S,
) -> dict[str, float]:
    """Premium drift in bp per hour vs the oldest usable buffered snapshot."""
    usable = [(ts, snap) for ts, snap in history if (now - ts).total_seconds() >= min_age_s]
    if not usable:
        return {symbol: 0.0 for symbol in snapshot}
    base_ts, base = usable[0]
    hours = max((now - base_ts).total_seconds() / 3600.0, 1e-9)
    return {
        symbol: ((bp - base[symbol]) / hours if symbol in base else 0.0)
        for symbol, bp in snapshot.items()
    }


def fetch_premium_index_all() -> dict[str, dict[str, float]]:
    """Per-symbol premium level and rate-of-change; {} on any failure.

    Uncached on purpose: the callers (build_metrics) are themselves
    st.cache_data-wrapped, so this runs once per scan and each run feeds
    the RoC buffer exactly once.
    """
    try:
        raw = _get_json("/fapi/v1/premiumIndex", timeout=20)
    except Exception:
        return {}
    snapshot = _premium_snapshot_from_raw(raw)
    if not snapshot:
        return {}
    now = _utc_now_naive()
    with _PREMIUM_HISTORY_LOCK:
        _PREMIUM_HISTORY.append((now, snapshot))
        cutoff = now - pd.Timedelta(seconds=PREMIUM_HISTORY_MAX_AGE_S)
        while _PREMIUM_HISTORY and _PREMIUM_HISTORY[0][0] < cutoff:
            _PREMIUM_HISTORY.pop(0)
        history = list(_PREMIUM_HISTORY)
    roc = _premium_roc_from_history(history, snapshot, now)
    return {
        symbol: {"premium_bp": bp, "premium_roc_bp_h": roc.get(symbol, 0.0)}
        for symbol, bp in snapshot.items()
    }
def _fetch_symbol_context(symbol: str) -> tuple[str, Optional[pd.DataFrame], float, float, float, float, float, float]:
    try:
        klines = _fetch_klines(symbol)
        oi_value, oi_change, oi_trend = _fetch_open_interest_hist(symbol)
        funding_rate, funding_cumulative, funding_trend = _fetch_funding_history(symbol)
        return symbol, klines, oi_value, oi_change, oi_trend, funding_rate, funding_cumulative, funding_trend
    except Exception:
        return symbol, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_symbol_contexts(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_symbol_context, symbol): symbol for symbol in symbols}
        for fut in as_completed(futures):
            symbol, klines, oi_value, oi_change, oi_trend, funding_rate, funding_cumulative, funding_trend = fut.result()
            if klines is not None:
                out[symbol] = {
                    "klines": klines,
                    "oi_value": oi_value,
                    "oi_change": oi_change,
                    "oi_trend": oi_trend,
                    "funding_rate": funding_rate,
                    "funding_cumulative_7d": funding_cumulative,
                    "funding_trend": funding_trend,
                }
    return out
def _fetch_ltf_symbol_context(
    symbol: str,
    feed: Optional[WSKlineFeed] = None,
) -> tuple[str, dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    klines: dict[str, pd.DataFrame] = {}
    oi_hist: dict[str, pd.DataFrame] = {}
    for interval in LTF_INTERVALS:
        try:
            df = feed.frame(symbol, interval) if feed is not None else None
            if df is None:
                # REST fallback (also the cold-start path); hand the result
                # to the feed as its baseline so ws bars stack on top and
                # the next scan is served without a REST call.
                df = _fetch_klines_interval(symbol, interval, LTF_KLINE_LIMITS[interval])
                if feed is not None and df is not None:
                    feed.seed(symbol, interval, df)
            if df is not None and not df.empty:
                klines[interval] = df
        except Exception:
            continue
        try:
            oi_hist[interval] = _fetch_open_interest_hist_frame(symbol, interval, LTF_OI_LIMIT)
        except Exception:
            oi_hist[interval] = pd.DataFrame()
    return symbol, klines, oi_hist
def _fetch_htf_daily_symbol_context(symbol: str) -> tuple[str, Optional[pd.DataFrame], pd.DataFrame, Optional[pd.DataFrame]]:
    daily = None
    spot_daily = None
    daily_oi = pd.DataFrame()
    try:
        daily = _fetch_klines_interval(symbol, "1d", HTF_DAILY_LIMIT)
    except Exception:
        daily = None
    try:
        daily_oi = _fetch_open_interest_hist_frame(symbol, "1d", HTF_DAILY_OI_LIMIT)
    except Exception:
        daily_oi = pd.DataFrame()
    try:
        spot_daily = _fetch_spot_klines_interval(symbol, "1d", HTF_DAILY_LIMIT)
    except Exception:
        spot_daily = None
    return symbol, daily, daily_oi, spot_daily
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_htf_daily_contexts(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_htf_daily_symbol_context, symbol): symbol for symbol in symbols}
        for fut in as_completed(futures):
            symbol, daily, daily_oi, spot_daily = fut.result()
            if daily is not None and not daily.empty:
                out[symbol] = {"daily": daily, "daily_oi": daily_oi, "spot_daily": spot_daily}
    return out
@st.cache_data(ttl=LTF_CACHE_TTL, show_spinner=False)
def fetch_ltf_symbol_contexts(symbols: tuple[str, ...], use_ws: bool = WS_LTF_ENABLED) -> dict[str, dict[str, object]]:
    feed = get_shared_feed(symbols, LTF_INTERVALS) if use_ws else None
    out: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_ltf_symbol_context, symbol, feed): symbol for symbol in symbols}
        for fut in as_completed(futures):
            symbol, klines, oi_hist = fut.result()
            if klines:
                out[symbol] = {"klines": klines, "oi_hist": oi_hist}
    return out
_BTC_PERP_KLINE_COLUMNS = [
    "ts",
    "open",
    "high",
    "low",
    "close",
    "base_vol",
    "close_ts",
    "quote_vol",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


def _fetch_binance_btc_perp_klines(interval: str = "5m", limit: int = BTC_OPTIONS_KLINE_LIMIT) -> pd.DataFrame:
    # Paginated backwards from now (fapi caps a single request at 1500
    # bars) so anchor modes that need a week or a month of history - the
    # anchored-VWAP weekly/monthly opens - can actually reach their
    # anchor instead of silently degrading to a rolling window.
    chunks = []
    end_time = None
    remaining = limit
    while remaining > 0:
        chunk_limit = min(1500, remaining)
        params = {"symbol": BTC_SYMBOL, "interval": interval, "limit": chunk_limit}
        if end_time is not None:
            params["endTime"] = end_time
        raw = _get_json("/fapi/v1/klines", params=params, timeout=20)
        if not raw:
            break
        chunk = pd.DataFrame(raw, columns=_BTC_PERP_KLINE_COLUMNS)
        chunks.append(chunk)
        end_time = int(chunk.iloc[0]["ts"]) - 1
        remaining -= len(chunk)
        if len(chunk) < chunk_limit:
            break

    if not chunks:
        return pd.DataFrame(columns=[c for c in _BTC_PERP_KLINE_COLUMNS if c != "ignore"])

    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
    for col in ["open", "high", "low", "close", "base_vol", "quote_vol", "taker_buy_base", "taker_buy_quote"]:
        df[col] = df[col].astype(float)
    df = _drop_unclosed_by_close_ts(df)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)
    df["trades"] = df["trades"].astype(int)
    return df.tail(limit).reset_index(drop=True)
def _fetch_binance_btc_open_interest_hist(period: str = "5m", limit: int = 100) -> pd.DataFrame:
    raw = _get_json("/futures/data/openInterestHist", params={"symbol": BTC_SYMBOL, "period": period, "limit": limit}, timeout=20)
    df = pd.DataFrame(raw)
    if df.empty:
        return pd.DataFrame(columns=["ts", "oi_contracts", "oi_value"])
    df["ts"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True).dt.tz_localize(None)
    df["oi_contracts"] = df["sumOpenInterest"].astype(float)
    df["oi_value"] = df["sumOpenInterestValue"].astype(float)
    return df[["ts", "oi_contracts", "oi_value"]].sort_values("ts")
def _fetch_binance_btc_perp_snapshot() -> dict[str, float]:
    premium = _get_json("/fapi/v1/premiumIndex", params={"symbol": BTC_SYMBOL}, timeout=20)
    open_interest = _get_json("/fapi/v1/openInterest", params={"symbol": BTC_SYMBOL}, timeout=20)
    return {
        "mark_price": _safe_float(premium.get("markPrice")),
        "index_price": _safe_float(premium.get("indexPrice")),
        "last_funding_rate": _safe_float(premium.get("lastFundingRate")),
        "next_funding_time": _safe_float(premium.get("nextFundingTime")),
        "open_interest_contracts": _safe_float(open_interest.get("openInterest")),
    }
