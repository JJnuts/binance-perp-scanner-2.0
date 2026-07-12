"""Multi-venue BTC spot data, bubble frames, spot flow."""

import numpy as np
import pandas as pd
import streamlit as st
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import (
    BTC_BUBBLE_TIMEFRAMES,
    BUBBLE_SIZE_MAX,
    BUBBLE_SIZE_MIN,
    BUBBLE_SIZE_MULTIPLIER,
    CACHE_TTL,
    MAX_WORKERS,
)
from .net import _get_json_url
from .utils import (
    _bybit_interval_to_timedelta,
    _classify_volume_temperature,
    _drop_unclosed_by_close_ts,
    _drop_unclosed_by_interval,
    _interval_to_timedelta,
    _safe_float,
    _utc_now_naive,
)
from .data_equities import _fetch_btc_etf_tape


def _resample_spot_frame(df: pd.DataFrame, rule: Optional[str], source_name: str) -> pd.DataFrame:
    out = df.sort_values("ts").copy()
    agg_map = {"close": ("close", "last"), "quote_volume": ("quote_volume", "sum")}
    if "aggressive_buy_volume" in out.columns:
        agg_map["aggressive_buy_volume"] = ("aggressive_buy_volume", "sum")
    if "aggressive_sell_volume" in out.columns:
        agg_map["aggressive_sell_volume"] = ("aggressive_sell_volume", "sum")
    if rule:
        out = (
            out.set_index("ts")
            .resample(rule)
            .agg(**agg_map)
            .dropna()
            .reset_index()
        )
        # The final resampled bucket may span into the future (e.g. a 12h
        # bucket built from 4h bars); drop it until it is fully formed.
        out = _drop_unclosed_by_interval(out, pd.Timedelta(rule))
    out["source"] = source_name
    cols = ["ts", "close", "quote_volume", "source"]
    for col in ("aggressive_buy_volume", "aggressive_sell_volume"):
        if col in out.columns:
            cols.insert(-1, col)
    return out[cols].sort_values("ts")
def _classify_spot_flow(imbalance: float, has_flow: bool = True) -> str:
    if not has_flow or not np.isfinite(imbalance):
        return "Unknown"
    if imbalance >= 0.18:
        return "Strong Buy"
    if imbalance >= 0.05:
        return "Buy"
    if imbalance <= -0.18:
        return "Strong Sell"
    if imbalance <= -0.05:
        return "Sell"
    return "Neutral"
