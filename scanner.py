"""
Binance USDT Perpetual Scanner

Scans active USDT-M perpetual futures on Binance Futures and ranks altcoins by
momentum, relative strength vs BTC, and overextension.
No API key required.
"""

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from streamlit_autorefresh import st_autorefresh
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore")


BINANCE_BASE = "https://fapi.binance.com"
INTERVAL = "1h"
CANDLE_LIMIT = 240
VWAP_FAST = 8
VWAP_SLOW = 24
VWAP_HTF = 120
EMA_FAST = 20
EMA_MID = 36
EMA_SLOW = 50
VOLUME_LOOKBACK = 30
OI_LOOKBACK = 3
OI_PERIOD = "1h"
FUNDING_LIMIT = 30
MAX_WORKERS = 20
REFRESH_MS = 5 * 60 * 1000
CACHE_TTL = 280
API_TIMEOUT = 15
BTC_SYMBOL = "BTCUSDT"
BTC_BUBBLE_LOOKBACK = 365
BTC_BUBBLE_Z_WINDOW = 30
SPOT_COLOR_MAP = {
    "Neutral": "#8a8f9c",
    "Cooling": "#3b82f6",
    "Heating": "#f472b6",
    "Overheating": "#ef4444",
}
APP_BG = "#0b100b"
APP_PANEL = "#111811"
APP_PANEL_SOFT = "#141d14"
APP_BORDER = "#273226"
APP_GRID = "rgba(113, 133, 105, 0.18)"
APP_TEXT = "#e4eadf"
APP_MUTED = "#8f9a8b"
APP_ACCENT = "#dfe7d8"
BUBBLE_SIZE_MULTIPLIER = 10.5
BUBBLE_SIZE_MIN = 4.0
BUBBLE_SIZE_MAX = 34.0


def _make_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "binance-perp-scanner/2.0"})
    return session


_SESSION = _make_session()


