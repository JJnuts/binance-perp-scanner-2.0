"""Binance USDT-M futures + spot market data fetchers."""

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
    OI_LOOKBACK,
    OI_PERIOD,
    WS_LTF_ENABLED,
)
from .net import _get_json, _get_json_url
from .utils import _drop_unclosed_by_close_ts, _safe_float
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
def _fetch_open_interest_hist(symbol: str) -> tuple[float, float]:
    df = _fetch_open_interest_hist_frame(symbol, OI_PERIOD, OI_LOOKBACK)
    if df.empty or len(df) < 2:
        return 0.0, 0.0

    latest_value = float(df["oi_value"].iloc[-1])
    prev_value = float(df["oi_value"].iloc[-2])
    if prev_value <= 0:
        return latest_value, 0.0
    return latest_value, (latest_value / prev_value) - 1.0
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
def _fetch_symbol_context(symbol: str) -> tuple[str, Optional[pd.DataFrame], float, float, float, float, float]:
    try:
        klines = _fetch_klines(symbol)
        oi_value, oi_change = _fetch_open_interest_hist(symbol)
        funding_rate, funding_cumulative, funding_trend = _fetch_funding_history(symbol)
        return symbol, klines, oi_value, oi_change, funding_rate, funding_cumulative, funding_trend
    except Exception:
        return symbol, None, 0.0, 0.0, 0.0, 0.0, 0.0
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_symbol_contexts(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_symbol_context, symbol): symbol for symbol in symbols}
        for fut in as_completed(futures):
            symbol, klines, oi_value, oi_change, funding_rate, funding_cumulative, funding_trend = fut.result()
            if klines is not None:
                out[symbol] = {
                    "klines": klines,
                    "oi_value": oi_value,
                    "oi_change": oi_change,
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
def _fetch_binance_btc_perp_klines(interval: str = "5m", limit: int = BTC_OPTIONS_KLINE_LIMIT) -> pd.DataFrame:
    raw = _get_json("/fapi/v1/klines", params={"symbol": BTC_SYMBOL, "interval": interval, "limit": limit}, timeout=20)
    df = pd.DataFrame(
        raw,
        columns=[
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
        ],
    )
    for col in ["open", "high", "low", "close", "base_vol", "quote_vol", "taker_buy_base", "taker_buy_quote"]:
        df[col] = df[col].astype(float)
    df = _drop_unclosed_by_close_ts(df)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)
    df["trades"] = df["trades"].astype(int)
    return df
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