def _prepare_bubble_frame(df: pd.DataFrame, z_window: int) -> pd.DataFrame:
    out = df.sort_values("ts").copy()
    out["volume_z"] = (
        out["quote_volume"]
        .rolling(z_window, min_periods=max(10, z_window // 3))
        .apply(
            lambda values: 0.0
            if float(np.std(values[:-1])) < 1e-12
            else (values[-1] - float(np.mean(values[:-1]))) / float(np.std(values[:-1])),
            raw=True,
        )
    )
    out["volume_z"] = out["volume_z"].fillna(0.0)
    out["temperature"] = out["volume_z"].map(_classify_volume_temperature)
    has_flow = {"aggressive_buy_volume", "aggressive_sell_volume"}.issubset(out.columns)
    if has_flow:
        out["aggressive_buy_volume"] = pd.to_numeric(out["aggressive_buy_volume"], errors="coerce").fillna(0.0)
        out["aggressive_sell_volume"] = pd.to_numeric(out["aggressive_sell_volume"], errors="coerce").fillna(0.0)
        flow_total = (out["aggressive_buy_volume"] + out["aggressive_sell_volume"]).replace(0.0, np.nan)
        out["spot_delta"] = out["aggressive_buy_volume"] - out["aggressive_sell_volume"]
        out["spot_imbalance"] = (out["spot_delta"] / flow_total).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        out["spot_cvd"] = out["spot_delta"].cumsum()
        out["flow_state"] = out["spot_imbalance"].map(lambda value: _classify_spot_flow(float(value), True))
    else:
        out["aggressive_buy_volume"] = np.nan
        out["aggressive_sell_volume"] = np.nan
        out["spot_delta"] = np.nan
        out["spot_imbalance"] = np.nan
        out["spot_cvd"] = np.nan
        out["flow_state"] = "Unknown"
    scale_base = float(out["quote_volume"].median()) if not out.empty else 1.0
    scale_base = max(scale_base, 1.0)
    out["bubble_size"] = (
        np.sqrt(out["quote_volume"] / scale_base) * BUBBLE_SIZE_MULTIPLIER
    ).clip(lower=BUBBLE_SIZE_MIN, upper=BUBBLE_SIZE_MAX)
    return out
def _coinbase_premium_frame(binance_df: pd.DataFrame, coinbase_df: pd.DataFrame) -> pd.DataFrame:
    if binance_df.empty or coinbase_df.empty:
        return pd.DataFrame(columns=["ts", "binance_close", "coinbase_close", "coinbase_premium_bp"])
    left = binance_df[["ts", "close"]].rename(columns={"close": "binance_close"}).sort_values("ts")
    right = coinbase_df[["ts", "close"]].rename(columns={"close": "coinbase_close"}).sort_values("ts")
    left["ts"] = pd.to_datetime(left["ts"]).astype("datetime64[ns]")
    right["ts"] = pd.to_datetime(right["ts"]).astype("datetime64[ns]")
    merged = pd.merge_asof(left, right, on="ts", direction="nearest", tolerance=pd.Timedelta(hours=2))
    merged = merged.dropna(subset=["binance_close", "coinbase_close"])
    if merged.empty:
        return pd.DataFrame(columns=["ts", "binance_close", "coinbase_close", "coinbase_premium_bp"])
    merged["coinbase_premium_bp"] = (
        (merged["coinbase_close"] - merged["binance_close"])
        / merged["binance_close"].replace(0.0, np.nan)
        * 10000.0
    ).replace([np.inf, -np.inf], np.nan)
    return merged.dropna(subset=["coinbase_premium_bp"]).sort_values("ts")
def _spot_flow_summary(df: pd.DataFrame, premium_df: pd.DataFrame, etf_tape: dict[str, object]) -> dict[str, object]:
    if df.empty:
        return {"state": "Unavailable", "copy": "No spot-flow data is available for this view."}
    latest = df.iloc[-1]
    has_flow = str(latest.get("flow_state", "Unknown")) != "Unknown"
    imbalance = _safe_float(latest.get("spot_imbalance"), np.nan)
    cvd_delta = 0.0
    price_delta = 0.0
    if has_flow and len(df) >= 6:
        cvd_delta = _safe_float(df["spot_cvd"].iloc[-1] - df["spot_cvd"].iloc[-6])
        price_delta = _safe_float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1.0)

    premium_latest = np.nan
    premium_delta = np.nan
    if not premium_df.empty:
        premium_latest = _safe_float(premium_df["coinbase_premium_bp"].iloc[-1], np.nan)
        if len(premium_df) >= 6:
            premium_delta = _safe_float(
                premium_df["coinbase_premium_bp"].iloc[-1] - premium_df["coinbase_premium_bp"].iloc[-6],
                np.nan,
            )

    etf_summary = etf_tape.get("summary", {}) if isinstance(etf_tape, dict) else {}
    etf_ratio = _safe_float(etf_summary.get("latest_proxy_ratio"), np.nan)

    if has_flow and cvd_delta > 0 and abs(price_delta) < 0.01:
        state = "Accumulation"
        copy = "Spot CVD is rising while price is relatively contained, which points to quiet buyer absorption."
    elif has_flow and cvd_delta < 0 and abs(price_delta) < 0.01:
        state = "Distribution"
        copy = "Spot CVD is falling while price is holding up, which points to sellers distributing into visible demand."
    elif has_flow and imbalance >= 0.05 and cvd_delta > 0:
        state = "Aggressive buying"
        copy = "Aggressive spot buyers are leading the latest bar and CVD is confirming the push."
    elif has_flow and imbalance <= -0.05 and cvd_delta < 0:
        state = "Aggressive selling"
        copy = "Aggressive spot sellers are leading the latest bar and CVD is confirming the pressure."
    else:
        state = "Neutral"
        copy = "Spot aggression is not giving a clean one-sided read in the selected view."

    return {
        "state": state,
        "copy": copy,
        "latest_imbalance": imbalance,
        "cvd_delta": cvd_delta,
        "price_delta": price_delta,
        "premium_latest": premium_latest,
        "premium_delta": premium_delta,
        "etf_proxy_ratio": etf_ratio,
    }
def _fetch_binance_spot_btc(limit: int, interval: str) -> pd.DataFrame:
    interval_ms = int(_interval_to_timedelta(interval).total_seconds() * 1000)
    chunks = []
    end_time = None
    remaining = limit
    while remaining > 0:
        chunk_limit = min(1000, remaining)
        params = {"symbol": "BTCUSDT", "interval": interval, "limit": chunk_limit}
        if end_time is not None:
            params["endTime"] = end_time
        raw = _get_json_url("https://api.binance.com/api/v3/klines", params=params, timeout=20)
        if not raw:
            break
        chunk = pd.DataFrame(raw).iloc[:, [0, 4, 6, 7, 10]].copy()
        chunk.columns = ["ts", "close", "close_ts", "quote_volume", "aggressive_buy_volume"]
        chunks.append(chunk)
        first_open = int(chunk.iloc[0]["ts"])
        end_time = first_open - interval_ms
        remaining -= len(chunk)
        if len(chunk) < chunk_limit:
            break

    if not chunks:
        return pd.DataFrame(columns=["ts", "close", "quote_volume", "source"])

    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
    df = _drop_unclosed_by_close_ts(df).drop(columns=["close_ts"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df["aggressive_buy_volume"] = df["aggressive_buy_volume"].astype(float)
    df["aggressive_sell_volume"] = (df["quote_volume"] - df["aggressive_buy_volume"]).clip(lower=0.0)
    df["source"] = "Binance spot"
    return df[
        ["ts", "close", "quote_volume", "aggressive_buy_volume", "aggressive_sell_volume", "source"]
    ].tail(limit)
def _fetch_coinbase_spot_btc(limit: int, granularity: int, rule: Optional[str]) -> pd.DataFrame:
    end = _utc_now_naive().floor("h")
    frames = []
    chunk_points = 290
    remaining = limit + 8
    chunk_end = end
    while remaining > 0:
        points = min(chunk_points, remaining)
        chunk_start = chunk_end - pd.Timedelta(seconds=granularity * points)
        raw = _get_json_url(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            params={
                "granularity": granularity,
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if raw:
            frames.append(pd.DataFrame(raw, columns=["ts", "low", "high", "open", "close", "base_volume"]))
        chunk_end = chunk_start
        remaining -= points

    if not frames:
        return pd.DataFrame(columns=["ts", "close", "quote_volume", "source"])

    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts"])
    df["ts"] = pd.to_datetime(df["ts"], unit="s")
    df = _drop_unclosed_by_interval(df, pd.Timedelta(seconds=granularity))
    df["close"] = df["close"].astype(float)
    df["base_volume"] = df["base_volume"].astype(float)
    df["quote_volume"] = df["close"] * df["base_volume"]
    df = _resample_spot_frame(df[["ts", "close", "quote_volume"]], rule, "Coinbase spot")
    return df.tail(limit)
def _fetch_bybit_spot_btc(limit: int, interval: str, rule: Optional[str]) -> pd.DataFrame:
    raw = _get_json_url(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "spot", "symbol": "BTCUSDT", "interval": interval, "limit": min(limit, 1000)},
        timeout=20,
    )
    rows = raw.get("result", {}).get("list", [])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "base_volume", "quote_volume"])
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="ms")
    df = _drop_unclosed_by_interval(df, _bybit_interval_to_timedelta(interval))
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df = _resample_spot_frame(df[["ts", "close", "quote_volume"]], rule, "Bybit spot")
    return df.tail(limit)
def _fetch_okx_spot_btc(limit: int, bar: str, rule: Optional[str]) -> pd.DataFrame:
    raw = _get_json_url(
        "https://www.okx.com/api/v5/market/history-candles",
        params={"instId": "BTC-USDT", "bar": bar, "limit": min(limit, 300)},
        timeout=20,
    )
    rows = raw.get("data", [])
    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "base_volume", "volume_ccy", "quote_volume", "confirm"],
    )
    df = df[df["confirm"].astype(str) == "1"]
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="ms")
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df = _resample_spot_frame(df[["ts", "close", "quote_volume"]], rule, "OKX spot")
    return df.tail(limit)