def _get_json(path: str, params: Optional[dict] = None, timeout: int = API_TIMEOUT):
    response = _SESSION.get(f"{BINANCE_BASE}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _get_json_url(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = API_TIMEOUT,
):
    response = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _inject_app_styles():
    st.markdown(
        f"""
        <style>
            :root {{
                --app-bg: {APP_BG};
                --app-panel: {APP_PANEL};
                --app-panel-soft: {APP_PANEL_SOFT};
                --app-border: {APP_BORDER};
                --app-grid: {APP_GRID};
                --app-text: {APP_TEXT};
                --app-muted: {APP_MUTED};
                --app-accent: {APP_ACCENT};
            }}

            .stApp {{
                background:
                    radial-gradient(circle at top left, rgba(34, 49, 30, 0.22) 0%, rgba(11, 16, 11, 0) 28%),
                    linear-gradient(180deg, #0c130c 0%, #0b100b 100%);
                color: var(--app-text);
            }}

            [data-testid="stAppViewContainer"] {{
                background: transparent;
            }}

            [data-testid="stHeader"] {{
                background: rgba(11, 16, 11, 0.72);
                border-bottom: 1px solid rgba(39, 50, 38, 0.6);
            }}

            [data-testid="stSidebar"] {{
                background: linear-gradient(180deg, #101710 0%, #0d140d 100%);
                border-right: 1px solid rgba(39, 50, 38, 0.75);
            }}

            [data-testid="stSidebar"] * {{
                color: var(--app-text);
            }}

            .block-container {{
                padding-top: 2.2rem;
                padding-bottom: 2.5rem;
                max-width: 1500px;
            }}

            h1, h2, h3 {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                letter-spacing: 0.18em;
                font-weight: 700;
                text-transform: uppercase;
            }}

            p, label, .stCaption, .stMarkdown, .stText {{
                color: var(--app-text);
            }}

            [data-testid="stMetric"] {{
                background: rgba(17, 24, 17, 0.92);
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 4px;
                padding: 0.9rem 1rem;
                box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
            }}

            [data-testid="stMetricLabel"] {{
                color: var(--app-muted);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                font-size: 0.68rem;
            }}

            [data-testid="stMetricValue"] {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-weight: 600;
            }}

            [data-testid="stRadio"] > div,
            [data-testid="stNumberInputContainer"],
            [data-testid="stSlider"] {{
                background: rgba(17, 24, 17, 0.88);
                border: 1px solid rgba(39, 50, 38, 0.75);
                border-radius: 4px;
                padding: 0.45rem 0.55rem;
            }}

            .stButton > button,
            [data-baseweb="select"] > div,
            [data-baseweb="input"] > div {{
                background: rgba(17, 24, 17, 0.9);
                border: 1px solid rgba(39, 50, 38, 0.85);
                color: var(--app-text);
                border-radius: 4px;
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
            }}

            .stButton > button:hover {{
                border-color: rgba(94, 111, 87, 0.9);
                color: var(--app-accent);
            }}

            [data-testid="stDataFrame"],
            [data-testid="stTable"] {{
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 4px;
                overflow: hidden;
                background: rgba(17, 24, 17, 0.84);
            }}

            [data-testid="stExpander"] {{
                border: 1px solid rgba(39, 50, 38, 0.78);
                border-radius: 4px;
                background: rgba(17, 24, 17, 0.6);
            }}

            hr {{
                border-color: rgba(39, 50, 38, 0.75);
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def _fetch_klines(symbol: str) -> Optional[pd.DataFrame]:
    params = {"symbol": symbol, "interval": INTERVAL, "limit": CANDLE_LIMIT}
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

    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")[["open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"]]


def _fetch_open_interest_hist(symbol: str) -> tuple[float, float]:
    params = {"symbol": symbol, "period": OI_PERIOD, "limit": OI_LOOKBACK}
    raw = _get_json("/futures/data/openInterestHist", params=params, timeout=12)
    if not isinstance(raw, list) or len(raw) < 2:
        return 0.0, 0.0

    latest = raw[-1]
    previous = raw[-2]
    latest_value = float(latest.get("sumOpenInterestValue", 0.0) or 0.0)
    prev_value = float(previous.get("sumOpenInterestValue", 0.0) or 0.0)
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


def _beta_adjusted_alpha(asset_close: pd.Series, btc_close: pd.Series, n: int, beta_lookback: int = 72) -> float:
    merged = pd.concat(
        [
            asset_close.pct_change().rename("asset"),
            btc_close.pct_change().rename("btc"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    if len(merged) < 12:
        beta = 1.0
    else:
        window = merged.tail(min(beta_lookback, len(merged)))
        btc_var = float(window["btc"].var())
        beta = 1.0 if btc_var < 1e-12 else float(window["asset"].cov(window["btc"]) / btc_var)

    return _return_n(asset_close, n) - beta * _return_n(btc_close, n)


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


def _classify_volume_temperature(zscore: float) -> str:
    if zscore >= 2.0:
        return "Overheating"
    if zscore >= 1.0:
        return "Heating"
    if zscore <= -1.0:
        return "Cooling"
    return "Neutral"


def _prepare_bubble_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values("ts").copy()
    out["volume_z"] = (
        out["quote_volume"]
        .rolling(BTC_BUBBLE_Z_WINDOW, min_periods=10)
        .apply(
            lambda values: 0.0
            if float(np.std(values[:-1])) < 1e-12
            else (values[-1] - float(np.mean(values[:-1]))) / float(np.std(values[:-1])),
            raw=True,
        )
    )
    out["volume_z"] = out["volume_z"].fillna(0.0)
    out["temperature"] = out["volume_z"].map(_classify_volume_temperature)
    scale_base = float(out["quote_volume"].median()) if not out.empty else 1.0
    scale_base = max(scale_base, 1.0)
    out["bubble_size"] = (
        np.sqrt(out["quote_volume"] / scale_base) * BUBBLE_SIZE_MULTIPLIER
    ).clip(lower=BUBBLE_SIZE_MIN, upper=BUBBLE_SIZE_MAX)
    return out


def _fetch_binance_spot_btc_daily(limit: int) -> pd.DataFrame:
    raw = _get_json_url(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1d", "limit": limit},
        timeout=20,
    )
    df = pd.DataFrame(raw)
    df = df.iloc[:, [0, 4, 7]].copy()
    df.columns = ["ts", "close", "quote_volume"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df["source"] = "Binance spot"
    return df


def _fetch_coinbase_spot_btc_daily(limit: int) -> pd.DataFrame:
    end = pd.Timestamp.utcnow().floor("D")
    frames = []
    chunk_days = 290
    remaining = limit + 5
    chunk_end = end
    while remaining > 0:
        chunk_start = chunk_end - pd.Timedelta(days=min(chunk_days, remaining))
        raw = _get_json_url(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            params={
                "granularity": 86400,
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if raw:
            frames.append(pd.DataFrame(raw, columns=["ts", "low", "high", "open", "close", "base_volume"]))
        chunk_end = chunk_start
        remaining -= chunk_days

    if not frames:
        return pd.DataFrame(columns=["ts", "close", "quote_volume", "source"])

    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts"])
    df["ts"] = pd.to_datetime(df["ts"], unit="s")
    df["close"] = df["close"].astype(float)
    df["base_volume"] = df["base_volume"].astype(float)
    df["quote_volume"] = df["close"] * df["base_volume"]
    df["source"] = "Coinbase spot"
    return df[["ts", "close", "quote_volume", "source"]].sort_values("ts").tail(limit)


def _fetch_bybit_spot_btc_daily(limit: int) -> pd.DataFrame:
    raw = _get_json_url(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "spot", "symbol": "BTCUSDT", "interval": "D", "limit": limit},
        timeout=20,
    )
    rows = raw.get("result", {}).get("list", [])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "base_volume", "quote_volume"])
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="ms")
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df["source"] = "Bybit spot"
    return df[["ts", "close", "quote_volume", "source"]].sort_values("ts")


def _fetch_okx_spot_btc_daily(limit: int) -> pd.DataFrame:
    raw = _get_json_url(
        "https://www.okx.com/api/v5/market/history-candles",
        params={"instId": "BTC-USDT", "bar": "1Dutc", "limit": limit},
        timeout=20,
    )
    rows = raw.get("data", [])
    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "base_volume", "volume_ccy", "quote_volume", "confirm"],
    )
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="ms")
    df["close"] = df["close"].astype(float)
    df["quote_volume"] = df["quote_volume"].astype(float)
    df["source"] = "OKX spot"
    return df[["ts", "close", "quote_volume", "source"]].sort_values("ts")


def _fetch_kraken_spot_btc_daily(limit: int) -> pd.DataFrame:
    raw = _get_json_url(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": "XBTUSD", "interval": 1440},
        timeout=20,
    )
    rows = raw.get("result", {}).get("XXBTZUSD", [])
    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "vwap", "base_volume", "count"],
    )
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="s")
    df["close"] = df["close"].astype(float)
    df["vwap"] = df["vwap"].astype(float)
    df["base_volume"] = df["base_volume"].astype(float)
    df["quote_volume"] = df["vwap"] * df["base_volume"]
    df["source"] = "Kraken spot"
    return df[["ts", "close", "quote_volume", "source"]].sort_values("ts").tail(limit)


def _safe_fetch_spot_source(name: str, fetcher, limit: int) -> tuple[str, Optional[pd.DataFrame], Optional[str]]:
    try:
        df = fetcher(limit)
        if df.empty:
            return name, None, "Empty response"
        return name, df.tail(limit), None
    except Exception as exc:
        return name, None, str(exc)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def build_bitcoin_bubble_data(limit: int) -> dict[str, object]:
    fetchers = {
        "binance": ("Binance spot", _fetch_binance_spot_btc_daily),
        "coinbase": ("Coinbase spot", _fetch_coinbase_spot_btc_daily),
        "bybit": ("Bybit spot", _fetch_bybit_spot_btc_daily),
        "okx": ("OKX spot", _fetch_okx_spot_btc_daily),
        "kraken": ("Kraken spot", _fetch_kraken_spot_btc_daily),
    }
    sources: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(len(fetchers), MAX_WORKERS)) as ex:
        futures = {
            ex.submit(_safe_fetch_spot_source, key, fetcher, limit): key
            for key, (_, fetcher) in fetchers.items()
        }
        for fut in as_completed(futures):
            key = futures[fut]
            _, df, error = fut.result()
            if df is not None:
                sources[key] = _prepare_bubble_frame(df)
            elif error:
                errors[key] = error

    aggregated = pd.DataFrame()
    aggregate_keys = [key for key in ("binance", "coinbase", "bybit", "okx", "kraken") if key in sources]
    if aggregate_keys:
        frames = [sources[key][["ts", "close", "quote_volume"]] for key in aggregate_keys]
        merged = pd.concat(frames, ignore_index=True)
        aggregated = (
            merged.groupby("ts", as_index=False)
            .agg(close=("close", "mean"), quote_volume=("quote_volume", "sum"))
            .sort_values("ts")
        )
        aggregated["source"] = "Aggregated CEX spot"
        aggregated = _prepare_bubble_frame(aggregated)

    return {
        "sources": sources,
        "aggregated": aggregated,
        "errors": errors,
        "aggregate_keys": aggregate_keys,
    }


def _build_bitcoin_bubble_chart(df: pd.DataFrame, title: str):
    fig = px.scatter(
        df,
        x="ts",
        y="close",
        size="bubble_size",
        size_max=int(BUBBLE_SIZE_MAX),
        color="temperature",
        color_discrete_map=SPOT_COLOR_MAP,
        hover_name="source",
        hover_data={
            "ts": "|%Y-%m-%d",
            "close": ":,.2f",
            "quote_volume": ":,.0f",
            "volume_z": ":.2f",
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
        legend_title="Volume State",
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


def _candidate_symbols(
    symbols: tuple[str, ...],
    ticker_stats: dict[str, dict[str, float]],
    min_quote_volume: float,
    min_trades: float,
) -> tuple[str, ...]:
    selected = [BTC_SYMBOL]
    for symbol in symbols:
        if symbol == BTC_SYMBOL:
            continue
        stats = ticker_stats.get(symbol, {})
        if stats.get("quote_volume_24h", 0.0) < min_quote_volume:
            continue
        if stats.get("trades_24h", 0.0) < min_trades:
            continue
        selected.append(symbol)
    return tuple(sorted(set(selected)))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def build_metrics(
    symbols: tuple[str, ...],
    min_quote_volume: float,
    min_trades: float,
    min_oi_value: float,
) -> pd.DataFrame:
    ticker_stats = fetch_ticker_stats()
    candidates = _candidate_symbols(symbols, ticker_stats, min_quote_volume, min_trades)
    contexts = fetch_symbol_contexts(candidates)
    btc_context = contexts.get(BTC_SYMBOL)
    if not btc_context:
        return pd.DataFrame()

    btc_close = btc_context["klines"]["close"]  # type: ignore[index]
    btc_ret_1h = _return_n(btc_close, 1)
    btc_ret_4h = _return_n(btc_close, 4)
    btc_ret_24h = _return_n(btc_close, 24)
    btc_ret_72h = _return_n(btc_close, 72)
    btc_ret_168h = _return_n(btc_close, 168)

    rows = []
    for symbol in candidates:
        if symbol == BTC_SYMBOL:
            continue
        context = contexts.get(symbol)
        if not context:
            continue

        df = context["klines"]
        oi_value = float(context["oi_value"])
        oi_change = float(context["oi_change"])
        funding_rate = float(context["funding_rate"])
        funding_cumulative_7d = float(context["funding_cumulative_7d"])
        funding_trend = float(context["funding_trend"])
        if oi_value < min_oi_value:
            continue

        close = df["close"]
        price = float(close.iloc[-1])
        ema20 = _ema(close, EMA_FAST)
        ema36 = _ema(close, EMA_MID)
        ema50 = _ema(close, EMA_SLOW)

        ret_1h = _return_n(close, 1)
        ret_4h = _return_n(close, 4)
        ret_24h = _return_n(close, 24)
        ret_72h = _return_n(close, 72)
        ret_168h = _return_n(close, 168)
        rs_1h = ret_1h - btc_ret_1h
        rs_4h = ret_4h - btc_ret_4h
        rs_24h = ret_24h - btc_ret_24h
        rs_72h = ret_72h - btc_ret_72h
        rs_168h = ret_168h - btc_ret_168h
        alpha_1h = _beta_adjusted_alpha(close, btc_close, 1, beta_lookback=72)
        alpha_4h = _beta_adjusted_alpha(close, btc_close, 4, beta_lookback=72)
        alpha_24h = _beta_adjusted_alpha(close, btc_close, 24, beta_lookback=96)
        alpha_72h = _beta_adjusted_alpha(close, btc_close, 72, beta_lookback=168)
        alpha_168h = _beta_adjusted_alpha(close, btc_close, 168, beta_lookback=216)
        vol_adj_4h = _vol_adjusted_return(close, 4)
        vol_adj_24h = _vol_adjusted_return(close, 24)
        vol_adj_72h = _vol_adjusted_return(close, 72)
        vol_adj_168h = _vol_adjusted_return(close, 168)

        volume_ratio_1h = _volume_ratio(df["quote_vol"])
        volume_z_1h = _volume_zscore(df["quote_vol"])
        z_fast = _vwap_zscore(df, VWAP_FAST)
        z_slow = _vwap_zscore(df, VWAP_SLOW)
        z_htf = _vwap_zscore(df, VWAP_HTF)

        dist_ema20 = (price / ema20 - 1.0) if ema20 else 0.0
        dist_ema36 = (price / ema36 - 1.0) if ema36 else 0.0
        dist_ema50 = (price / ema50 - 1.0) if ema50 else 0.0
        ema_alignment = (
            float(price > ema20)
            + float(ema20 > ema36)
            + float(ema36 > ema50)
        ) / 3.0

        ltf_alpha_raw = 0.20 * alpha_1h + 0.45 * alpha_4h + 0.35 * alpha_24h
        htf_alpha_raw = 0.50 * alpha_24h + 0.30 * alpha_72h + 0.20 * alpha_168h
        vol_adjusted_raw = 0.35 * vol_adj_24h + 0.35 * vol_adj_72h + 0.30 * vol_adj_168h
        rs_raw = 0.20 * rs_1h + 0.45 * rs_4h + 0.35 * rs_24h
        htf_rs_raw = 0.50 * rs_24h + 0.30 * rs_72h + 0.20 * rs_168h
        volume_raw = 0.60 * _tanh_scale(volume_ratio_1h - 1.0, 1.25) + 0.40 * _tanh_scale(volume_z_1h, 0.40)
        trend_raw = (
            0.30 * _tanh_scale(dist_ema20, 35.0)
            + 0.20 * _tanh_scale(dist_ema36, 30.0)
            + 0.15 * _tanh_scale(dist_ema50, 25.0)
            + 0.20 * ema_alignment
            + 0.15 * _tanh_scale(max(z_fast, 0.0), 0.60)
        )
        htf_trend_raw = (
            0.30 * _tanh_scale(dist_ema36, 30.0)
            + 0.25 * _tanh_scale(dist_ema50, 25.0)
            + 0.25 * ema_alignment
            + 0.20 * _tanh_scale(max(z_htf, 0.0), 0.35)
        )
        oi_raw = _tanh_scale(oi_change, 8.0)
        overextension_raw = (
            0.45 * max(z_slow, 0.0)
            + 0.25 * max(dist_ema20, 0.0) * 40.0
            + 0.15 * max(dist_ema36, 0.0) * 35.0
            + 0.15 * max(ret_1h, 0.0) * 30.0
        )

        stats = ticker_stats.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "price": price,
                "quote_volume_24h": float(stats.get("quote_volume_24h", 0.0)),
                "trades_24h": float(stats.get("trades_24h", 0.0)),
                "funding_rate": funding_rate,
                "funding_cumulative_7d": funding_cumulative_7d,
                "funding_trend": funding_trend,
                "oi_value": oi_value,
                "oi_change_1h": oi_change,
                "ret_1h": ret_1h,
                "ret_4h": ret_4h,
                "ret_24h": ret_24h,
                "ret_72h": ret_72h,
                "ret_168h": ret_168h,
                "rs_1h": rs_1h,
                "rs_4h": rs_4h,
                "rs_24h": rs_24h,
                "rs_72h": rs_72h,
                "rs_168h": rs_168h,
                "alpha_1h": alpha_1h,
                "alpha_4h": alpha_4h,
                "alpha_24h": alpha_24h,
                "alpha_72h": alpha_72h,
                "alpha_168h": alpha_168h,
                "vol_adj_4h": vol_adj_4h,
                "vol_adj_24h": vol_adj_24h,
                "vol_adj_72h": vol_adj_72h,
                "vol_adj_168h": vol_adj_168h,
                "volume_ratio_1h": volume_ratio_1h,
                "volume_z_1h": volume_z_1h,
                "ema20": ema20,
                "ema36": ema36,
                "ema50": ema50,
                "ema_alignment": ema_alignment,
                "dist_ema20": dist_ema20,
                "dist_ema36": dist_ema36,
                "dist_ema50": dist_ema50,
                "z_fast": z_fast,
                "z_slow": z_slow,
                "z_htf": z_htf,
                "ltf_alpha_raw": ltf_alpha_raw,
                "htf_alpha_raw": htf_alpha_raw,
                "vol_adjusted_raw": vol_adjusted_raw,
                "rs_raw": rs_raw,
                "htf_rs_raw": htf_rs_raw,
                "volume_raw": volume_raw,
                "trend_raw": trend_raw,
                "htf_trend_raw": htf_trend_raw,
                "oi_raw": oi_raw,
                "overextension_raw": overextension_raw,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    funding_std = float(out["funding_rate"].std())
    out["funding_z"] = (out["funding_rate"] - out["funding_rate"].mean()) / (funding_std + 1e-10)
    funding_cumulative_std = float(out["funding_cumulative_7d"].std())
    out["funding_cumulative_z"] = (
        out["funding_cumulative_7d"] - out["funding_cumulative_7d"].mean()
    ) / (funding_cumulative_std + 1e-10)
    funding_trend_std = float(out["funding_trend"].std())
    out["funding_trend_z"] = (out["funding_trend"] - out["funding_trend"].mean()) / (funding_trend_std + 1e-10)
    out["alpha_score"] = _percentile_score(out["ltf_alpha_raw"])
    out["htf_alpha_score"] = _percentile_score(out["htf_alpha_raw"])
    out["vol_adjusted_score"] = _percentile_score(out["vol_adjusted_raw"])
    out["relative_strength_score"] = _percentile_score(out["rs_raw"])
    out["htf_relative_strength_score"] = _percentile_score(out["htf_rs_raw"])
    out["volume_score"] = _percentile_score(out["volume_raw"])
    out["trend_score"] = _percentile_score(out["trend_raw"])
    out["htf_trend_score"] = _percentile_score(out["htf_trend_raw"])
    out["oi_score"] = _percentile_score(out["oi_raw"])
    out["funding_quality_score"] = _funding_quality_score(out["funding_z"], out["funding_rate"])
    out["funding_trend_quality_score"] = _funding_trend_quality_score(
        out["funding_cumulative_z"], out["funding_trend_z"]
    )
    out["momentum_score"] = (
        0.25 * out["alpha_score"]
        + 0.15 * out["relative_strength_score"]
        + 0.25 * out["volume_score"]
        + 0.20 * out["trend_score"]
        + 0.10 * out["oi_score"]
        + 0.05 * out["funding_quality_score"]
    )
    out["htf_momentum_score"] = (
        0.30 * out["htf_alpha_score"]
        + 0.20 * out["vol_adjusted_score"]
        + 0.20 * out["htf_relative_strength_score"]
        + 0.15 * out["htf_trend_score"]
        + 0.10 * out["oi_score"]
        + 0.05 * out["funding_trend_quality_score"]
    )
    out["overextension_score"] = _percentile_score(out["overextension_raw"])
    out["setup_score"] = (out["momentum_score"] - 0.45 * out["overextension_score"]).clip(lower=0.0)
    out["htf_setup_score"] = (out["htf_momentum_score"] - 0.40 * out["overextension_score"]).clip(lower=0.0)
    return out.sort_values(["momentum_score", "setup_score"], ascending=False).reset_index(drop=True)


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


def _show_table(df: pd.DataFrame):
    cols = [
        "symbol",
        "momentum_score",
        "htf_momentum_score",
        "overextension_score",
        "setup_score",
        "htf_setup_score",
        "alpha_score",
        "htf_alpha_score",
        "vol_adjusted_score",
        "relative_strength_score",
        "htf_relative_strength_score",
        "volume_score",
        "trend_score",
        "htf_trend_score",
        "oi_score",
        "funding_quality_score",
        "funding_trend_quality_score",
        "rs_1h",
        "rs_4h",
        "rs_24h",
        "rs_72h",
        "alpha_4h",
        "alpha_24h",
        "alpha_72h",
        "vol_adj_24h",
        "vol_adj_72h",
        "volume_ratio_1h",
        "volume_z_1h",
        "oi_change_1h",
        "funding_rate",
        "funding_cumulative_7d",
        "funding_trend",
        "quote_volume_24h",
        "trades_24h",
        "oi_value",
    ]
    if df.empty:
        st.info("No assets match the current filters.")
        return

    table_df = df[cols].copy()
    percent_cols = [
        "rs_1h",
        "rs_4h",
        "rs_24h",
        "rs_72h",
        "alpha_4h",
        "alpha_24h",
        "alpha_72h",
        "oi_change_1h",
    ]
    for col in percent_cols:
        table_df[col] = table_df[col].map(lambda value: f"{value:.2%}")

    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "momentum_score": st.column_config.NumberColumn("Momentum", format="%.1f"),
            "htf_momentum_score": st.column_config.NumberColumn("HTF Momentum", format="%.1f"),
            "overextension_score": st.column_config.NumberColumn("Overext", format="%.1f"),
            "setup_score": st.column_config.NumberColumn("Setup", format="%.1f"),
            "htf_setup_score": st.column_config.NumberColumn("HTF Setup", format="%.1f"),
            "alpha_score": st.column_config.NumberColumn("Alpha Score", format="%.1f"),
            "htf_alpha_score": st.column_config.NumberColumn("HTF Alpha", format="%.1f"),
            "vol_adjusted_score": st.column_config.NumberColumn("Vol-Adj", format="%.1f"),
            "relative_strength_score": st.column_config.NumberColumn("RS Score", format="%.1f"),
            "htf_relative_strength_score": st.column_config.NumberColumn("HTF RS", format="%.1f"),
            "volume_score": st.column_config.NumberColumn("Vol Score", format="%.1f"),
            "trend_score": st.column_config.NumberColumn("Trend Score", format="%.1f"),
            "htf_trend_score": st.column_config.NumberColumn("HTF Trend", format="%.1f"),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f"),
            "funding_quality_score": st.column_config.NumberColumn("Funding Score", format="%.1f"),
            "funding_trend_quality_score": st.column_config.NumberColumn("Funding Trend", format="%.1f"),
            "rs_1h": st.column_config.TextColumn("RS 1H"),
            "rs_4h": st.column_config.TextColumn("RS 4H"),
            "rs_24h": st.column_config.TextColumn("RS 24H"),
            "rs_72h": st.column_config.TextColumn("RS 72H"),
            "alpha_4h": st.column_config.TextColumn("Alpha 4H"),
            "alpha_24h": st.column_config.TextColumn("Alpha 24H"),
            "alpha_72h": st.column_config.TextColumn("Alpha 72H"),
            "vol_adj_24h": st.column_config.NumberColumn("Vol-Adj 24H", format="%.2f"),
            "vol_adj_72h": st.column_config.NumberColumn("Vol-Adj 72H", format="%.2f"),
            "volume_ratio_1h": st.column_config.NumberColumn("Vol Ratio", format="%.2f"),
            "volume_z_1h": st.column_config.NumberColumn("Vol Z", format="%.2f"),
            "oi_change_1h": st.column_config.TextColumn("OI 1H"),
            "funding_rate": st.column_config.NumberColumn("Funding", format="%.5f"),
            "funding_cumulative_7d": st.column_config.NumberColumn("Funding 7D", format="%.5f"),
            "funding_trend": st.column_config.NumberColumn("Funding Trend Raw", format="%.5f"),
            "quote_volume_24h": st.column_config.NumberColumn("24H Quote Vol", format="%.0f"),
            "trades_24h": st.column_config.NumberColumn("24H Trades", format="%.0f"),
            "oi_value": st.column_config.NumberColumn("OI Value", format="%.0f"),
        },
        hide_index=True,
    )


def _render_bitcoin_section(bitcoin_mode: str, bubble_lookback_days: int):
    progress_msg = st.empty()
    progress_msg.info("Building daily Bitcoin spot volume bubble map across spot venues...")
    bubble_bundle = build_bitcoin_bubble_data(bubble_lookback_days)
    progress_msg.empty()

    sources = bubble_bundle["sources"]
    aggregated = bubble_bundle["aggregated"]
    errors = bubble_bundle["errors"]
    aggregate_keys = bubble_bundle["aggregate_keys"]

    if bitcoin_mode == "Bitcoin Spot Vol (Binance)":
        source_key = "binance"
        title = "Bitcoin Spot Volume Bubble Map - Binance"
        df = sources.get(source_key, pd.DataFrame())
    elif bitcoin_mode == "Bitcoin Spot Vol (Coinbase)":
        source_key = "coinbase"
        title = "Bitcoin Spot Volume Bubble Map - Coinbase"
        df = sources.get(source_key, pd.DataFrame())
    else:
        source_key = "aggregated"
        title = "Bitcoin Spot Volume Bubble Map - Aggregated CEX"
        df = aggregated

    if df.empty:
        st.error("No Bitcoin spot bubble-map data was available for this source right now.")
        if errors:
            st.caption("Source errors: " + " | ".join(f"{key}: {value}" for key, value in errors.items()))
        st.stop()

    latest = df.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bars", f"{len(df):,}")
    c2.metric("Latest Price", f"{latest['close']:,.2f}")
    c3.metric("Latest Spot Volume", f"{latest['quote_volume']:,.0f}")
    c4.metric("Volume State", str(latest["temperature"]))

    st.caption(
        "Colors reflect 1d rolling spot-volume temperature from a 30-day z-score: "
        "blue = Cooling, gray = Neutral, pink = Heating, red = Overheating."
    )
    if source_key == "aggregated":
        st.caption(
            "Aggregated view currently sums public spot data from: "
            + ", ".join(key.title() for key in aggregate_keys)
            + ". Hyperliquid spot is not included in this first version."
        )
    elif source_key in errors:
        st.caption(f"Fetch note for {source_key}: {errors[source_key]}")

    fig = _build_bitcoin_bubble_chart(df, title)
    st.plotly_chart(fig, use_container_width=True)

    detail_df = df[["ts", "close", "quote_volume", "volume_z", "temperature"]].copy()
    detail_df["ts"] = detail_df["ts"].dt.strftime("%Y-%m-%d")
    st.dataframe(
        detail_df.sort_values("ts", ascending=False),
        use_container_width=True,
        height=360,
        column_config={
            "ts": st.column_config.TextColumn("Date"),
            "close": st.column_config.NumberColumn("BTC Price", format="%.2f"),
            "quote_volume": st.column_config.NumberColumn("Spot Volume (USD)", format="%.0f"),
            "volume_z": st.column_config.NumberColumn("Volume Z", format="%.2f"),
            "temperature": st.column_config.TextColumn("State"),
        },
        hide_index=True,
    )


def main():
    st.set_page_config(
        page_title="Binance Perp Scanner 2.0",
        page_icon=":satellite:",
        layout="wide",
    )
    _inject_app_styles()

    st_autorefresh(interval=REFRESH_MS, key="scanner_refresh")

    st.title("Binance Perp Scanner 2.0")
    st.caption(
        "Altcoin momentum screener for Binance USDT-M perps using BTC-relative strength, "
        "volume expansion, EMA/VWAP trend, open interest, and funding quality."
    )

    with st.sidebar:
        st.header("Controls")
        if st.button("Force refresh"):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        section = st.radio("Section", ["Altcoins", "BITCOIN"], index=0)

        if section == "BITCOIN":
            st.subheader("BITCOIN")
            bitcoin_mode = st.radio(
                "Bitcoin Spot Volume Bubblemap",
                [
                    "Bitcoin Spot Vol (Binance)",
                    "Bitcoin Spot Vol (Coinbase)",
                    "Bitcoin Spot Vol (Aggregated)",
                ],
                index=0,
            )
            bubble_lookback_days = st.slider("Days to show", 90, 1000, 365, 30)
            st.caption("Aggregated view uses public spot data from Binance, Coinbase, Bybit, OKX, and Kraken when available.")
        else:
            st.subheader("Liquidity gates")
            min_quote_volume = st.number_input(
                "Min 24H quote volume (USDT)",
                min_value=0.0,
                value=10_000_000.0,
                step=1_000_000.0,
                format="%.0f",
            )
            min_trades = st.number_input(
                "Min 24H trades",
                min_value=0.0,
                value=15_000.0,
                step=1_000.0,
                format="%.0f",
            )
            min_oi_value = st.number_input(
                "Min open interest value",
                min_value=0.0,
                value=5_000_000.0,
                step=500_000.0,
                format="%.0f",
            )
            st.caption("Balanced defaults: 10M quote volume, 15k trades, 5M open interest.")

            st.divider()
            st.subheader("Setup filters")
            scoring_mode = st.radio(
                "Scoring mode",
                ["LTF Momentum", "HTF Leadership"],
                index=0,
                help="LTF favors 1H/4H/24H ignition. HTF favors 24H/72H/7D leadership and cleaner trends.",
            )
            min_momentum_score = st.slider("Min momentum score", 0, 100, 70, 1)
            max_overextension_score = st.slider("Max overextension score", 0, 100, 65, 1)
            top_n = st.slider("Rows to show", 10, 100, 30, 5)

            st.divider()
            st.subheader("View mode")
            view = st.radio(
                "Chart",
                [
                    "Momentum vs Overextension",
                    "RS 4H vs RS 24H",
                    "Volume vs OI Expansion",
                ],
                index=0,
            )

            st.divider()
            st.caption("Universe excludes BTCUSDT by design.")
            st.caption("Data: Binance Futures public market data endpoints.")

    if section == "BITCOIN":
        _render_bitcoin_section(bitcoin_mode, bubble_lookback_days)
        return

    with st.spinner("Fetching active Binance perpetuals..."):
        try:
            symbols = tuple(get_usdt_perpetuals())
        except Exception as e:
            st.error(f"Could not connect to Binance: {e}")
            st.stop()

    progress_msg = st.empty()
    progress_msg.info("Building 1H momentum model for Binance altcoin perps - first load can take ~20 sec...")
    df = build_metrics(symbols, min_quote_volume, min_trades, min_oi_value)
    progress_msg.empty()

    if df.empty:
        st.error("No altcoins matched the current gates. Lower the liquidity thresholds and try again.")
        st.stop()

    if scoring_mode == "HTF Leadership":
        momentum_col = "htf_momentum_score"
        setup_col = "htf_setup_score"
        score_label = "HTF Momentum"
        sort_cols = ["htf_setup_score", "htf_momentum_score"]
    else:
        momentum_col = "momentum_score"
        setup_col = "setup_score"
        score_label = "LTF Momentum"
        sort_cols = ["setup_score", "momentum_score"]

    setups = df[
        (df[momentum_col] >= float(min_momentum_score))
        & (df["overextension_score"] <= float(max_overextension_score))
    ].sort_values(sort_cols, ascending=False)
    ranked_df = df.sort_values([momentum_col, setup_col], ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alts Scanned", f"{len(df):,}")
    c2.metric("Qualified Setups", f"{len(setups):,}")
    c3.metric(f"Top {score_label}", f"{df[momentum_col].max():.1f}")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    st.divider()

    if view == "Momentum vs Overextension":
        fig = _scatter(
            ranked_df,
            momentum_col,
            "overextension_score",
            "alpha_score" if scoring_mode == "LTF Momentum" else "htf_alpha_score",
            f"{score_label} vs Overextension",
            f"{score_label} Score",
            "Overextension Score",
        )
    elif view == "RS 4H vs RS 24H":
        fig = _scatter(
            ranked_df,
            "rs_4h",
            "rs_24h",
            momentum_col,
            "Relative Strength vs BTC",
            "RS vs BTC (4H)",
            "RS vs BTC (24H)",
        )
    else:
        fig = _scatter(
            ranked_df,
            "volume_ratio_1h",
            "oi_change_1h",
            momentum_col,
            "Volume Expansion vs Open Interest Expansion",
            "1H Volume Ratio",
            "1H OI Change",
        )

    st.plotly_chart(fig, use_container_width=True)

    st.subheader(f"Top {score_label.lower()} setups ({len(setups)} assets)")
    _show_table(setups.head(top_n))

    with st.expander(f"Full screener ({len(df)} assets)"):
        _show_table(ranked_df)


if __name__ == "__main__":
    main()
