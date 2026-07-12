"""Yahoo/EODHD equity data: IBIT context and ETF tape."""

import numpy as np
import pandas as pd
import streamlit as st
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import BTC_ETF_TICKERS, CACHE_TTL, EODHD_BASE, IBIT_SYMBOL, MAX_WORKERS
from .net import _get_json_url, _get_yahoo_chart
from .utils import _safe_float, _utc_now_naive


def _get_eodhd_api_token() -> str:
    token = os.getenv("EODHD_API_TOKEN", "").strip()
    if token:
        return token
    try:
        return str(st.secrets.get("EODHD_API_TOKEN", "")).strip()
    except Exception:
        return ""
def _fetch_eodhd_etf_history(ticker: str, lookback_days: int, api_token: str) -> pd.DataFrame:
    start = (_utc_now_naive() - pd.Timedelta(days=max(lookback_days + 10, 45))).strftime("%Y-%m-%d")
    raw = _get_json_url(
        f"{EODHD_BASE}/eod/{ticker}.US",
        params={"api_token": api_token, "fmt": "json", "period": "d", "from": start},
        timeout=20,
    )
    if not isinstance(raw, list) or not raw:
        return pd.DataFrame(columns=["date", "ticker", "close", "volume", "dollar_volume", "signed_dollar_volume"])
    df = pd.DataFrame(raw)
    if "date" not in df.columns or "close" not in df.columns or "volume" not in df.columns:
        return pd.DataFrame(columns=["date", "ticker", "close", "volume", "dollar_volume", "signed_dollar_volume"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    close_col = "adjusted_close" if "adjusted_close" in df.columns else "close"
    df["close"] = pd.to_numeric(df[close_col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    df["session_return"] = df["close"].pct_change().fillna(0.0)
    df["dollar_volume"] = df["close"] * df["volume"]
    df["signed_dollar_volume"] = df["dollar_volume"] * np.sign(df["session_return"])
    df["ticker"] = ticker
    return df[["date", "ticker", "close", "volume", "dollar_volume", "signed_dollar_volume", "session_return"]]
def _fetch_btc_etf_tape(lookback_days: int) -> dict[str, object]:
    token = _get_eodhd_api_token()
    if not token:
        return {
            "data": pd.DataFrame(),
            "summary": {},
            "error": "EODHD_API_TOKEN is not configured, so ETF tape is unavailable.",
        }
    frames = []
    errors = {}
    with ThreadPoolExecutor(max_workers=min(len(BTC_ETF_TICKERS), MAX_WORKERS)) as ex:
        futures = {
            ex.submit(_fetch_eodhd_etf_history, ticker, lookback_days, token): ticker
            for ticker in BTC_ETF_TICKERS
        }
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                df = fut.result()
                if df.empty:
                    errors[ticker] = "Empty response"
                else:
                    frames.append(df)
            except Exception as exc:
                errors[ticker] = str(exc)
    if not frames:
        return {"data": pd.DataFrame(), "summary": {}, "error": "No ETF tape data returned from EODHD.", "errors": errors}

    merged = pd.concat(frames, ignore_index=True)
    daily = (
        merged.groupby("date", as_index=False)
        .agg(
            etf_dollar_volume=("dollar_volume", "sum"),
            etf_signed_dollar_volume=("signed_dollar_volume", "sum"),
            etf_tickers=("ticker", "nunique"),
        )
        .sort_values("date")
    )
    daily["etf_flow_proxy_ratio"] = (
        daily["etf_signed_dollar_volume"] / daily["etf_dollar_volume"].replace(0.0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    latest = daily.iloc[-1]
    lookback = daily.tail(5)
    summary = {
        "latest_date": latest["date"],
        "latest_dollar_volume": float(latest["etf_dollar_volume"]),
        "latest_signed_proxy": float(latest["etf_signed_dollar_volume"]),
        "latest_proxy_ratio": float(latest["etf_flow_proxy_ratio"]),
        "five_day_signed_proxy": float(lookback["etf_signed_dollar_volume"].sum()),
        "five_day_dollar_volume": float(lookback["etf_dollar_volume"].sum()),
        "tickers": int(latest["etf_tickers"]),
    }
    return {"data": daily.tail(max(lookback_days, 30)), "summary": summary, "error": "", "errors": errors}
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _build_ibit_context() -> dict[str, object]:
    intraday = _get_yahoo_chart(IBIT_SYMBOL, "1d", "5m", timeout=20)
    daily = _get_yahoo_chart(IBIT_SYMBOL, "3mo", "1d", timeout=20)

    intraday_result = (((intraday or {}).get("chart") or {}).get("result") or [None])[0]
    daily_result = (((daily or {}).get("chart") or {}).get("result") or [None])[0]
    if intraday_result is None or daily_result is None:
        return {"error": "IBIT data is unavailable from Yahoo right now."}

    intraday_meta = intraday_result.get("meta", {}) or {}
    intraday_quote = (((intraday_result.get("indicators") or {}).get("quote") or [None])[0] or {})
    daily_quote = (((daily_result.get("indicators") or {}).get("quote") or [None])[0] or {})

    current_price = _safe_float(intraday_meta.get("regularMarketPrice"))
    previous_close = _safe_float(intraday_meta.get("chartPreviousClose")) or _safe_float(intraday_meta.get("previousClose"))
    intraday_volumes = [float(v) for v in (intraday_quote.get("volume") or []) if v is not None]
    session_volume = _safe_float(intraday_meta.get("regularMarketVolume"))
    if session_volume <= 0.0 and intraday_volumes:
        session_volume = float(sum(intraday_volumes))

    daily_volumes = [float(v) for v in (daily_quote.get("volume") or []) if v is not None and float(v) > 0]
    lookback = daily_volumes[-21:-1] if len(daily_volumes) >= 21 else daily_volumes[-20:]
    avg_20d_volume = float(np.mean(lookback)) if lookback else 0.0

    session_return = (current_price / previous_close - 1.0) if current_price > 0 and previous_close > 0 else 0.0
    volume_ratio = (session_volume / avg_20d_volume) if session_volume > 0 and avg_20d_volume > 0 else 0.0

    if session_volume <= 0:
        flow_state = "Inactive"
        flow_copy = "IBIT is not showing active session flow right now, so ETF tape is not adding much confirmation."
    elif volume_ratio >= 1.25 and session_return > 0.002:
        flow_state = "Supportive"
        flow_copy = "IBIT volume is running above normal and price is green, which supports the BTC move rather than arguing against it."
    elif volume_ratio >= 1.0 and session_return < -0.002:
        flow_state = "Weak"
        flow_copy = "IBIT volume is active but price is weak, which suggests ETF flow is not confirming BTC strength."
    elif volume_ratio >= 1.0:
        flow_state = "Active"
        flow_copy = "IBIT is seeing healthy participation, but the ETF tape is not giving a one-sided directional message yet."
    else:
        flow_state = "Neutral"
        flow_copy = "IBIT participation is below its usual pace, so ETF flow is not a strong confirmation signal yet."

    market_time = intraday_meta.get("regularMarketTime")
    market_ts = None
    if market_time:
        market_ts = pd.to_datetime(int(market_time), unit="s", utc=True).tz_localize(None)

    return {
        "price": current_price,
        "previous_close": previous_close,
        "session_return": session_return,
        "session_volume": session_volume,
        "avg_20d_volume": avg_20d_volume,
        "volume_ratio": volume_ratio,
        "flow_state": flow_state,
        "flow_copy": flow_copy,
        "market_ts": market_ts,
    }