def _fetch_kraken_spot_btc(limit: int, interval: int, rule: Optional[str]) -> pd.DataFrame:
    raw = _get_json_url(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": "XBTUSD", "interval": interval},
        timeout=20,
    )
    rows = raw.get("result", {}).get("XXBTZUSD", [])
    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "vwap", "base_volume", "count"],
    )
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="s")
    df = _drop_unclosed_by_interval(df, pd.Timedelta(minutes=int(interval)))
    df["close"] = df["close"].astype(float)
    df["vwap"] = df["vwap"].astype(float)
    df["base_volume"] = df["base_volume"].astype(float)
    df["quote_volume"] = df["vwap"] * df["base_volume"]
    df = _resample_spot_frame(df[["ts", "close", "quote_volume"]], rule, "Kraken spot")
    return df.tail(limit)
def _safe_fetch_spot_source(name: str, fetcher, *args) -> tuple[str, Optional[pd.DataFrame], Optional[str]]:
    try:
        df = fetcher(*args)
        if df.empty:
            return name, None, "Empty response"
        return name, df, None
    except Exception as exc:
        return name, None, str(exc)
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def build_bitcoin_bubble_data(lookback_days: int, timeframe_key: str) -> dict[str, object]:
    config = BTC_BUBBLE_TIMEFRAMES[timeframe_key]
    display_bars = int(np.ceil(lookback_days * config["bars_per_day"]))
    limit = display_bars + int(config["z_window"]) + 5
    source_limit = lambda key: int(np.ceil(limit * float(config.get(f"{key}_fetch_multiplier", 1))))
    fetchers = {
        "binance": (
            "Binance spot",
            _fetch_binance_spot_btc,
            source_limit("binance"),
            str(config["binance_interval"]),
        ),
        "coinbase": (
            "Coinbase spot",
            _fetch_coinbase_spot_btc,
            source_limit("coinbase"),
            int(config["coinbase_granularity"]),
            config["coinbase_rule"],
        ),
        "bybit": (
            "Bybit spot",
            _fetch_bybit_spot_btc,
            source_limit("bybit"),
            str(config["bybit_interval"]),
            config["bybit_rule"],
        ),
        "okx": (
            "OKX spot",
            _fetch_okx_spot_btc,
            source_limit("okx"),
            str(config["okx_bar"]),
            config["okx_rule"],
        ),
        "kraken": (
            "Kraken spot",
            _fetch_kraken_spot_btc,
            source_limit("kraken"),
            int(config["kraken_interval"]),
            config["kraken_rule"],
        ),
    }
    sources: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(len(fetchers), MAX_WORKERS)) as ex:
        futures = {
            ex.submit(_safe_fetch_spot_source, key, fetcher, *args): key
            for key, (_, fetcher, *args) in fetchers.items()
        }
        for fut in as_completed(futures):
            key = futures[fut]
            _, df, error = fut.result()
            if df is not None:
                prepared = _prepare_bubble_frame(df.tail(limit), int(config["z_window"]))
                sources[key] = prepared.tail(display_bars)
            elif error:
                errors[key] = error

    aggregated = pd.DataFrame()
    aggregate_keys = [key for key in ("binance", "coinbase", "bybit", "okx", "kraken") if key in sources]
    if aggregate_keys:
        frames = []
        for key in aggregate_keys:
            cols = ["ts", "close", "quote_volume"]
            for flow_col in ("aggressive_buy_volume", "aggressive_sell_volume"):
                if flow_col in sources[key].columns:
                    cols.append(flow_col)
            frames.append(sources[key][cols])
        merged = pd.concat(frames, ignore_index=True)
        agg_map = {"close": ("close", "mean"), "quote_volume": ("quote_volume", "sum")}
        if "aggressive_buy_volume" in merged.columns:
            agg_map["aggressive_buy_volume"] = ("aggressive_buy_volume", "sum")
        if "aggressive_sell_volume" in merged.columns:
            agg_map["aggressive_sell_volume"] = ("aggressive_sell_volume", "sum")
        aggregated = (
            merged.groupby("ts", as_index=False)
            .agg(**agg_map)
            .sort_values("ts")
        )
        aggregated["source"] = "Aggregated CEX spot"
        aggregated = _prepare_bubble_frame(aggregated.tail(limit), int(config["z_window"])).tail(display_bars)
    premium = _coinbase_premium_frame(sources.get("binance", pd.DataFrame()), sources.get("coinbase", pd.DataFrame()))
    etf_tape = _fetch_btc_etf_tape(min(max(lookback_days, 30), 365))

    return {
        "sources": sources,
        "aggregated": aggregated,
        "errors": errors,
        "aggregate_keys": aggregate_keys,
        "coinbase_premium": premium,
        "etf_tape": etf_tape,
    }
