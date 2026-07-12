"""
Binance USDT Perpetual Scanner

Scans active USDT-M perpetual futures on Binance Futures and ranks altcoins by
momentum, relative strength vs BTC, and overextension.
No API key required.
"""

import warnings
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from streamlit_autorefresh import st_autorefresh
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore")


BINANCE_BASE = "https://fapi.binance.com"
BINANCE_SPOT_BASE = "https://api.binance.com"
DERIBIT_BASE = "https://www.deribit.com/api/v2"
YAHOO_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
EODHD_BASE = "https://eodhd.com/api"
INTERVAL = "1h"
CANDLE_LIMIT = 240
ETH_SYMBOL = "ETHUSDT"
LTF_INTERVALS = ("5m", "15m", "1h")
LTF_KLINE_LIMITS = {"5m": 240, "15m": 240, "1h": 240}
LTF_OI_LIMIT = 120
LTF_CACHE_TTL = 45
HTF_DAILY_LIMIT = 120
HTF_DAILY_OI_LIMIT = 60
ATR_PERIOD = 14
ATR_PERCENTILE_LOOKBACK = 120
ATR_ROC_LOOKBACK = 3
COMPRESSION_RECENT_BARS = {"5m": 18, "15m": 12, "1h": 8}
RANGE_LOOKBACK_BARS = {"5m": 36, "15m": 24, "1h": 24}
RS_LOOKBACK_BARS = {"5m": 12, "15m": 8, "1h": 4}
ATR_ROC_THRESHOLD = 0.08
VOLUME_Z_THRESHOLD = 2.0
OI_Z_THRESHOLD = 2.0
FRESH_TRIGGER_BARS = {"5m": 2, "15m": 2, "1h": 1}
MAX_BEST_SETUP_TRIGGER_BARS = 3
MIN_TF_ALIGNMENT = 2
TAKER_IMBALANCE_THRESHOLD = 0.08
BASIS_CONFIRM_BP = 0.0
LTF_ALPHA_WEIGHTS = {"1h": 0.20, "4h": 0.45, "24h": 0.35}
HTF_ALPHA_WEIGHTS = {"24h": 0.50, "72h": 0.30, "168h": 0.20}
VOL_ADJUSTED_WEIGHTS = {"24h": 0.35, "72h": 0.35, "168h": 0.30}
LTF_RS_WEIGHTS = {"1h": 0.20, "4h": 0.45, "24h": 0.35}
HTF_RS_WEIGHTS = {"24h": 0.50, "72h": 0.30, "168h": 0.20}
VOLUME_BLEND_WEIGHTS = {"ratio": 0.60, "zscore": 0.40}
TREND_BLEND_WEIGHTS = {"ema20": 0.30, "ema36": 0.20, "ema50": 0.15, "alignment": 0.20, "vwap": 0.15}
HTF_TREND_BLEND_WEIGHTS = {"ema36": 0.30, "ema50": 0.25, "alignment": 0.25, "vwap": 0.20}
OVEREXTENSION_WEIGHTS = {"vwap": 0.45, "ema20": 0.25, "ema36": 0.15, "ret_1h": 0.15}
MOMENTUM_SCORE_WEIGHTS = {"alpha": 0.25, "rs": 0.15, "volume": 0.25, "trend": 0.20, "oi": 0.10, "funding": 0.05}
HTF_MOMENTUM_SCORE_WEIGHTS = {
    "alpha": 0.30,
    "vol_adjusted": 0.20,
    "rs": 0.20,
    "trend": 0.15,
    "oi": 0.10,
    "funding": 0.05,
}
SETUP_OVEREXTENSION_PENALTY = 0.45
HTF_SETUP_OVEREXTENSION_PENALTY = 0.40
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
REFRESH_MS = 60 * 1000
CACHE_TTL = 280
API_TIMEOUT = 15
BTC_SYMBOL = "BTCUSDT"
IBIT_SYMBOL = "IBIT"
BTC_ETF_TICKERS = ("IBIT", "FBTC", "ARKB", "BITB")
BTC_BUBBLE_LOOKBACK = 365
BTC_OPTIONS_MAX_CONTRACTS = 180
BTC_OPTIONS_MAX_DAYS = 120
BTC_OPTIONS_KLINE_LIMIT = 576
BTC_OPTIONS_HISTORY_PATH = Path(__file__).with_name("data") / "btc_options_history.csv"
BTC_OPTIONS_BLOCK_DB_PATH = Path(__file__).with_name("data") / "deribit_block_trades.sqlite"
FRONT_DAY_HOURS = 24
FRONT_WEEK_DAYS = 7
PIN_MAX_HOURS = 24
PIN_DISTANCE_PCT = 2.0
BLOCK_FLOW_RETENTION_DAYS = 30
BLOCK_FLOW_PRIMARY_DAYS = 7
BLOCK_RFQ_WEIGHT = 2.0
BTC_BUBBLE_TIMEFRAMES = {
    "1D": {
        "label": "1D",
        "rule": "1D",
        "bars_per_day": 1,
        "z_window": 30,
        "binance_interval": "1d",
        "binance_fetch_multiplier": 1,
        "coinbase_granularity": 86400,
        "coinbase_rule": None,
        "coinbase_fetch_multiplier": 1,
        "kraken_interval": 1440,
        "kraken_rule": None,
        "kraken_fetch_multiplier": 1,
        "bybit_interval": "D",
        "bybit_rule": None,
        "bybit_fetch_multiplier": 1,
        "okx_bar": "1Dutc",
        "okx_rule": None,
        "okx_fetch_multiplier": 1,
    },
    "12H": {
        "label": "12H",
        "rule": "12h",
        "bars_per_day": 2,
        "z_window": 60,
        "binance_interval": "12h",
        "binance_fetch_multiplier": 1,
        "coinbase_granularity": 3600,
        "coinbase_rule": "12h",
        "coinbase_fetch_multiplier": 12,
        "kraken_interval": 240,
        "kraken_rule": "12h",
        "kraken_fetch_multiplier": 3,
        "bybit_interval": "720",
        "bybit_rule": None,
        "bybit_fetch_multiplier": 1,
        "okx_bar": "12H",
        "okx_rule": None,
        "okx_fetch_multiplier": 1,
    },
    "8H": {
        "label": "8H",
        "rule": "8h",
        "bars_per_day": 3,
        "z_window": 90,
        "binance_interval": "8h",
        "binance_fetch_multiplier": 1,
        "coinbase_granularity": 3600,
        "coinbase_rule": "8h",
        "coinbase_fetch_multiplier": 8,
        "kraken_interval": 240,
        "kraken_rule": "8h",
        "kraken_fetch_multiplier": 2,
        "bybit_interval": "240",
        "bybit_rule": "8h",
        "bybit_fetch_multiplier": 2,
        "okx_bar": "4H",
        "okx_rule": "8h",
        "okx_fetch_multiplier": 2,
    },
}
SPOT_COLOR_MAP = {
    "Neutral": "#8a8f9c",
    "Cooling": "#3b82f6",
    "Heating": "#f472b6",
    "Overheating": "#ef4444",
}
SPOT_FLOW_COLOR_MAP = {
    "Strong Buy": "#22c55e",
    "Buy": "#86efac",
    "Neutral": "#8a8f9c",
    "Sell": "#fca5a5",
    "Strong Sell": "#ef4444",
    "Unknown": "#64748b",
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
TERM_GUIDE = [
    (
        "Momentum",
        "Primary low-timeframe score. It blends beta-adjusted alpha, raw relative strength vs BTC, volume expansion, trend structure, open-interest expansion, and funding quality into one 0-100 ranking.",
    ),
    (
        "LTF Scalping",
        "Renamed low-timeframe dashboard. It keeps the original LTF momentum model and adds the native 5m/15m/1h LTF Ignition regime scan above it.",
    ),
    (
        "HTF Momentum",
        "Higher-timeframe leadership score. It leans more heavily on 24H/72H/7D behavior, cleaner trend structure, and volatility-adjusted persistence rather than short-term ignition.",
    ),
    (
        "LTF Ignition",
        "Native lower-timeframe regime scan. It looks for recent ATR compression followed by ATR expansion, volume/OI z-score spikes, VWAP/range break, and BTC/ETH relative-strength confirmation.",
    ),
    (
        "HTF Expansion",
        "Higher-timeframe context scan derived from the existing 1H history. It highlights ATR compression/expansion, broader structure breaks, participation, and relative strength.",
    ),
    (
        "Best Setups",
        "Combined view that ranks accurate LTF ignition against HTF expansion or compression context. The strongest rows align lower-timeframe trigger with higher-timeframe regime.",
    ),
    (
        "Overext",
        "Overextension score. Higher values mean the move is already stretched through VWAP distance, EMA extension, and recent acceleration, so continuation is more vulnerable to snapback.",
    ),
    (
        "Setup",
        "LTF setup score. This is momentum adjusted down by overextension, which helps surface strong names that are not already too crowded or late.",
    ),
    (
        "HTF Setup",
        "HTF setup score. Same idea as Setup, but built from the higher-timeframe leadership model instead of the lower-timeframe momentum model.",
    ),
    (
        "Alpha Score",
        "Percentile score of beta-adjusted outperformance vs BTC on the LTF blend. High values mean the coin is outperforming what its usual BTC sensitivity would imply.",
    ),
    (
        "HTF Alpha",
        "Percentile score of higher-timeframe beta-adjusted outperformance vs BTC. This helps identify leaders that stay strong even after filtering out the broader BTC move.",
    ),
    (
        "Vol-Adj",
        "Volatility-adjusted return score. It rewards returns that stay strong after accounting for how noisy the path was, which helps separate cleaner trends from chaotic moves.",
    ),
    (
        "RS Score",
        "Percentile score of raw relative strength vs BTC on the LTF blend. It answers whether the alt beat BTC without beta-adjusting for its usual behavior.",
    ),
    (
        "HTF RS",
        "Higher-timeframe raw relative strength vs BTC score. Useful for seeing which names have been leadership candidates over longer windows.",
    ),
    (
        "Vol Score",
        "Volume expansion score. It combines 1H volume ratio and 1H volume z-score to find names with both large and unusual participation.",
    ),
    (
        "Trend Score",
        "Low-timeframe trend-structure score based on price vs EMA20/36/50, EMA alignment, and positive VWAP bias.",
    ),
    (
        "HTF Trend",
        "Higher-timeframe trend-structure score. It puts more weight on sustained EMA structure and higher-timeframe VWAP position than on short bursts.",
    ),
    (
        "OI Score",
        "Open-interest expansion score. Higher values mean the move is being confirmed by OI growth rather than only drifting on price.",
    ),
    (
        "Funding Score",
        "Funding quality score. It rewards neutral-to-healthy funding and penalizes extreme crowding, since very stretched funding often means the move is late.",
    ),
    (
        "Funding Trend",
        "Funding trend quality score. It looks at cumulative funding and recent funding shift to tell whether the longer funding backdrop is still healthy or already overheated.",
    ),
    (
        "RS 1H",
        "Raw 1-hour relative strength vs BTC. Positive values mean the coin beat BTC over the last hour.",
    ),
    (
        "RS 4H",
        "Raw 4-hour relative strength vs BTC. This is one of the core short-term leadership windows in the LTF model.",
    ),
    (
        "RS 24H",
        "Raw 24-hour relative strength vs BTC. It helps distinguish a real move from a very short-lived spike.",
    ),
    (
        "RS 72H",
        "Raw 72-hour relative strength vs BTC. Mainly useful for the HTF side of the screener.",
    ),
    (
        "Alpha 4H",
        "4-hour beta-adjusted relative strength vs BTC. Positive values mean the coin beat what its normal BTC relationship would have predicted.",
    ),
    (
        "Alpha 24H",
        "24-hour beta-adjusted relative strength vs BTC. A cleaner measure of whether the coin’s move is genuinely special, not just high-beta follow-through.",
    ),
    (
        "Alpha 72H",
        "72-hour beta-adjusted relative strength vs BTC. This is more relevant for sustained higher-timeframe leadership.",
    ),
    (
        "Vol Ratio",
        "Current 1H quote volume divided by its recent baseline. It tells you whether the latest participation is large relative to normal.",
    ),
    (
        "Vol Z",
        "1H volume z-score. It measures how statistically unusual the latest volume is compared with recent history.",
    ),
    (
        "OI 1H",
        "1-hour open-interest change. Positive values mean new exposure is entering; negative values suggest exposure is being closed out.",
    ),
    (
        "Funding",
        "Latest funding rate on the perpetual contract. Mildly positive funding can be healthy, but extreme positive funding often signals crowding.",
    ),
    (
        "Funding 7D",
        "Cumulative recent funding backdrop. This helps show whether a contract has been persistently crowded over the past week.",
    ),
    (
        "Funding Trend Raw",
        "Change in the latest funding rate versus its recent baseline. It helps spot when funding is rapidly becoming more crowded or relaxing.",
    ),
    (
        "24H Quote Vol",
        "24-hour quote volume from Binance futures ticker data. This is one of the main liquidity gates used to keep thin markets out of the screener.",
    ),
    (
        "24H Trades",
        "24-hour trade count from Binance futures ticker data. This is another liquidity gate that helps exclude contracts with weak participation.",
    ),
    (
        "OI Value",
        "Estimated open-interest notional value. Higher values usually mean the contract is liquid enough to treat its signals more seriously.",
    ),
]
TERM_GUIDE_GROUPS = [
    (
        "Scores",
        [
            ("LTF Scalping", "Renamed LTF dashboard. It keeps the classic LTF model and adds the accurate native LTF Ignition scanner above it."),
            ("Momentum", "Primary LTF ranking. It blends alpha, RS vs BTC, volume, trend, OI, and funding quality into one 0-100 score."),
            ("HTF Momentum", "Higher-timeframe leadership score. It favors cleaner 24H to 7D strength over short bursts."),
            ("LTF Ignition", "Native 5m/15m/1h regime score for compression resolving into expansion."),
            ("HTF Expansion", "Lighter HTF context score for compression/expansion and broader structure."),
            ("Best Setups", "Combined score for accurate LTF ignition with supportive HTF context."),
            ("Overext", "Overextension score. Higher values mean the move is more stretched and vulnerable to snapback."),
            ("Setup", "LTF setup score. It rewards strong momentum while penalizing names that already look too extended."),
            ("HTF Setup", "HTF version of Setup. It starts from the higher-timeframe model instead of the LTF model."),
            ("Alpha Score", "Percentile score of beta-adjusted outperformance vs BTC on the LTF blend."),
            ("HTF Alpha", "Higher-timeframe beta-adjusted outperformance vs BTC. Good for spotting true leaders, not just BTC passengers."),
            ("Vol-Adj", "Volatility-adjusted return score. It rewards strength that came with a cleaner path."),
            ("RS Score", "Percentile score of raw relative strength vs BTC on the LTF blend."),
            ("HTF RS", "Higher-timeframe raw relative strength vs BTC. Useful for sustained leadership."),
            ("Vol Score", "Volume expansion score built from 1H volume ratio and 1H volume z-score."),
            ("Trend Score", "LTF trend-structure score from EMA alignment and VWAP bias."),
            ("HTF Trend", "HTF trend-structure score. It leans more on sustained EMA structure and higher-timeframe VWAP position."),
            ("OI Score", "Open-interest expansion score. Higher values mean price strength is being confirmed by new exposure."),
        ],
    ),
    (
        "Raw RS / Alpha",
        [
            ("RS 1H", "Raw 1-hour relative strength vs BTC. Positive means the coin beat BTC over the last hour."),
            ("RS 4H", "Raw 4-hour relative strength vs BTC. One of the core LTF leadership windows."),
            ("RS 24H", "Raw 24-hour relative strength vs BTC. Useful for separating real moves from short spikes."),
            ("RS 72H", "Raw 72-hour relative strength vs BTC. More relevant on the HTF side."),
            ("Alpha 4H", "4-hour beta-adjusted RS vs BTC. Positive means the coin beat what its normal BTC sensitivity implied."),
            ("Alpha 24H", "24-hour beta-adjusted RS vs BTC. A cleaner read on whether the move is genuinely special."),
            ("Alpha 72H", "72-hour beta-adjusted RS vs BTC. Better for sustained higher-timeframe leadership."),
        ],
    ),
    (
        "Volume / OI",
        [
            ("Vol Ratio", "Current 1H quote volume divided by its recent baseline."),
            ("Vol Z", "1H volume z-score. It shows how unusual the latest volume is vs recent history."),
            ("OI 1H", "1-hour open-interest change. Positive means exposure is entering; negative means it is being closed."),
        ],
    ),
    (
        "Funding / Liquidity",
        [
            ("Funding Score", "Funding quality score. It prefers healthy funding and penalizes extreme crowding."),
            ("Funding Trend", "Funding trend quality score from cumulative funding and recent funding shift."),
            ("Funding", "Latest funding rate on the perpetual contract."),
            ("Funding 7D", "Recent cumulative funding backdrop. Helpful for spotting persistent crowding."),
            ("Funding Trend Raw", "Latest funding minus its recent baseline. Useful for seeing crowding accelerate or fade."),
            ("24H Quote Vol", "24-hour quote volume from Binance futures ticker data. One of the main liquidity gates."),
            ("24H Trades", "24-hour trade count from Binance futures ticker data. Helps exclude weakly traded contracts."),
            ("OI Value", "Estimated open-interest notional value. Higher values usually mean a more liquid, trustworthy contract."),
        ],
    ),
]


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


def _get_deribit(method: str, params: Optional[dict] = None, timeout: int = API_TIMEOUT):
    response = _get_json_url(f"{DERIBIT_BASE}/{method}", params=params, timeout=timeout)
    if isinstance(response, dict) and "result" in response:
        return response["result"]
    return response


def _get_yahoo_chart(symbol: str, range_: str, interval: str, timeout: int = API_TIMEOUT):
    return _get_json_url(
        f"{YAHOO_CHART_BASE}/{symbol}",
        params={"range": range_, "interval": interval, "includePrePost": "false"},
        timeout=timeout,
    )


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
                max-width: 1820px;
                padding-left: 2rem;
                padding-right: 2rem;
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
                min-height: 76px;
                position: relative;
                overflow: hidden;
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
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                padding-right: 3.9rem;
            }}

            [data-testid="stMetricDelta"] {{
                position: absolute;
                right: 0.85rem;
                bottom: 0.72rem;
                margin: 0;
                max-width: 3.6rem;
                overflow: hidden;
                white-space: nowrap;
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

            .term-guide-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 0.85rem;
                margin-top: 0.8rem;
            }}

            @media (max-width: 1100px) {{
                .term-guide-grid {{
                    grid-template-columns: minmax(0, 1fr);
                }}
            }}

            .term-guide-card {{
                background: rgba(20, 29, 20, 0.9);
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 10px;
                padding: 0.9rem 1rem;
                box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
            }}

            .term-guide-title {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.98rem;
                font-weight: 700;
                line-height: 1.2;
                margin-bottom: 0.45rem;
            }}

            .term-guide-copy {{
                color: var(--app-muted);
                font-size: 0.84rem;
                line-height: 1.45;
            }}

            .term-guide-group {{
                margin-top: 1rem;
            }}

            .term-guide-section {{
                margin-top: 1rem;
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 10px;
                background: rgba(17, 24, 17, 0.65);
                overflow: hidden;
            }}

            .term-guide-summary {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 1.02rem;
                font-weight: 700;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                padding: 0.9rem 1rem;
                cursor: pointer;
                list-style: none;
                background: rgba(20, 29, 20, 0.92);
                border-bottom: 1px solid rgba(39, 50, 38, 0.75);
            }}

            .term-guide-summary::-webkit-details-marker {{
                display: none;
            }}

            .term-guide-summary::before {{
                content: "▸";
                display: inline-block;
                margin-right: 0.55rem;
                transition: transform 0.18s ease;
            }}

            details[open] > .term-guide-summary::before {{
                transform: rotate(90deg);
            }}

            .term-guide-section-body {{
                padding: 0 1rem 1rem 1rem;
            }}

            .gex-level-title {{
                font-size: 1.32rem;
                font-weight: 700;
                color: var(--app-text);
                margin: 0 0 0.55rem 0;
            }}

            .gex-level-list {{
                display: flex;
                flex-direction: column;
                gap: 0.95rem;
                margin: 0;
            }}

            .gex-level-item {{
                padding: 0.76rem 0.85rem;
                border: 1px solid rgba(39, 50, 38, 0.95);
                border-radius: 6px;
                background: rgba(17, 24, 17, 0.9);
                line-height: 1.82;
                font-size: 1.09rem;
                color: var(--app-text);
            }}

            .gex-chip {{
                display: inline-block;
                margin: 0 0.42rem 0.34rem 0;
                padding: 0.16rem 0.38rem;
                border: 1px solid rgba(39, 50, 38, 0.95);
                border-radius: 6px;
                background: rgba(17, 24, 17, 0.9);
                font-size: 1.09rem;
                font-family: "IBM Plex Mono", "Consolas", monospace;
                color: #7ef0a0;
            }}

            .options-snapshot-large [data-testid="stDataFrame"] {{
                font-size: 1.12rem;
            }}

            .options-snapshot-large [data-testid="stDataFrame"] [role="columnheader"] {{
                font-size: 0.98rem;
            }}

            .options-snapshot-table {{
                width: 100%;
                border-collapse: collapse;
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 4px;
                overflow: hidden;
                background: rgba(17, 24, 17, 0.84);
                font-family: "IBM Plex Mono", "Consolas", monospace;
                font-size: 1.12rem;
            }}

            .options-snapshot-table thead th {{
                text-align: left;
                padding: 0.8rem 0.95rem;
                font-size: 0.98rem;
                font-weight: 600;
                color: var(--app-muted);
                background: rgba(28, 34, 28, 0.96);
                border-bottom: 1px solid rgba(39, 50, 38, 0.8);
            }}

            .options-snapshot-table tbody td {{
                padding: 0.82rem 0.95rem;
                border-top: 1px solid rgba(39, 50, 38, 0.55);
                color: var(--app-text);
            }}

            .jarvis-fab {{
                position: fixed;
                right: 1.2rem;
                bottom: 1.2rem;
                z-index: 999;
                width: min(360px, calc(100vw - 2rem));
            }}

            .jarvis-fab > summary {{
                list-style: none;
                cursor: pointer;
                margin-left: auto;
                width: fit-content;
                max-width: 100%;
                background: rgba(17, 24, 17, 0.96);
                border: 1px solid rgba(39, 50, 38, 0.92);
                border-radius: 999px;
                padding: 0.78rem 1rem;
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.94rem;
                font-weight: 700;
                box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
            }}

            .jarvis-fab > summary::-webkit-details-marker {{
                display: none;
            }}

            .jarvis-panel {{
                margin-top: 0.75rem;
                border: 1px solid rgba(39, 50, 38, 0.92);
                border-radius: 10px;
                background: rgba(12, 18, 12, 0.98);
                box-shadow: 0 12px 28px rgba(0, 0, 0, 0.34);
                overflow: hidden;
                max-height: min(68vh, 620px);
                display: flex;
                flex-direction: column;
            }}

            .jarvis-panel-head {{
                padding: 0.9rem 1rem 0.75rem 1rem;
                border-bottom: 1px solid rgba(39, 50, 38, 0.72);
                background: rgba(20, 29, 20, 0.94);
            }}

            .jarvis-panel-title {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 1rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin: 0 0 0.35rem 0;
            }}

            .jarvis-panel-copy {{
                color: var(--app-muted);
                font-size: 0.84rem;
                line-height: 1.45;
            }}

            .jarvis-faq {{
                padding: 0.85rem 1rem 1rem 1rem;
                overflow-y: auto;
                max-height: calc(min(68vh, 620px) - 96px);
                scrollbar-width: thin;
                scrollbar-color: rgba(94, 111, 87, 0.9) rgba(17, 24, 17, 0.65);
            }}

            .jarvis-faq::-webkit-scrollbar {{
                width: 10px;
            }}

            .jarvis-faq::-webkit-scrollbar-track {{
                background: rgba(17, 24, 17, 0.65);
                border-left: 1px solid rgba(39, 50, 38, 0.45);
            }}

            .jarvis-faq::-webkit-scrollbar-thumb {{
                background: rgba(94, 111, 87, 0.9);
                border-radius: 999px;
                border: 2px solid rgba(17, 24, 17, 0.65);
            }}

            .jarvis-faq-item {{
                border: 1px solid rgba(39, 50, 38, 0.82);
                border-radius: 8px;
                background: rgba(17, 24, 17, 0.88);
                overflow: hidden;
            }}

            .jarvis-faq-item > summary {{
                list-style: none;
                cursor: pointer;
                padding: 0.82rem 0.9rem;
                color: var(--app-text);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.88rem;
                font-weight: 700;
                line-height: 1.4;
                background: rgba(20, 29, 20, 0.96);
                border-bottom: 1px solid rgba(39, 50, 38, 0.72);
            }}

            .jarvis-faq-item > summary::-webkit-details-marker {{
                display: none;
            }}

            .jarvis-faq-item > summary::before {{
                content: "›";
                display: inline-block;
                margin-right: 0.45rem;
                transition: transform 0.18s ease;
            }}

            .jarvis-faq-item[open] > summary::before {{
                transform: rotate(90deg);
            }}

            .jarvis-answer {{
                padding: 0.9rem 0.95rem 0.95rem 0.95rem;
            }}

            .jarvis-answer ul {{
                margin: 0;
                padding-left: 1.05rem;
            }}

            .jarvis-answer li {{
                color: var(--app-text);
                font-size: 0.84rem;
                line-height: 1.52;
                margin: 0 0 0.55rem 0;
            }}

            .jarvis-answer h4 {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.86rem;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin: 0.95rem 0 0.42rem 0;
            }}

            .jarvis-answer h4:first-child {{
                margin-top: 0;
            }}

            .jarvis-answer p {{
                color: var(--app-text);
                font-size: 0.84rem;
                line-height: 1.5;
                margin: 0 0 0.6rem 0;
            }}

            .jarvis-answer .jarvis-note {{
                color: var(--app-muted);
                font-size: 0.78rem;
                line-height: 1.45;
                margin-top: 0.8rem;
            }}

            .jarvis-answer .jarvis-simple-summary {{
                color: var(--app-muted);
                font-size: 0.67rem;
                line-height: 1.38;
            }}

            .jarvis-answer strong {{
                color: var(--app-accent);
            }}

            .positioning-grid {{
                display: grid;
                grid-template-columns: repeat(5, minmax(0, 1fr));
                gap: 0.75rem;
                margin: 0.85rem 0 1.15rem 0;
            }}

            @media (max-width: 1320px) {{
                .positioning-grid {{
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }}
            }}

            @media (max-width: 900px) {{
                .positioning-grid {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
            }}

            .positioning-card {{
                border: 1px solid rgba(39, 50, 38, 0.88);
                border-radius: 8px;
                background: rgba(17, 24, 17, 0.92);
                padding: 0.82rem 0.9rem 0.86rem 0.9rem;
                min-height: 122px;
            }}

            .positioning-head {{
                display: flex;
                align-items: center;
                gap: 0.55rem;
                margin-bottom: 0.52rem;
            }}

            .positioning-dot {{
                width: 14px;
                height: 14px;
                border-radius: 999px;
                border: 1px solid rgba(228, 234, 223, 0.18);
                flex: 0 0 auto;
            }}

            .positioning-title {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.82rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                line-height: 1.3;
            }}

            .positioning-status {{
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.98rem;
                font-weight: 700;
                margin-bottom: 0.35rem;
            }}

            .positioning-copy {{
                color: var(--app-muted);
                font-size: 0.79rem;
                line-height: 1.46;
            }}

            .positioning-green {{
                color: #7ef0a0;
            }}

            .positioning-yellow {{
                color: #f4d35e;
            }}

            .positioning-red {{
                color: #ff6b6b;
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


def _fetch_ltf_symbol_context(symbol: str) -> tuple[str, dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    klines: dict[str, pd.DataFrame] = {}
    oi_hist: dict[str, pd.DataFrame] = {}
    for interval in LTF_INTERVALS:
        try:
            df = _fetch_klines_interval(symbol, interval, LTF_KLINE_LIMITS[interval])
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
def fetch_ltf_symbol_contexts(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_ltf_symbol_context, symbol): symbol for symbol in symbols}
        for fut in as_completed(futures):
            symbol, klines, oi_hist = fut.result()
            if klines:
                out[symbol] = {"klines": klines, "oi_hist": oi_hist}
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
    denom = max(atr_value, 1e-12)

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


def _direction_from_state(value: object) -> str:
    text = str(value)
    if text.startswith("Long"):
        return "Long"
    if text.startswith("Short"):
        return "Short"
    if text == "Compression":
        return "Compression"
    return "Neutral"


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


def _ltf_interval_metrics(
    symbol: str,
    interval: str,
    df: pd.DataFrame,
    oi_df: pd.DataFrame,
    btc_df: pd.DataFrame,
    eth_df: Optional[pd.DataFrame],
    spot_df: Optional[pd.DataFrame],
) -> dict[str, object]:
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
    atr_expansion_score = _score_from_threshold(max(atr_roc, 0.0), ATR_ROC_THRESHOLD, 70.0)
    volume_spike_score = _score_from_threshold(max(volume_zscore, 0.0), VOLUME_Z_THRESHOLD, 80.0)
    oi_spike_score = _score_from_threshold(max(oi_zscore, 0.0), OI_Z_THRESHOLD, 80.0)
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

    expansion_ready_series = (atr_roc_series > ATR_ROC_THRESHOLD) & (compression_count_series > 0)
    volume_spike_series = volume_z_series > VOLUME_Z_THRESHOLD
    oi_spike_series = oi_z_aligned > OI_Z_THRESHOLD
    raw_long_trigger_series = (
        expansion_ready_series
        & volume_spike_series
        & oi_spike_series
        & (df["close"] > vwap_series)
        & break_hold_long_series
        & taker_long_series
        & basis_long_series
    )
    raw_short_trigger_series = (
        expansion_ready_series
        & volume_spike_series
        & oi_spike_series
        & (df["close"] < vwap_series)
        & break_hold_short_series
        & taker_short_series
        & basis_short_series
    )
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
        "rs_vs_btc": rs_vs_btc,
        "rs_vs_eth": rs_vs_eth,
    }


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


def _interval_to_timedelta(interval: str) -> pd.Timedelta:
    interval = interval.strip().lower()
    if interval.endswith("d"):
        return pd.Timedelta(days=int(interval[:-1]))
    if interval.endswith("h"):
        return pd.Timedelta(hours=int(interval[:-1]))
    if interval.endswith("m"):
        return pd.Timedelta(minutes=int(interval[:-1]))
    raise ValueError(f"Unsupported interval: {interval}")


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
        chunk = pd.DataFrame(raw).iloc[:, [0, 4, 7, 10]].copy()
        chunk.columns = ["ts", "close", "quote_volume", "aggressive_buy_volume"]
        chunks.append(chunk)
        first_open = int(chunk.iloc[0]["ts"])
        end_time = first_open - interval_ms
        remaining -= len(chunk)
        if len(chunk) < chunk_limit:
            break

    if not chunks:
        return pd.DataFrame(columns=["ts", "close", "quote_volume", "source"])

    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
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


def _build_bitcoin_bubble_chart(df: pd.DataFrame, title: str):
    color_col = "flow_state" if "flow_state" in df.columns and (df["flow_state"] != "Unknown").any() else "temperature"
    color_map = SPOT_FLOW_COLOR_MAP if color_col == "flow_state" else SPOT_COLOR_MAP
    legend_title = "Spot Flow" if color_col == "flow_state" else "Volume State"
    fig = px.scatter(
        df,
        x="ts",
        y="close",
        size="bubble_size",
        size_max=int(BUBBLE_SIZE_MAX),
        color=color_col,
        color_discrete_map=color_map,
        hover_name="source",
        hover_data={
            "ts": "|%Y-%m-%d",
            "close": ":,.2f",
            "quote_volume": ":,.0f",
            "volume_z": ":.2f",
            "spot_imbalance": ":.2%",
            "spot_delta": ":,.0f",
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
        legend_title=legend_title,
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


def _build_spot_cvd_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if "spot_cvd" in df.columns and df["spot_cvd"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=df["ts"],
                y=df["spot_cvd"],
                mode="lines",
                name="Spot CVD",
                line=dict(color="#86efac", width=2),
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=260,
        title="Spot CVD - Aggressive Buy Volume Minus Aggressive Sell Volume",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=55, b=25),
        xaxis_title="Date",
        yaxis_title="CVD (USD notional)",
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _build_coinbase_premium_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(
            go.Scatter(
                x=df["ts"],
                y=df["coinbase_premium_bp"],
                mode="lines",
                name="Coinbase Premium",
                line=dict(color="#93c5fd", width=2),
            )
        )
        fig.add_hline(y=0, line_color=APP_MUTED, line_dash="dot")
    fig.update_layout(
        template="plotly_dark",
        height=240,
        title="Coinbase Premium - Coinbase BTC/USD vs Binance BTC/USDT",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=55, b=25),
        xaxis_title="Date",
        yaxis_title="Premium (bp)",
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _build_etf_tape_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        colors = np.where(df["etf_signed_dollar_volume"] >= 0, "#22c55e", "#ef4444")
        fig.add_trace(
            go.Bar(
                x=df["date"],
                y=df["etf_signed_dollar_volume"],
                name="ETF Demand Proxy",
                marker_color=colors,
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=260,
        title="BTC ETF Tape Proxy - Signed Dollar Volume, Not Reported Net Flow",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=55, b=25),
        xaxis_title="Date",
        yaxis_title="Signed dollar volume",
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _fetch_deribit_option_ticker(instrument_name: str) -> dict[str, object]:
    payload = _get_deribit("public/ticker", params={"instrument_name": instrument_name}, timeout=12)
    greeks = payload.get("greeks", {}) if isinstance(payload, dict) else {}
    underlying_price = 0.0
    if isinstance(payload, dict):
        underlying_price = _safe_float(payload.get("underlying_price"))
        if underlying_price <= 0.0:
            underlying_price = _safe_float(payload.get("index_price"))
    return {
        "instrument_name": instrument_name,
        "underlying_price": underlying_price,
        "mark_iv": _safe_float(payload.get("mark_iv") if isinstance(payload, dict) else 0.0),
        "bid_iv": _safe_float(payload.get("bid_iv") if isinstance(payload, dict) else 0.0),
        "ask_iv": _safe_float(payload.get("ask_iv") if isinstance(payload, dict) else 0.0),
        "delta": _safe_float(greeks.get("delta")),
        "gamma": _safe_float(greeks.get("gamma")),
        "vega": _safe_float(greeks.get("vega")),
        "theta": _safe_float(greeks.get("theta")),
        "last_price": _safe_float(payload.get("last_price") if isinstance(payload, dict) else 0.0),
    }


def _safe_fetch_deribit_option_ticker(instrument_name: str) -> Optional[dict[str, object]]:
    try:
        return _fetch_deribit_option_ticker(instrument_name)
    except Exception:
        return None


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
        "pressure_copy": f"Estimated next-8h dealer pressure: {bias}. Charm proxy {charm_proxy:+.2f}, vanna proxy {vanna_proxy:+.2f}. Treat as an estimate, not exact dealer inventory.",
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
    gex_median = float(history.tail(30)["total_abs_gex"].median()) if "total_abs_gex" in history else 0.0
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


def _init_block_trade_store() -> sqlite3.Connection:
    BTC_OPTIONS_BLOCK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(BTC_OPTIONS_BLOCK_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deribit_block_trades (
            trade_id TEXT PRIMARY KEY,
            block_trade_id TEXT NOT NULL,
            block_rfq_id TEXT,
            combo_id TEXT,
            combo_trade_id TEXT,
            block_trade_leg_count INTEGER,
            timestamp INTEGER NOT NULL,
            instrument_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL,
            contracts REAL,
            price REAL,
            mark_price REAL,
            iv REAL,
            index_price REAL,
            inserted_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deribit_blocks_ts ON deribit_block_trades(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deribit_blocks_block_id ON deribit_block_trades(block_trade_id)")
    return conn


def _fetch_recent_deribit_block_trades() -> list[dict[str, object]]:
    payload = _get_deribit(
        "public/get_last_trades_by_currency",
        params={"currency": "BTC", "kind": "option", "count": 1000, "sorting": "desc"},
        timeout=20,
    )
    trades = payload.get("trades", []) if isinstance(payload, dict) else []
    return [trade for trade in trades if isinstance(trade, dict) and trade.get("block_trade_id") and trade.get("trade_id")]


def _store_deribit_block_trades(trades: list[dict[str, object]]) -> dict[str, int]:
    conn = _init_block_trade_store()
    inserted = 0
    try:
        now_text = _utc_now_naive().isoformat()
        rows = []
        for trade in trades:
            rows.append(
                (
                    str(trade.get("trade_id")),
                    str(trade.get("block_trade_id")),
                    str(trade.get("block_rfq_id")) if trade.get("block_rfq_id") not in (None, "") else None,
                    str(trade.get("combo_id")) if trade.get("combo_id") not in (None, "") else None,
                    str(trade.get("combo_trade_id")) if trade.get("combo_trade_id") not in (None, "") else None,
                    int(_safe_float(trade.get("block_trade_leg_count"), 1.0)),
                    int(_safe_float(trade.get("timestamp"))),
                    str(trade.get("instrument_name", "")),
                    str(trade.get("direction", "")).lower(),
                    _safe_float(trade.get("amount")),
                    _safe_float(trade.get("contracts")),
                    _safe_float(trade.get("price")),
                    _safe_float(trade.get("mark_price")),
                    _safe_float(trade.get("iv")),
                    _safe_float(trade.get("index_price")),
                    now_text,
                )
            )
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO deribit_block_trades (
                trade_id, block_trade_id, block_rfq_id, combo_id, combo_trade_id,
                block_trade_leg_count, timestamp, instrument_name, direction,
                amount, contracts, price, mark_price, iv, index_price, inserted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.total_changes - before
        cutoff_ms = int((_utc_now_naive() - pd.Timedelta(days=BLOCK_FLOW_RETENTION_DAYS)).timestamp() * 1000)
        conn.execute("DELETE FROM deribit_block_trades WHERE timestamp < ?", (cutoff_ms,))
        conn.commit()
    finally:
        conn.close()
    return {"fetched": len(trades), "inserted": int(inserted)}


def _read_deribit_block_trades(days: int = BLOCK_FLOW_RETENTION_DAYS) -> pd.DataFrame:
    if not BTC_OPTIONS_BLOCK_DB_PATH.exists():
        return pd.DataFrame()
    cutoff_ms = int((_utc_now_naive() - pd.Timedelta(days=days)).timestamp() * 1000)
    conn = sqlite3.connect(BTC_OPTIONS_BLOCK_DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM deribit_block_trades WHERE timestamp >= ? ORDER BY timestamp DESC",
            conn,
            params=(cutoff_ms,),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True).dt.tz_localize(None)
    for col in ["amount", "contracts", "price", "mark_price", "iv", "index_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def _update_deribit_block_trade_store() -> dict[str, object]:
    try:
        trades = _fetch_recent_deribit_block_trades()
        result = _store_deribit_block_trades(trades)
        result["error"] = ""
        return result
    except Exception as exc:
        return {"fetched": 0, "inserted": 0, "error": str(exc)}


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
    matched["leg_gex_abs"] = (
        matched["gamma"].abs()
        * matched["amount"].abs()
        * matched["underlying_for_gex"].replace(0.0, np.nan).fillna(0.0)
        * matched["underlying_for_gex"].replace(0.0, np.nan).fillna(0.0)
        * matched["contract_size"].replace(0.0, 1.0).fillna(1.0)
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

    options_df["gex_abs"] = (
        options_df["gamma"].abs()
        * options_df["open_interest"]
        * options_df["underlying_price"].replace(0.0, spot).fillna(spot)
        * options_df["underlying_price"].replace(0.0, spot).fillna(spot)
        * options_df["contract_size"]
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

    klines = _fetch_binance_btc_perp_klines()
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


def _build_gex_strike_chart(strike_map: pd.DataFrame, spot: float) -> go.Figure:
    nearby = strike_map[(strike_map["strike"] >= spot * 0.85) & (strike_map["strike"] <= spot * 1.15)].copy()
    if nearby.empty:
        nearby = strike_map.copy()
    nearby["call_gex_b"] = nearby["call_gex"] / 1000.0
    nearby["put_gex_b"] = nearby["put_gex"] / 1000.0
    if "block_adjusted_gex" in nearby.columns:
        nearby["block_adjusted_gex_b"] = nearby["block_adjusted_gex"] / 1000.0
    else:
        nearby["block_adjusted_gex_b"] = 0.0
    for col, default in {
        "call_expiry_hover": "Expiry detail unavailable",
        "put_expiry_hover": "Expiry detail unavailable",
        "dominant_expiry": "n/a",
        "dominant_expiry_share": 0.0,
        "front_24h_share": 0.0,
        "front_7d_share": 0.0,
    }.items():
        if col not in nearby.columns:
            nearby[col] = default
    customdata = nearby[
        [
            "call_expiry_hover",
            "put_expiry_hover",
            "front_24h_share",
            "front_7d_share",
            "dominant_expiry",
            "dominant_expiry_share",
            "call_gex_b",
            "put_gex_b",
        ]
    ].to_numpy()
    fig = go.Figure()
    fig.add_bar(
        name="Call GEX",
        x=nearby["strike"],
        y=nearby["call_gex_b"],
        marker_color="#9fab95",
        customdata=customdata,
        hovertemplate=(
            "Strike %{x:,.0f}<br>"
            "Call GEX %{customdata[6]:.2f}B<br>"
            "Top call expiries:<br>%{customdata[0]}<br>"
            "Dominant total expiry: %{customdata[4]} (%{customdata[5]:.0%})<br>"
            "Front 24h share: %{customdata[2]:.0%}<br>"
            "Front 7d share: %{customdata[3]:.0%}"
            "<extra>Call GEX</extra>"
        ),
    )
    fig.add_bar(
        name="Put GEX",
        x=nearby["strike"],
        y=-nearby["put_gex_b"],
        marker_color="#f472b6",
        customdata=customdata,
        hovertemplate=(
            "Strike %{x:,.0f}<br>"
            "Put GEX %{customdata[7]:.2f}B<br>"
            "Top put expiries:<br>%{customdata[1]}<br>"
            "Dominant total expiry: %{customdata[4]} (%{customdata[5]:.0%})<br>"
            "Front 24h share: %{customdata[2]:.0%}<br>"
            "Front 7d share: %{customdata[3]:.0%}"
            "<extra>Put GEX</extra>"
        ),
    )
    if nearby["block_adjusted_gex_b"].abs().sum() > 0:
        colors = np.where(
            nearby.get("block_agreement", pd.Series(index=nearby.index, data="No block read")).eq("Agrees"),
            "rgba(34, 197, 94, 0.62)",
            np.where(
                nearby.get("block_agreement", pd.Series(index=nearby.index, data="No block read")).eq("Disagrees"),
                "rgba(244, 211, 94, 0.72)",
                "rgba(148, 163, 184, 0.35)",
            ),
        )
        fig.add_bar(
            name="Block-Adjusted GEX Est.",
            x=nearby["strike"],
            y=nearby["block_adjusted_gex_b"],
            marker_color=colors,
            opacity=0.72,
            customdata=customdata,
            hovertemplate=(
                "Strike %{x:,.0f}<br>"
                "Block-adjusted est. %{y:.2f}B<br>"
                "Dominant total expiry: %{customdata[4]} (%{customdata[5]:.0%})<br>"
                "Front 24h share: %{customdata[2]:.0%}<br>"
                "Front 7d share: %{customdata[3]:.0%}"
                "<extra>Block-adjusted GEX</extra>"
            ),
        )
    fig.add_vline(x=spot, line_color="#e4eadf", line_dash="dash")
    fig.update_layout(
        barmode="relative",
        title="Strike Gamma Map + Block-Adjusted Gamma Overlay",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Strike",
        yaxis_title="Approx GEX ($B)",
        legend=dict(orientation="h"),
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=True, zerolinecolor=APP_BORDER, linecolor=APP_BORDER)
    return fig


def _build_expiry_map_chart(expiry_map: pd.DataFrame) -> go.Figure:
    chart_df = expiry_map.copy()
    chart_df["abs_gex_b"] = chart_df["abs_gex"] / 1000.0
    fig = go.Figure()
    fig.add_bar(x=chart_df["expiry_label"], y=chart_df["abs_gex_b"], name="Abs GEX ($B)", marker_color="#dfe7d8")
    fig.add_scatter(x=chart_df["expiry_label"], y=chart_df["total_oi"], name="OI (BTC)", mode="lines+markers", yaxis="y2", line=dict(color="#3b82f6"))
    fig.update_layout(
        title="Expiry Map",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=30, t=60, b=30),
        xaxis_title="Expiry",
        yaxis_title="Abs GEX ($B)",
        yaxis2=dict(title="OI (BTC)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h"),
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _build_strike_expiry_heatmap(options_df: pd.DataFrame, spot: float) -> go.Figure:
    fig = go.Figure()
    if options_df.empty:
        fig.update_layout(title="Strike x Expiry Gamma Heatmap")
        return fig

    nearby = options_df[(options_df["strike"] >= spot * 0.85) & (options_df["strike"] <= spot * 1.15)].copy()
    if nearby.empty:
        nearby = options_df.copy()
    grouped = (
        nearby.groupby(["expiration_ts", "expiry_label", "strike"], as_index=False)
        .agg(
            call_gex=("call_gex", "sum"),
            put_gex=("put_gex", "sum"),
            signed_gex=("signed_gex", "sum"),
            abs_gex=("gex_abs", "sum"),
            total_oi=("open_interest", "sum"),
            hours_to_expiry=("hours_to_expiry", "min"),
        )
        .sort_values(["expiration_ts", "strike"])
    )
    if grouped.empty:
        fig.update_layout(title="Strike x Expiry Gamma Heatmap")
        return fig

    grouped["expiry_display"] = grouped.apply(
        lambda row: _display_expiry_label(row.get("expiry_label"), _safe_float(row.get("hours_to_expiry"))),
        axis=1,
    )
    grouped["net_gex_b"] = grouped["signed_gex"] / 1000.0
    grouped["call_gex_b"] = grouped["call_gex"] / 1000.0
    grouped["put_gex_b"] = grouped["put_gex"] / 1000.0
    grouped["hover_text"] = grouped.apply(
        lambda row: (
            f"Expiry {row['expiry_display']}<br>"
            f"Strike {row['strike']:,.0f}<br>"
            f"Net GEX {row['net_gex_b']:+.2f}B<br>"
            f"Call GEX {row['call_gex_b']:.2f}B<br>"
            f"Put GEX {row['put_gex_b']:.2f}B<br>"
            f"OI {row['total_oi']:,.0f} BTC"
        ),
        axis=1,
    )

    x_values = sorted(grouped["strike"].unique())
    y_order = (
        grouped[["expiration_ts", "expiry_display"]]
        .drop_duplicates()
        .sort_values("expiration_ts")["expiry_display"]
        .tolist()
    )
    z = grouped.pivot(index="expiry_display", columns="strike", values="net_gex_b").reindex(index=y_order, columns=x_values)
    hover = grouped.pivot(index="expiry_display", columns="strike", values="hover_text").reindex(index=y_order, columns=x_values)
    fig.add_heatmap(
        x=x_values,
        y=y_order,
        z=z.to_numpy(),
        text=hover.to_numpy(),
        hovertemplate="%{text}<extra></extra>",
        colorscale=[
            [0.0, "#f472b6"],
            [0.48, "#253021"],
            [0.5, "#111811"],
            [0.52, "#2d3b2b"],
            [1.0, "#9fab95"],
        ],
        zmid=0.0,
        colorbar=dict(title="Net GEX ($B)"),
        xgap=1,
        ygap=1,
        hoverongaps=False,
    )
    fig.add_vline(x=spot, line_color="#e4eadf", line_dash="dash")
    fig.update_layout(
        title="Strike x Expiry Gamma Heatmap",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=30, t=60, b=35),
        xaxis_title="Strike",
        yaxis_title="Expiry",
        height=380,
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=False, linecolor=APP_BORDER)
    return fig


def _build_iv_curve_chart(atm_iv: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=atm_iv["expiry_label"], y=atm_iv["effective_iv"], mode="lines+markers", line=dict(color="#e4eadf"))
    fig.update_layout(
        title="ATM IV by Expiry",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Expiry",
        yaxis_title="ATM IV",
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _build_risk_reversal_chart(risk_reversal: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not risk_reversal.empty:
        fig.add_scatter(
            x=risk_reversal["expiry_label"],
            y=risk_reversal["risk_reversal"],
            mode="lines+markers",
            line=dict(color="#f4d35e"),
            name="25D Call IV - Put IV",
        )
        fig.add_hline(y=0.0, line_dash="dot", line_color="#8f9a8b")
    fig.update_layout(
        title="25D Risk Reversal by Expiry",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Expiry",
        yaxis_title="RR (IV pts)",
    )
    fig.update_xaxes(showgrid=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _build_avwap_chart(avwap_df: pd.DataFrame, anchor_ts: pd.Timestamp) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=avwap_df["ts"],
            open=avwap_df["open"],
            high=avwap_df["high"],
            low=avwap_df["low"],
            close=avwap_df["close"],
            name="BTCUSDT Perp",
            increasing_line_color="#dfe7d8",
            decreasing_line_color="#8f9a8b",
            showlegend=False,
        )
    )
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["avwap"], mode="lines", name="Anchored VWAP", line=dict(color="#3b82f6", width=2))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_1_up"], mode="lines", name="+1sigma", line=dict(color="#59705a", dash="dot"))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_1_dn"], mode="lines", name="-1sigma", line=dict(color="#59705a", dash="dot"))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_2_up"], mode="lines", name="+2sigma", line=dict(color="#f472b6", dash="dash"))
    fig.add_scatter(x=avwap_df["ts"], y=avwap_df["band_2_dn"], mode="lines", name="-2sigma", line=dict(color="#f472b6", dash="dash"))
    fig.add_vline(x=anchor_ts, line_color="#e4eadf", line_dash="dot")
    fig.update_layout(
        title=f"Anchored VWAP Dashboard ({anchor_ts.strftime('%Y-%m-%d %H:%M UTC')})",
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        margin=dict(l=30, r=20, t=60, b=30),
        xaxis_title="Time",
        yaxis_title="BTCUSDT Perp",
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _render_gex_levels(levels: pd.DataFrame, title: str, field: str):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    if levels.empty:
        st.caption("No levels found in the current simple GEX window.")
        return
    rows = []
    for _, row in levels.iterrows():
        distance = _safe_float(row.get("distance_pct"))
        rows.append(
            f"""
            <div class="gex-level-item">
                <span class="gex-chip">{row['strike']:,.0f}</span>
                <span class="gex-chip">{field}: {_format_gex_billions(row[field])}</span>
                <span class="gex-chip">OI: {row['total_oi']:.2f} BTC</span>
                <span class="gex-chip">Distance: {distance:+.2f}%</span>
            </div>
            """
        )
    render(
        f"""
        <div class="gex-level-list">
            {''.join(rows)}
        </div>
        """
    )


def _render_options_snapshot_table(summary_rows: pd.DataFrame):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    body_rows = "".join(
        f"<tr><td>{escape(str(row['Metric']))}</td><td>{escape(str(row['Value']))}</td></tr>"
        for _, row in summary_rows.iterrows()
    )
    render(
        f"""
        <table class="options-snapshot-table">
            <thead>
                <tr><th>Metric</th><th>Value</th></tr>
            </thead>
            <tbody>
                {body_rows}
            </tbody>
        </table>
        """
    )


def _jarvis_vwap_read(latest: pd.Series) -> str:
    close = _safe_float(latest.get("close"))
    avwap = _safe_float(latest.get("avwap"))
    band_1_up = _safe_float(latest.get("band_1_up"))
    band_1_dn = _safe_float(latest.get("band_1_dn"))
    band_2_up = _safe_float(latest.get("band_2_up"))
    band_2_dn = _safe_float(latest.get("band_2_dn"))
    if close >= band_2_up and band_2_up > 0:
        return "price is above +2 sigma from anchored VWAP, which is stretched and easier to fade than chase"
    if close <= band_2_dn and band_2_dn > 0:
        return "price is below -2 sigma from anchored VWAP, which is stretched to the downside and vulnerable to snapback"
    if close > band_1_up and band_1_up > 0:
        return "price is above anchored VWAP and leaning strong, but already outside the first deviation band"
    if close < band_1_dn and band_1_dn > 0:
        return "price is below anchored VWAP and trading weak beneath the first deviation band"
    if close >= avwap:
        return "price is holding above anchored VWAP, which keeps intraday structure constructive"
    return "price is below anchored VWAP, which keeps intraday structure softer unless VWAP is reclaimed"


def _jarvis_funding_read(funding_rate: float, oi_change_1h: float) -> str:
    abs_rate = abs(funding_rate)
    if abs_rate < 0.0001:
        funding_text = "funding is calm"
    elif abs_rate < 0.0004:
        funding_text = "funding is elevated but not extreme"
    else:
        funding_text = "funding is stretched and crowding risk is higher"

    if oi_change_1h > 0.01:
        oi_text = "OI is expanding, which means new exposure is joining the move"
    elif oi_change_1h < -0.01:
        oi_text = "OI is contracting, which points more toward de-risking or squeeze dynamics than clean new positioning"
    else:
        oi_text = "OI is roughly flat, so the tape is not showing a major fresh positioning surge right now"
    return f"{funding_text}; {oi_text}."


def _jarvis_iv_read(atm_iv: pd.DataFrame) -> str:
    if atm_iv.empty:
        return "ATM IV is unavailable from the current snapshot."
    first_iv = _safe_float(atm_iv["effective_iv"].iloc[0])
    last_iv = _safe_float(atm_iv["effective_iv"].iloc[-1])
    slope = last_iv - first_iv
    if slope > 2.0:
        shape = "the IV curve is upward sloping, which usually means the front is calmer than later expiries"
    elif slope < -2.0:
        shape = "the IV curve is front-loaded, which usually means near-term stress or event premium is heavier"
    else:
        shape = "the IV curve is fairly flat, so there is no dramatic near-term vol distortion"
    return f"Front ATM IV is {first_iv:.1f}; {shape}."


def _jarvis_sweep_read(sweeps: pd.DataFrame) -> str:
    if sweeps.empty:
        return "No recent 5-minute sweep candidates were detected by the current wick, volume, and OI rules."
    latest = sweeps.sort_values("ts").iloc[-1]
    direction = str(latest.get("direction", "Sweep"))
    broken_level = _safe_float(latest.get("broken_level"))
    vol_z = _safe_float(latest.get("volume_z"))
    taker_imb = _safe_float(latest.get("taker_imbalance"))
    oi_delta = _safe_float(latest.get("oi_value_change"))
    return (
        f"Latest sweep signal is <strong>{escape(direction)}</strong> through {broken_level:,.0f}, "
        f"with volume z-score {vol_z:.2f}, taker imbalance {taker_imb:+.2f}, and OI change {oi_delta:+.2%}."
    )


def _jarvis_level_line(levels: pd.DataFrame, field: str, label: str, metric_label: str) -> str:
    if levels.empty:
        return f"No nearby {label.lower()} level is available in the current GEX window."
    top = levels.iloc[0]
    strike = _safe_float(top.get("strike"))
    gex = _format_gex_billions(_safe_float(top.get(field)))
    distance = _safe_float(top.get("distance_pct"))
    oi = _safe_float(top.get("total_oi"))
    return f"Nearest high-probability {label.lower()} is {strike:,.0f} with {metric_label} {gex}, OI {oi:,.0f} BTC, and distance {distance:+.2f}%."


def _jarvis_level_list(levels: pd.DataFrame, field: str, label: str) -> str:
    if levels.empty:
        return f"<li>No nearby {escape(label.lower())} levels are available in the current GEX window.</li>"
    items = []
    for _, row in levels.head(3).iterrows():
        strike = _safe_float(row.get("strike"))
        gex = _format_gex_billions(_safe_float(row.get(field)))
        distance = _safe_float(row.get("distance_pct"))
        items.append(
            f"<li><strong>{strike:,.0f}</strong>: {gex} GEX, {distance:+.2f}% from spot.</li>"
        )
    return "".join(items)


def _jarvis_dealer_hedging_section(
    spot: float,
    gamma_flip: object,
    signed_gex: float,
    support_levels: pd.DataFrame,
    resistance_levels: pd.DataFrame,
) -> str:
    flip = float(gamma_flip) if gamma_flip else 0.0
    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0
    support_gex = _format_gex_billions(_safe_float(support_levels.iloc[0].get("put_gex"))) if top_support else "n/a"
    resistance_gex = _format_gex_billions(_safe_float(resistance_levels.iloc[0].get("call_gex"))) if top_resistance else "n/a"

    if signed_gex > 0 and (not flip or spot >= flip):
        regime = "The visible map leans long gamma / stabilizing."
        dealer_action = "Dealers are more likely to lean against moves: selling strength and buying weakness."
        regime_effect = "That can compress volatility and help price stay range-bound around heavy strikes."
    elif signed_gex < 0 and (not flip or spot < flip):
        regime = "The visible map leans short gamma / unstable."
        dealer_action = "Dealers are more likely to chase the move: buying as price rises and selling as price falls."
        regime_effect = "That can expand volatility once a key level breaks."
    else:
        regime = "The visible map is mixed."
        dealer_action = "Dealer hedging pressure is less one-sided right now."
        regime_effect = "The important read is whether price accepts above resistance or loses support with perp flow confirming."

    flip_line = (
        f"The gamma flip near {flip:,.0f} is the main regime switch. Above it, hedge flow should be more stabilizing; below it, hedge flow can become more momentum-following."
        if flip
        else "No clean gamma flip is available in this snapshot, so use the nearest support and resistance zones as the practical hedge-pressure levels."
    )
    downside_line = (
        f"If BTC loses {top_support:,.0f}, the put-heavy support zone ({support_gex}) is the key downside hedge trigger. A clean break below it can make dealers sell into weakness, especially if spot is also below the gamma flip."
        if top_support
        else "No clean downside put-heavy support zone is available in this snapshot."
    )
    upside_line = (
        f"If BTC pushes into {top_resistance:,.0f}, the call-heavy resistance zone ({resistance_gex}) is the key upside hedge zone. In a long-gamma regime this can force selling into strength and create pinning; a clean acceptance through it weakens that cap."
        if top_resistance
        else "No clean upside call-heavy resistance zone is available in this snapshot."
    )

    return f"""
        <h4>Dealer Hedging Pressure</h4>
        <p>{escape(regime)} <strong>{escape(dealer_action)}</strong> {escape(regime_effect)}</p>
        <ul>
            <li><strong>Regime switch:</strong> {escape(flip_line)}</li>
            <li><strong>Downside trigger:</strong> {escape(downside_line)}</li>
            <li><strong>Upside hedge zone:</strong> {escape(upside_line)}</li>
        </ul>
    """


def _jarvis_options_pressure_section(bundle: dict[str, object]) -> str:
    front_24h = bundle.get("front_24h", {})
    front_7d = bundle.get("front_7d", {})
    pin = bundle.get("pin", {})
    iv_term = bundle.get("iv_term", {})
    risk_reversal = bundle.get("risk_reversal", pd.DataFrame())
    pressure = bundle.get("pressure_forecast", {})
    history = bundle.get("history_comparison", {})
    front_rr = _safe_float(risk_reversal["risk_reversal"].iloc[0]) if isinstance(risk_reversal, pd.DataFrame) and not risk_reversal.empty else 0.0
    return f"""
        <h4>Front-Week Options Pressure</h4>
        <ul>
            <li><strong>Front 24H GEX:</strong> {_format_gex_billions(_safe_float(front_24h.get("abs_gex")))} absolute, {_format_gex_billions(_safe_float(front_24h.get("signed_gex")), signed=True)} signed. Top strike { _safe_float(front_24h.get("top_strike")):,.0f} ({_safe_float(front_24h.get("top_distance_pct")):+.2f}% from spot).</li>
            <li><strong>Front 7D GEX:</strong> {_format_gex_billions(_safe_float(front_7d.get("abs_gex")))} absolute, {_format_gex_billions(_safe_float(front_7d.get("signed_gex")), signed=True)} signed. Use this before the full 120-day chain for today's dealer-pressure read.</li>
            <li><strong>Pin:</strong> {escape(str(pin.get("pin_copy", "No pin candidate is visible.")))}</li>
            <li><strong>Risk reversal:</strong> Front 25D call IV minus put IV is {front_rr:+.2f}; RR z-score is {_safe_float(history.get("rr_zscore")):+.2f} while history builds.</li>
            <li><strong>IV term:</strong> {escape(str(iv_term.get("term_regime", "Unavailable")))} at {_safe_float(iv_term.get("iv_ratio")):.2f}x front/back IV. {escape(str(iv_term.get("term_copy", "")))}</li>
            <li><strong>Estimated charm/vanna:</strong> {escape(str(pressure.get("pressure_copy", "Unavailable.")))}</li>
            <li><strong>Historical context:</strong> Current GEX is {_safe_float(history.get("gex_vs_30d_median")):+.1%} vs the rolling history median; 24H OI delta is {_safe_float(history.get("oi_24h_delta")):+,.0f} contracts.</li>
        </ul>
    """


def _jarvis_block_flow_section(bundle: dict[str, object]) -> str:
    flow = bundle.get("block_flow_7d", {}) if isinstance(bundle.get("block_flow_7d", {}), dict) else {}
    disagreements = bundle.get("block_disagreements", pd.DataFrame())
    update = bundle.get("block_store_update", {}) if isinstance(bundle.get("block_store_update", {}), dict) else {}
    matched = int(flow.get("matched_legs", 0) or 0)
    blocks = int(flow.get("blocks", 0) or 0)
    rfq = int(flow.get("rfq_trades", 0) or 0)
    stored = int(flow.get("stored_trades", 0) or 0)
    inserted = int(update.get("inserted", 0) or 0)
    error = str(update.get("error", ""))

    if matched <= 0:
        status = (
            f"No matched block-flow gamma estimate is available yet. Stored block legs: {stored:,}. "
            "The page will improve as the local 30D block-trade store accumulates."
        )
        if error:
            status += f" Latest Deribit block poll error: {error}"
        return f"""
            <h4>Block-Adjusted Gamma Map</h4>
            <p>{escape(status)}</p>
        """

    confidence = "Medium" if rfq > 0 else "Medium-Low"
    if isinstance(disagreements, pd.DataFrame) and not disagreements.empty:
        items = []
        for _, row in disagreements.iterrows():
            strike = _safe_float(row.get("strike"))
            raw = _format_gex_billions(_safe_float(row.get("signed_gex")), signed=True)
            block = _format_gex_billions(_safe_float(row.get("block_adjusted_gex")), signed=True)
            distance = _safe_float(row.get("distance_pct"))
            rfq_count = int(_safe_float(row.get("rfq_trades")))
            items.append(
                f"<li><strong>{strike:,.0f}</strong> ({distance:+.2f}%): raw strike map {raw}, block-adjusted estimate {block}; RFQ legs {rfq_count}.</li>"
            )
        disagreement_copy = (
            "<p><strong>Important disagreement:</strong> The raw strike map and recent block flow disagree at these strikes. "
            "Treat those levels with lower confidence because institution-sized flow may be pointing to the opposite dealer-side exposure.</p>"
            f"<ul>{''.join(items)}</ul>"
        )
    else:
        disagreement_copy = (
            "<p>No major nearby disagreement is visible between the raw strike map and the 7D block-adjusted estimate. "
            "That raises confidence where block flow exists, but no-block strikes remain map-only.</p>"
        )

    return f"""
        <h4>Block-Adjusted Gamma Map</h4>
        <p><strong>Confidence: {escape(confidence)}.</strong> This is an estimate from Deribit block trades, not paid dealer inventory. Direction is interpreted as customer/aggressor side: buy = dealer short gamma, sell = dealer long gamma. Fresh inserted block legs this refresh: {inserted:,}.</p>
        <p>7D matched block flow: {matched:,} legs across {blocks:,} blocks; RFQ-tagged legs: {rfq:,}. Total block-adjusted gamma estimate: {_format_gex_billions(_safe_float(flow.get("total_block_gex")), signed=True)}.</p>
        {disagreement_copy}
    """


def _jarvis_plain_vwap_state(latest: pd.Series) -> tuple[str, str]:
    close = _safe_float(latest.get("close"))
    avwap = _safe_float(latest.get("avwap"))
    band_1_up = _safe_float(latest.get("band_1_up"))
    band_1_dn = _safe_float(latest.get("band_1_dn"))
    band_2_up = _safe_float(latest.get("band_2_up"))
    band_2_dn = _safe_float(latest.get("band_2_dn"))
    if close >= band_2_up and band_2_up > 0:
        return (
            "stretched above anchored VWAP",
            "BTC is trading above the +2 sigma VWAP band. That usually means the move is hot; chasing late longs is riskier unless price keeps accepting above the band.",
        )
    if close <= band_2_dn and band_2_dn > 0:
        return (
            "stretched below anchored VWAP",
            "BTC is trading below the -2 sigma VWAP band. That usually means downside is extended; shorts need confirmation instead of blindly pressing lows.",
        )
    if close >= avwap:
        return (
            "above anchored VWAP",
            "BTC is above anchored VWAP. For a novice read, that means buyers currently control the intraday average price from the selected anchor.",
        )
    return (
        "below anchored VWAP",
        "BTC is below anchored VWAP. For a novice read, that means sellers currently control the intraday average price from the selected anchor.",
    )


def _positioning_color(state: str) -> tuple[str, str]:
    mapping = {
        "green": ("#7ef0a0", "positioning-green"),
        "yellow": ("#f4d35e", "positioning-yellow"),
        "red": ("#ff6b6b", "positioning-red"),
    }
    return mapping.get(state, mapping["yellow"])


def _build_institutional_positioning_cards(bundle: dict[str, object]) -> list[dict[str, str]]:
    spot = _safe_float(bundle.get("spot"))
    gamma_flip = bundle.get("gamma_flip")
    signed_gex = _safe_float(bundle.get("total_signed_gex"))
    support_levels = bundle.get("support_levels", pd.DataFrame())
    resistance_levels = bundle.get("resistance_levels", pd.DataFrame())
    perp = bundle.get("perp_snapshot", {})
    ibit = bundle.get("ibit_context", {})
    avwap_df = bundle.get("avwap_df", pd.DataFrame())
    sweeps = bundle.get("sweeps", pd.DataFrame())

    ibit_flow_state = str(ibit.get("flow_state", "Neutral"))
    if ibit_flow_state == "Supportive":
        ibit_state = "green"
        ibit_status = "Bullish"
    elif ibit_flow_state == "Weak":
        ibit_state = "red"
        ibit_status = "Bearish"
    else:
        ibit_state = "yellow"
        ibit_status = "Neutral"
    ibit_copy = str(ibit.get("flow_copy", "ETF flow is unavailable right now."))

    if signed_gex > 0 and gamma_flip and spot >= float(gamma_flip):
        gex_state = "green"
        gex_status = "Constructive"
        gex_copy = f"Positive net gamma and spot above the flip near {float(gamma_flip):,.0f} point to a calmer, more supportive options regime."
    elif signed_gex < 0 and gamma_flip and spot < float(gamma_flip):
        gex_state = "red"
        gex_status = "Volatile"
        gex_copy = f"Negative net gamma and spot below the flip near {float(gamma_flip):,.0f} favor faster moves and shakier downside reactions."
    else:
        gex_state = "yellow"
        gex_status = "Mixed"
        gex_copy = "Options structure is not cleanly one-sided right now, so use the strike map and perp tape together."

    support_distance = _safe_float(support_levels.iloc[0].get("distance_pct")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    resistance_distance = _safe_float(resistance_levels.iloc[0].get("distance_pct")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0
    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0
    if top_support and spot > top_support and resistance_distance > 1.5:
        levels_state = "green"
        levels_status = "Room Above"
        levels_copy = f"BTC is sitting above nearby support at {top_support:,.0f} with some room before the first major resistance at {top_resistance:,.0f}."
    elif top_support and spot <= top_support:
        levels_state = "red"
        levels_status = "Fragile"
        levels_copy = f"BTC is leaning on or below the nearest support zone around {top_support:,.0f}; breaks here deserve respect."
    else:
        levels_state = "yellow"
        levels_status = "Trapped"
        levels_copy = f"BTC is caught between nearby support and resistance. Expect more level-to-level behavior than open air movement."

    funding_rate = abs(_safe_float(perp.get("last_funding_rate")))
    oi_change_1h = _safe_float(bundle.get("oi_change_1h"))
    if funding_rate < 0.0004 and 0.0 <= oi_change_1h <= 0.03:
        perp_state = "green"
        perp_status = "Healthy"
        perp_copy = "Funding is not crowded and OI expansion still looks constructive rather than overheated."
    elif funding_rate >= 0.0008 or oi_change_1h > 0.05:
        perp_state = "red"
        perp_status = "Crowded"
        perp_copy = "Perp positioning looks stretched. Late leverage is more likely to destabilize the move than support it."
    else:
        perp_state = "yellow"
        perp_status = "Mixed"
        perp_copy = "Perp leverage is active, but not clean enough to call supportive or outright dangerous on its own."

    if isinstance(avwap_df, pd.DataFrame) and not avwap_df.empty:
        latest = avwap_df.iloc[-1]
        close = _safe_float(latest.get("close"))
        avwap = _safe_float(latest.get("avwap"))
        band_2_up = _safe_float(latest.get("band_2_up"))
        band_2_dn = _safe_float(latest.get("band_2_dn"))
        latest_sweep = None if sweeps.empty else str(sweeps.sort_values("ts").iloc[-1].get("direction", ""))
        if close > avwap and close < band_2_up and (latest_sweep.startswith("Down-sweep") or latest_sweep is None):
            tape_state = "green"
            tape_status = "Supportive"
            tape_copy = "Price is above anchored VWAP and the latest tape does not show a fresh bearish rejection."
        elif close < avwap and latest_sweep.startswith("Up-sweep"):
            tape_state = "red"
            tape_status = "Weak"
            tape_copy = "Price is below anchored VWAP and the latest sweep behavior leans bearish, so upside needs cleaner proof."
        else:
            tape_state = "yellow"
            tape_status = "Chop"
            tape_copy = "VWAP and the latest sweep/tape behavior are mixed, so execution quality matters more than broad bias."
    else:
        tape_state = "yellow"
        tape_status = "Mixed"
        tape_copy = "Anchored VWAP context is unavailable right now."

    cards = [
        {"title": "IBIT Flow", "status": ibit_status, "state": ibit_state, "copy": ibit_copy},
        {"title": "GEX Regime", "status": gex_status, "state": gex_state, "copy": gex_copy},
        {"title": "Dealer Levels", "status": levels_status, "state": levels_state, "copy": levels_copy},
        {"title": "Perp Positioning", "status": perp_status, "state": perp_state, "copy": perp_copy},
        {"title": "VWAP / Tape", "status": tape_status, "state": tape_state, "copy": tape_copy},
    ]
    return cards


def _render_institutional_positioning_summary(bundle: dict[str, object]):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    cards = _build_institutional_positioning_cards(bundle)
    fragments = []
    for card in cards:
        dot_color, class_name = _positioning_color(str(card["state"]))
        fragments.append(
            f"""
            <div class="positioning-card">
                <div class="positioning-head">
                    <span class="positioning-dot" style="background:{dot_color};"></span>
                    <div class="positioning-title">{escape(str(card["title"]))}</div>
                </div>
                <div class="positioning-status {class_name}">{escape(str(card["status"]))}</div>
                <div class="positioning-copy">{escape(str(card["copy"]))}</div>
            </div>
            """
        )
    render(f'<div class="positioning-grid">{"".join(fragments)}</div>')


def _build_jarvis_summary(bundle: dict[str, object], anchor_mode: str) -> str:
    spot = _safe_float(bundle.get("spot"))
    gamma_flip = bundle.get("gamma_flip")
    signed_gex = _safe_float(bundle.get("total_signed_gex"))
    top5_concentration = _safe_float(bundle.get("top5_concentration"))
    support_levels = bundle.get("support_levels", pd.DataFrame())
    resistance_levels = bundle.get("resistance_levels", pd.DataFrame())
    perp = bundle.get("perp_snapshot", {})
    ibit = bundle.get("ibit_context", {})
    funding_rate = _safe_float(perp.get("last_funding_rate"))
    oi_change_1h = _safe_float(bundle.get("oi_change_1h"))
    atm_iv = bundle.get("atm_iv", pd.DataFrame())
    sweeps = bundle.get("sweeps", pd.DataFrame())

    if signed_gex < 0:
        big_picture = (
            "Put-side gamma is dominating this snapshot. In simple terms, the options map is more sensitive to downside levels, "
            "and breaks below key support can become faster if perp flow confirms."
        )
    elif signed_gex > 0:
        big_picture = (
            "Call-side gamma is dominating this snapshot. In simple terms, upside strike zones matter more right now, "
            "and BTC may react or slow down around the strongest resistance levels."
        )
    else:
        big_picture = "Net GEX is close to balanced, so the strike map is less directional and perp flow deserves more weight."

    flip_read = "The gamma flip is not available, so do not use it as a trigger today."
    if gamma_flip:
        flip = float(gamma_flip)
        if spot >= flip:
            flip_read = f"BTC is above the gamma flip near {flip:,.0f}. Staying above it supports a more constructive intraday read."
        else:
            flip_read = f"BTC is below the gamma flip near {flip:,.0f}. Reclaiming it would improve the bullish read; rejection keeps pressure on."

    support_text = _jarvis_level_line(support_levels, "put_gex", "Support", "put GEX")
    resistance_text = _jarvis_level_line(resistance_levels, "call_gex", "Resistance", "call GEX")
    funding_text = _jarvis_funding_read(funding_rate, oi_change_1h)
    iv_text = _jarvis_iv_read(atm_iv)
    sweep_text = _jarvis_sweep_read(sweeps)
    ibit_state = str(ibit.get("flow_state", "Unavailable"))
    ibit_copy = str(ibit.get("flow_copy", "IBIT flow context is unavailable right now."))
    ibit_price = _safe_float(ibit.get("price"))
    ibit_return = _safe_float(ibit.get("session_return"))
    ibit_ratio = _safe_float(ibit.get("volume_ratio"))

    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0

    bullish_case = (
        f"If BTC holds above {top_support:,.0f} and flow stays supportive, the cleaner long idea is a push toward {top_resistance:,.0f}."
        if top_support and top_resistance
        else "If BTC holds its intraday structure, the bullish case improves; use the visible GEX resistance zones as upside checkpoints."
    )
    bearish_case = (
        f"If BTC loses {top_support:,.0f} with weak tape and negative OI/funding confirmation, the next support zones become more important."
        if top_support
        else "If BTC loses intraday structure with weak perp flow, the bearish case improves; use the visible support zones as downside checkpoints."
    )
    resistance_case = (
        f"If BTC reaches {top_resistance:,.0f}, watch whether it accepts above that level or rejects. Acceptance can open continuation; rejection makes it a fade/pullback zone."
        if top_resistance
        else "If BTC reaches a visible resistance zone, watch acceptance vs rejection rather than treating the level as guaranteed."
    )

    support_items = _jarvis_level_list(support_levels, "put_gex", "Support")
    resistance_items = _jarvis_level_list(resistance_levels, "call_gex", "Resistance")
    dealer_hedging_section = _jarvis_dealer_hedging_section(
        spot,
        gamma_flip,
        signed_gex,
        support_levels,
        resistance_levels,
    )
    options_pressure_section = _jarvis_options_pressure_section(bundle)
    block_flow_section = _jarvis_block_flow_section(bundle)

    return f"""
        <h4>Big Picture</h4>
        <p><strong>Spot:</strong> {spot:,.2f}. <strong>Net GEX:</strong> {_format_gex_billions(signed_gex, signed=True)}. {escape(big_picture)}</p>
        <p>{escape(flip_read)} Top-5 GEX concentration is {top5_concentration:.1%}, so a small number of strikes are carrying a meaningful part of the options pressure.</p>

        <h4>Key Levels To Watch</h4>
        <p><strong>Support zones:</strong></p>
        <ul>{support_items}</ul>
        <p><strong>Resistance zones:</strong></p>
        <ul>{resistance_items}</ul>
        <p>{escape(support_text)} {escape(resistance_text)}</p>

        {dealer_hedging_section}

        {block_flow_section}

        {options_pressure_section}

        <h4>Intraday Read</h4>
        <ul>
            <li><strong>Funding / OI:</strong> {escape(funding_text)}</li>
            <li><strong>IV:</strong> {escape(iv_text)}</li>
            <li><strong>IBIT:</strong> {escape(ibit_state)} flow. {escape(ibit_copy)} Current IBIT price is {ibit_price:,.2f} with session return {ibit_return:+.2%} and volume at {ibit_ratio:.2f}x its 20-day average.</li>
            <li><strong>Sweeps:</strong> {sweep_text}</li>
        </ul>

        <h4>How To Use This Today</h4>
        <ul>
            <li><strong>Bullish scenario:</strong> {escape(bullish_case)}</li>
            <li><strong>Bearish scenario:</strong> {escape(bearish_case)}</li>
            <li><strong>Resistance test:</strong> {escape(resistance_case)}</li>
        </ul>

        <h4>Simple Summary</h4>
        <p class="jarvis-simple-summary">Do not treat any GEX level as magic. Treat it as a map of where dealer hedging pressure may appear. The highest-alpha read is whether price is entering a stabilizing zone where hedging can compress the move, or breaking into an unstable zone where hedging can fuel expansion.</p>
    """


def _render_jarvis_widget(bundle: dict[str, object], anchor_mode: str):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    summary_html = _build_jarvis_summary(bundle, anchor_mode)
    render(
        f"""
        <details class="jarvis-fab">
            <summary>Ask Jarvis</summary>
            <div class="jarvis-panel">
                <div class="jarvis-panel-head">
                    <div class="jarvis-panel-title">Ask Jarvis</div>
                    <div class="jarvis-panel-copy">Read-only live interpreter of the current BTC options screener state. It explains what the current snapshot is implying; it does not invent data or place trades for you.</div>
                </div>
                <div class="jarvis-faq">
                    <details class="jarvis-faq-item" open>
                        <summary>Summarise how to use current BTC options data in my daytrading</summary>
                        <div class="jarvis-answer">
                            {summary_html}
                        </div>
                    </details>
                </div>
            </div>
        </details>
        """
    )


def _render_btc_options_cockpit(anchor_mode: str):
    status = st.empty()
    status.info("Building BTC options screener from public Deribit + Binance data...")
    try:
        bundle = build_btc_options_cockpit(anchor_mode)
    except Exception as exc:
        status.empty()
        st.error(f"BTC options screener failed to load: {exc}")
        st.caption("This page depends on live public Deribit and Binance endpoints. Try Force refresh in a moment.")
        return
    status.empty()
    if "error" in bundle:
        st.error(str(bundle["error"]))
        return

    spot = float(bundle["spot"])
    options_df = bundle["options_df"]
    gamma_flip = bundle["gamma_flip"]
    perp = bundle["perp_snapshot"]
    strike_map = bundle["strike_map"]
    strike_expiry_context = bundle.get("strike_expiry_context", strike_map)
    strike_expiry_breakdown = bundle.get("strike_expiry_breakdown", pd.DataFrame())
    expiry_map = bundle["expiry_map"]
    atm_iv = bundle["atm_iv"]
    avwap_df = bundle["avwap_df"]
    sweeps = bundle["sweeps"]
    support_levels = bundle["support_levels"]
    resistance_levels = bundle["resistance_levels"]
    ibit = bundle["ibit_context"]
    front_24h = bundle["front_24h"]
    front_7d = bundle["front_7d"]
    pin = bundle["pin"]
    iv_term = bundle["iv_term"]
    risk_reversal = bundle["risk_reversal"]
    pressure_forecast = bundle["pressure_forecast"]
    history_comparison = bundle["history_comparison"]
    block_flow_7d = bundle.get("block_flow_7d", {})
    block_flow_30d = bundle.get("block_flow_30d", {})
    block_store_update = bundle.get("block_store_update", {})
    block_disagreements = bundle.get("block_disagreements", pd.DataFrame())

    st.title("BTC Options Screener")
    st.caption(
        "Phase 1 public-data framework using Deribit BTC options and Binance BTCUSDT perpetuals. "
        "GEX here is a simple call-minus-put gamma approximation built from Deribit open interest and greeks."
    )

    st.markdown("### Institutional Positioning Summary")
    _render_institutional_positioning_summary(bundle)

    st.markdown("### IBIT Flow Context")
    if isinstance(ibit, dict) and "error" in ibit:
        st.caption(str(ibit["error"]))
    else:
        ib1, ib2, ib3, ib4, ib5 = st.columns(5)
        ib1.metric("IBIT Price", f"{_safe_float(ibit.get('price')):,.2f}")
        ib2.metric("Session Return", f"{_safe_float(ibit.get('session_return')):+.2%}")
        ib3.metric("Session Volume", _format_human_count(_safe_float(ibit.get("session_volume"))))
        ib4.metric("20D Avg Volume", _format_human_count(_safe_float(ibit.get("avg_20d_volume"))))
        ib5.metric("ETF Flow", str(ibit.get("flow_state", "Unavailable")))
        market_ts = ibit.get("market_ts")
        if isinstance(market_ts, pd.Timestamp):
            market_copy = market_ts.strftime("%Y-%m-%d %H:%M UTC")
        else:
            market_copy = "latest available session data"
        st.caption(
            f"IBIT is used here as a US-session spot-demand confirmation layer. Volume is running at "
            f"{_safe_float(ibit.get('volume_ratio')):.2f}x its 20-day average as of {market_copy}. {str(ibit.get('flow_copy', ''))}"
        )

    metric_help = {
        "BTC Spot": "Current BTC reference price used to anchor option strikes, moneyness, and distance calculations.",
        "OI 1H": "One-hour change in Binance BTCUSDT perpetual open interest; rising OI often means new leverage is entering.",
        "Gamma Flip": "Estimated BTC price where dealer gamma exposure changes sign, often shifting hedging from stabilizing to amplifying moves.",
        "Net GEX Approx": "Simple call-minus-put gamma exposure estimate across the visible Deribit option chain.",
        "Front 24H GEX": "Absolute gamma exposure in options expiring within the next 24 hours.",
        "Front 7D GEX": "Absolute gamma exposure in options expiring within the next seven days.",
        "Pin Score": "How strongly nearby option gamma may pull BTC toward a candidate strike into expiry.",
        "Funding 8H": "Latest eight-hour BTCUSDT perpetual funding rate; positive means longs pay shorts.",
        "ATM IV": "At-the-money implied volatility for the nearest liquid BTC options expiry.",
        "IV Term": "Front-expiry IV versus back-expiry IV; contango means back IV is higher, backwardation means front IV is higher.",
        "Front RR": "Front-expiry risk reversal, comparing call IV to put IV; positive favors calls, negative favors puts.",
        "Pressure Est.": "Estimated next hedging pressure bias from the current gamma and charm/vanna context.",
        "7D Block GEX Est.": "Seven-day block-trade-adjusted gamma estimate from matched Deribit block option legs.",
        "Block Legs": "Number of matched Deribit block option legs used in the seven-day block-flow estimate.",
        "RFQ Legs": "Matched block legs tagged as RFQ, weighted as higher-confidence institutional flow.",
        "30D Stored Blocks": "Number of distinct Deribit block trades currently stored in the local 30-day block-flow database.",
    }

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("BTC Spot", f"{spot:,.2f}", help=metric_help["BTC Spot"])
    c2.metric("OI 1H", f"{float(bundle['oi_change_1h']):+.2%}", help=metric_help["OI 1H"])
    c3.metric("Gamma Flip", f"{gamma_flip:,.0f}" if gamma_flip else "n/a", help=metric_help["Gamma Flip"])
    c4.metric("Net GEX Approx", _format_gex_billions(float(bundle["total_signed_gex"]), signed=True), help=metric_help["Net GEX Approx"])
    c5.metric("Front 24H GEX", _format_gex_billions(float(front_24h["abs_gex"])), help=metric_help["Front 24H GEX"])
    c6.metric("Front 7D GEX", _format_gex_billions(float(front_7d["abs_gex"])), help=metric_help["Front 7D GEX"])

    f1, f2, f3, f4, f5, f6 = st.columns(6)
    f1.metric("Pin Score", f"{_safe_float(pin.get('pin_score')):.0f}/100", help=metric_help["Pin Score"])
    f2.metric("Funding 8H", f"{_safe_float(perp.get('last_funding_rate')):.4%}", help=metric_help["Funding 8H"])
    f3.metric("ATM IV", f"{float(atm_iv['effective_iv'].iloc[0]):.1f}" if not atm_iv.empty else "n/a", help=metric_help["ATM IV"])
    f4.metric("IV Term", str(iv_term.get("term_regime", "n/a")), f"{_safe_float(iv_term.get('iv_ratio')):.2f}x", help=metric_help["IV Term"])
    f5.metric("Front RR", f"{_safe_float(risk_reversal['risk_reversal'].iloc[0]):+.2f}" if not risk_reversal.empty else "n/a", help=metric_help["Front RR"])
    f6.metric("Pressure Est.", str(pressure_forecast.get("pressure_bias", "Neutral")), help=metric_help["Pressure Est."])

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("7D Block GEX Est.", _format_gex_billions(_safe_float(block_flow_7d.get("total_block_gex")), signed=True), help=metric_help["7D Block GEX Est."])
    b2.metric("Block Legs", f"{int(block_flow_7d.get('matched_legs', 0)):,}", f"+{int(block_store_update.get('inserted', 0)):,} new", help=metric_help["Block Legs"])
    b3.metric("RFQ Legs", f"{int(block_flow_7d.get('rfq_trades', 0)):,}", help=metric_help["RFQ Legs"])
    b4.metric("30D Stored Blocks", f"{int(block_flow_30d.get('blocks', 0)):,}", help=metric_help["30D Stored Blocks"])

    st.caption(
        f"{str(pin.get('pin_copy', 'No pin candidate.'))} "
        f"{str(iv_term.get('term_copy', ''))} "
        f"{str(pressure_forecast.get('pressure_copy', ''))}"
    )
    st.caption(
        "Block-adjusted gamma is an estimate from Deribit block trades only. "
        "Direction is interpreted as customer/aggressor side, RFQ-tagged legs get higher confidence, and strikes without block flow remain map-only."
    )

    s1, s2 = st.columns(2)
    with s1:
        st.markdown("### Potential GEX Support")
        _render_gex_levels(support_levels, "Support", "put_gex")
    with s2:
        st.markdown("### Potential GEX Resistance")
        _render_gex_levels(resistance_levels, "Resistance", "call_gex")

    st.caption(
        "Support/resistance levels are ranked from strike-level put/call gamma concentration near spot. "
        "Treat them as probabilistic levels, not guaranteed barriers."
    )

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.plotly_chart(_build_gex_strike_chart(strike_expiry_context, spot), use_container_width=True)
    with chart_right:
        st.plotly_chart(_build_expiry_map_chart(expiry_map), use_container_width=True)

    st.markdown("### Strike Expiry Breakdown")
    if isinstance(strike_expiry_breakdown, pd.DataFrame) and not strike_expiry_breakdown.empty:
        display_breakdown = strike_expiry_breakdown[
            [
                "strike",
                "distance_pct",
                "total_gex_b",
                "call_gex_b",
                "put_gex_b",
                "net_gex_b",
                "dominant_expiry",
                "dominant_share",
                "top_call_expiry",
                "top_put_expiry",
                "front_24h_share",
                "front_7d_share",
                "total_oi",
            ]
        ].copy()
        for share_col in ["dominant_share", "front_24h_share", "front_7d_share"]:
            display_breakdown[share_col] = display_breakdown[share_col] * 100.0
        st.dataframe(
            display_breakdown,
            use_container_width=True,
            height=320,
            column_config={
                "strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                "distance_pct": st.column_config.NumberColumn("Distance", format="%.2f%%"),
                "total_gex_b": st.column_config.NumberColumn("Total GEX ($B)", format="%.2f"),
                "call_gex_b": st.column_config.NumberColumn("Call GEX ($B)", format="%.2f"),
                "put_gex_b": st.column_config.NumberColumn("Put GEX ($B)", format="%.2f"),
                "net_gex_b": st.column_config.NumberColumn("Net GEX ($B)", format="%+.2f"),
                "dominant_expiry": st.column_config.TextColumn("Dominant Expiry"),
                "dominant_share": st.column_config.NumberColumn("Dominant Share", format="%.0f%%"),
                "top_call_expiry": st.column_config.TextColumn("Top Call Expiry"),
                "top_put_expiry": st.column_config.TextColumn("Top Put Expiry"),
                "front_24h_share": st.column_config.NumberColumn("Front 24H", format="%.0f%%"),
                "front_7d_share": st.column_config.NumberColumn("Front 7D", format="%.0f%%"),
                "total_oi": st.column_config.NumberColumn("OI (BTC)", format="%.0f"),
            },
            hide_index=True,
        )
    else:
        st.caption("No strike-expiry breakdown is available for the current options snapshot.")

    st.plotly_chart(_build_strike_expiry_heatmap(options_df, spot), use_container_width=True)

    if isinstance(block_disagreements, pd.DataFrame) and not block_disagreements.empty:
        st.markdown("### Block Flow Disagreements")
        display_disagreements = block_disagreements[
            ["strike", "distance_pct", "signed_gex", "block_adjusted_gex", "block_abs_gex", "block_count", "rfq_trades"]
        ].copy()
        st.dataframe(
            display_disagreements,
            use_container_width=True,
            height=180,
            column_config={
                "strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                "distance_pct": st.column_config.NumberColumn("Distance", format="%.2f%%"),
                "signed_gex": st.column_config.NumberColumn("Raw Signed GEX", format="%.2f"),
                "block_adjusted_gex": st.column_config.NumberColumn("Block GEX Est.", format="%.2f"),
                "block_abs_gex": st.column_config.NumberColumn("Block Abs GEX", format="%.2f"),
                "block_count": st.column_config.NumberColumn("Blocks", format="%d"),
                "rfq_trades": st.column_config.NumberColumn("RFQ Legs", format="%d"),
            },
            hide_index=True,
        )

    curve_col, flow_col = st.columns(2)
    with curve_col:
        st.plotly_chart(_build_iv_curve_chart(atm_iv), use_container_width=True)
    with flow_col:
        summary_rows = pd.DataFrame(
            [
                {"Metric": "Total Signed GEX", "Value": _format_gex_billions(float(bundle["total_signed_gex"]), signed=True)},
                {"Metric": "Total Absolute GEX", "Value": _format_gex_billions(float(bundle["total_abs_gex"]))},
                {"Metric": "Call GEX", "Value": _format_gex_billions(float(bundle["call_gex"]))},
                {"Metric": "Put GEX", "Value": _format_gex_billions(float(bundle["put_gex"]))},
                {"Metric": "Top 5 Concentration", "Value": f"{float(bundle['top5_concentration']):.1%}"},
                {"Metric": "Perp Mark / Index", "Value": f"{_safe_float(perp.get('mark_price')):,.2f} / {_safe_float(perp.get('index_price')):,.2f}"},
                {"Metric": "Current OI Contracts", "Value": f"{_safe_float(perp.get('open_interest_contracts')):,.0f}"},
                {"Metric": "OI Value (latest)", "Value": f"{float(bundle['oi_latest_value']):,.0f}"},
                {"Metric": "7D Block GEX Estimate", "Value": _format_gex_billions(_safe_float(block_flow_7d.get("total_block_gex")), signed=True)},
                {"Metric": "7D Block Legs / Blocks", "Value": f"{int(block_flow_7d.get('matched_legs', 0)):,} / {int(block_flow_7d.get('blocks', 0)):,}"},
                {"Metric": "30D Stored Block Legs", "Value": f"{int(block_flow_30d.get('stored_trades', 0)):,}"},
            ]
        )
        st.markdown("### Options / Perp Snapshot")
        _render_options_snapshot_table(summary_rows)

    rr_col, hist_col = st.columns(2)
    with rr_col:
        st.plotly_chart(_build_risk_reversal_chart(risk_reversal), use_container_width=True)
    with hist_col:
        front_rows = pd.DataFrame(
            [
                {"Metric": "Front 24H Signed GEX", "Value": _format_gex_billions(float(front_24h["signed_gex"]), signed=True)},
                {"Metric": "Front 24H Top Strike", "Value": f"{_safe_float(front_24h.get('top_strike')):,.0f} ({_safe_float(front_24h.get('top_distance_pct')):+.2f}%)"},
                {"Metric": "Front 7D Signed GEX", "Value": _format_gex_billions(float(front_7d["signed_gex"]), signed=True)},
                {"Metric": "Front 7D Top Strike", "Value": f"{_safe_float(front_7d.get('top_strike')):,.0f} ({_safe_float(front_7d.get('top_distance_pct')):+.2f}%)"},
                {"Metric": "GEX vs History", "Value": f"{_safe_float(history_comparison.get('gex_vs_30d_median')):+.1%} vs rolling median"},
                {"Metric": "OI 24H Delta", "Value": f"{_safe_float(history_comparison.get('oi_24h_delta')):+,.0f} contracts"},
                {"Metric": "Front RR Z", "Value": f"{_safe_float(history_comparison.get('rr_zscore')):+.2f}"},
                {"Metric": "History Rows", "Value": f"{int(history_comparison.get('history_rows', 0)):,} snapshots"},
            ]
        )
        st.markdown("### Front-Week / History")
        _render_options_snapshot_table(front_rows)

    st.plotly_chart(_build_avwap_chart(avwap_df, bundle["anchor_ts"]), use_container_width=True)

    st.markdown("### Sweep Dashboard")
    if sweeps.empty:
        st.caption("No recent 5m sweep candidates were detected from the current price / wick / volume / OI rules.")
    else:
        sweep_df = sweeps.copy()
        sweep_df["ts"] = sweep_df["ts"].dt.strftime("%Y-%m-%d %H:%M")
        sweep_df["oi_value_change"] = sweep_df["oi_value_change"].map(lambda value: f"{float(value):+.2%}")
        st.dataframe(
            sweep_df.sort_values("ts", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "ts": st.column_config.TextColumn("Time"),
                "direction": st.column_config.TextColumn("Sweep Type"),
                "close": st.column_config.NumberColumn("Close", format="%.2f"),
                "volume_z": st.column_config.NumberColumn("Vol Z", format="%.2f"),
                "taker_imbalance": st.column_config.NumberColumn("Taker Imb", format="%.2f"),
                "oi_value_change": st.column_config.TextColumn("OI Change"),
                "broken_level": st.column_config.NumberColumn("Broken Level", format="%.2f"),
            },
            height=260,
        )

    _render_jarvis_widget(bundle, anchor_mode)

def _render_term_guide():
    st.subheader("Glossary / Term Guide")
    st.caption("Quick explanations for the score names and raw fields used in the screener table.")
    sections = []
    for idx, (group_name, items) in enumerate(TERM_GUIDE_GROUPS):
        cards = []
        for term, description in items:
            cards.append(
                f'<div class="term-guide-card"><div class="term-guide-title">{term}</div><div class="term-guide-copy">{description}</div></div>'
            )
        open_attr = " open" if idx == 0 else ""
        sections.append(
            f'<details class="term-guide-section"{open_attr}><summary class="term-guide-summary">{group_name}</summary><div class="term-guide-section-body"><div class="term-guide-grid">{"".join(cards)}</div></div></details>'
        )
    st.markdown("".join(sections), unsafe_allow_html=True)


def _set_page(page_name: str):
    st.session_state["app_page"] = page_name


def _render_glossary_jump():
    st.button(
        "📖 Glossary / Term Guide",
        key="open_glossary_page",
        on_click=_set_page,
        args=("Glossary / Term Guide",),
        use_container_width=True,
    )


def _term_help(term: str) -> str:
    for _, items in TERM_GUIDE_GROUPS:
        for item_term, description in items:
            if item_term == term:
                return description
    fallback = {
        "Overall HTF Strength": "Single summary of the asset's higher-timeframe momentum profile. In this view it is the HTF Momentum score.",
        "Overall LTF Strength": "Single summary of the asset's lower-timeframe momentum profile. In this view it is the LTF Scalping score.",
        "Symbol": "Binance USDT-M perpetual contract symbol.",
    }
    return fallback.get(term, "")


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
    daily_contexts = fetch_htf_daily_contexts(candidates)
    btc_context = contexts.get(BTC_SYMBOL)
    if not btc_context:
        return pd.DataFrame()
    btc_daily_context = daily_contexts.get(BTC_SYMBOL, {})
    btc_regime = _btc_daily_regime(btc_daily_context.get("daily") if isinstance(btc_daily_context, dict) else None)

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
        daily_context = daily_contexts.get(symbol, {})
        daily_metrics = _daily_swing_context(
            daily_context.get("daily") if isinstance(daily_context, dict) else None,
            daily_context.get("daily_oi", pd.DataFrame()) if isinstance(daily_context, dict) else pd.DataFrame(),
            daily_context.get("spot_daily") if isinstance(daily_context, dict) else None,
        )

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
        aligned_returns = _aligned_return_frame(close, btc_close)
        beta_by_lookback = {
            lookback: _estimate_beta(aligned_returns, lookback)
            for lookback in (72, 96, 168, 216)
        }
        alpha_1h = _alpha_from_beta(close, btc_close, 1, beta_by_lookback[72])
        alpha_4h = _alpha_from_beta(close, btc_close, 4, beta_by_lookback[72])
        alpha_24h = _alpha_from_beta(close, btc_close, 24, beta_by_lookback[96])
        alpha_72h = _alpha_from_beta(close, btc_close, 72, beta_by_lookback[168])
        alpha_168h = _alpha_from_beta(close, btc_close, 168, beta_by_lookback[216])
        vol_adj_4h = _vol_adjusted_return(close, 4)
        vol_adj_24h = _vol_adjusted_return(close, 24)
        vol_adj_72h = _vol_adjusted_return(close, 72)
        vol_adj_168h = _vol_adjusted_return(close, 168)

        volume_ratio_1h = _volume_ratio(df["quote_vol"])
        volume_z_1h = _volume_zscore(df["quote_vol"])
        z_fast = _vwap_zscore(df, VWAP_FAST)
        z_slow = _vwap_zscore(df, VWAP_SLOW)
        z_htf = _vwap_zscore(df, VWAP_HTF)
        atr_1h = _atr_series(df)
        htf_atr_value = float(atr_1h.iloc[-1]) if not atr_1h.empty and pd.notna(atr_1h.iloc[-1]) else 0.0
        htf_atr_percentile = float(_rolling_percentile(atr_1h).iloc[-1]) if not atr_1h.empty else 50.0
        if len(atr_1h) > 12 and float(atr_1h.iloc[-13]) > 1e-12:
            htf_atr_roc = float(atr_1h.iloc[-1] / atr_1h.iloc[-13] - 1.0)
        else:
            htf_atr_roc = 0.0
        htf_range_high, htf_range_low, htf_range_position, htf_range_width_atr = _range_context(df, 72, htf_atr_value)
        if price > htf_range_high and htf_range_high > 0:
            htf_breakout_distance_atr = (price - htf_range_high) / max(htf_atr_value, 1e-12)
        elif price < htf_range_low and htf_range_low > 0:
            htf_breakout_distance_atr = (price - htf_range_low) / max(htf_atr_value, 1e-12)
        else:
            htf_breakout_distance_atr = 0.0

        dist_ema20 = (price / ema20 - 1.0) if ema20 else 0.0
        dist_ema36 = (price / ema36 - 1.0) if ema36 else 0.0
        dist_ema50 = (price / ema50 - 1.0) if ema50 else 0.0
        ema_alignment = (
            float(price > ema20)
            + float(ema20 > ema36)
            + float(ema36 > ema50)
        ) / 3.0

        ltf_alpha_raw = (
            LTF_ALPHA_WEIGHTS["1h"] * alpha_1h
            + LTF_ALPHA_WEIGHTS["4h"] * alpha_4h
            + LTF_ALPHA_WEIGHTS["24h"] * alpha_24h
        )
        htf_alpha_raw = (
            HTF_ALPHA_WEIGHTS["24h"] * alpha_24h
            + HTF_ALPHA_WEIGHTS["72h"] * alpha_72h
            + HTF_ALPHA_WEIGHTS["168h"] * alpha_168h
        )
        vol_adjusted_raw = (
            VOL_ADJUSTED_WEIGHTS["24h"] * vol_adj_24h
            + VOL_ADJUSTED_WEIGHTS["72h"] * vol_adj_72h
            + VOL_ADJUSTED_WEIGHTS["168h"] * vol_adj_168h
        )
        rs_raw = LTF_RS_WEIGHTS["1h"] * rs_1h + LTF_RS_WEIGHTS["4h"] * rs_4h + LTF_RS_WEIGHTS["24h"] * rs_24h
        htf_rs_raw = (
            HTF_RS_WEIGHTS["24h"] * rs_24h
            + HTF_RS_WEIGHTS["72h"] * rs_72h
            + HTF_RS_WEIGHTS["168h"] * rs_168h
        )
        volume_raw = (
            VOLUME_BLEND_WEIGHTS["ratio"] * _tanh_scale(volume_ratio_1h - 1.0, 1.25)
            + VOLUME_BLEND_WEIGHTS["zscore"] * _tanh_scale(volume_z_1h, 0.40)
        )
        trend_raw = (
            TREND_BLEND_WEIGHTS["ema20"] * _tanh_scale(dist_ema20, 35.0)
            + TREND_BLEND_WEIGHTS["ema36"] * _tanh_scale(dist_ema36, 30.0)
            + TREND_BLEND_WEIGHTS["ema50"] * _tanh_scale(dist_ema50, 25.0)
            + TREND_BLEND_WEIGHTS["alignment"] * ema_alignment
            + TREND_BLEND_WEIGHTS["vwap"] * _tanh_scale(max(z_fast, 0.0), 0.60)
        )
        htf_trend_raw = (
            HTF_TREND_BLEND_WEIGHTS["ema36"] * _tanh_scale(dist_ema36, 30.0)
            + HTF_TREND_BLEND_WEIGHTS["ema50"] * _tanh_scale(dist_ema50, 25.0)
            + HTF_TREND_BLEND_WEIGHTS["alignment"] * ema_alignment
            + HTF_TREND_BLEND_WEIGHTS["vwap"] * _tanh_scale(max(z_htf, 0.0), 0.35)
        )
        oi_raw = _tanh_scale(oi_change, 8.0)
        overextension_raw = (
            OVEREXTENSION_WEIGHTS["vwap"] * max(z_slow, 0.0)
            + OVEREXTENSION_WEIGHTS["ema20"] * max(dist_ema20, 0.0) * 40.0
            + OVEREXTENSION_WEIGHTS["ema36"] * max(dist_ema36, 0.0) * 35.0
            + OVEREXTENSION_WEIGHTS["ret_1h"] * max(ret_1h, 0.0) * 30.0
        )
        htf_atr_compression_score = _compression_score(htf_atr_percentile, volume_z_1h, oi_raw)
        htf_atr_expansion_score = _score_from_threshold(max(htf_atr_roc, 0.0), 0.18, 75.0)
        htf_range_break_score = _score_from_threshold(abs(htf_breakout_distance_atr), 0.65, 80.0)
        htf_long_structure_score = float(
            np.clip(
                40.0 * float(price > ema36)
                + 25.0 * float(ema36 > ema50)
                + 20.0 * max(z_htf, 0.0)
                + 15.0 * max(htf_range_position - 0.50, 0.0) * 2.0,
                0.0,
                100.0,
            )
        )
        htf_short_structure_score = float(
            np.clip(
                40.0 * float(price < ema36)
                + 25.0 * float(ema36 < ema50)
                + 20.0 * max(-z_htf, 0.0)
                + 15.0 * max(0.50 - htf_range_position, 0.0) * 2.0,
                0.0,
                100.0,
            )
        )
        htf_long_rs_score = float(np.clip((rs_24h * 500.0) + (rs_72h * 350.0) + (rs_168h * 250.0), 0.0, 100.0))
        htf_short_rs_score = float(np.clip((-rs_24h * 500.0) + (-rs_72h * 350.0) + (-rs_168h * 250.0), 0.0, 100.0))
        htf_long_expansion_score = (
            0.20 * htf_atr_compression_score
            + 0.20 * htf_atr_expansion_score
            + 0.20 * volume_raw * 100.0
            + 0.15 * max(oi_raw, 0.0) * 100.0
            + 0.15 * htf_long_structure_score
            + 0.10 * htf_long_rs_score
        )
        htf_short_expansion_score = (
            0.20 * htf_atr_compression_score
            + 0.20 * htf_atr_expansion_score
            + 0.20 * volume_raw * 100.0
            + 0.15 * max(oi_raw, 0.0) * 100.0
            + 0.15 * htf_short_structure_score
            + 0.10 * htf_short_rs_score
        )
        daily_long_confirmed = bool(daily_metrics.get("daily_long_confirmed", False))
        daily_short_confirmed = bool(daily_metrics.get("daily_short_confirmed", False))
        daily_oi_persistence_days = int(daily_metrics.get("daily_oi_persistence_days", 0))
        daily_volume_persistence_days = int(daily_metrics.get("daily_volume_persistence_days", 0))
        daily_long_quality = float(daily_metrics.get("daily_long_score", 0.0))
        daily_short_quality = float(daily_metrics.get("daily_short_score", 0.0))
        htf_long_expansion_score = float(
            np.clip(
                (
                    0.72 * htf_long_expansion_score
                    + 0.28 * daily_long_quality
                    + 4.0 * min(daily_oi_persistence_days, 3)
                    + 3.0 * min(daily_volume_persistence_days, 3)
                )
                * float(btc_regime["btc_long_multiplier"]),
                0.0,
                100.0,
            )
        )
        htf_short_expansion_score = float(
            np.clip(
                (
                    0.72 * htf_short_expansion_score
                    + 0.28 * daily_short_quality
                    + 4.0 * min(daily_oi_persistence_days, 3)
                    + 3.0 * min(daily_volume_persistence_days, 3)
                )
                * float(btc_regime["btc_short_multiplier"]),
                0.0,
                100.0,
            )
        )
        if htf_long_expansion_score >= htf_short_expansion_score and htf_long_expansion_score >= 45.0:
            htf_expansion_direction = "Long expansion" if daily_long_confirmed else "Long watch"
            htf_expansion_score = htf_long_expansion_score if daily_long_confirmed else htf_long_expansion_score * 0.82
        elif htf_short_expansion_score > htf_long_expansion_score and htf_short_expansion_score >= 45.0:
            htf_expansion_direction = "Short expansion" if daily_short_confirmed else "Short watch"
            htf_expansion_score = htf_short_expansion_score if daily_short_confirmed else htf_short_expansion_score * 0.82
        elif htf_atr_percentile < 25.0:
            htf_expansion_direction = "Compression"
            htf_expansion_score = htf_atr_compression_score * 0.60
        else:
            htf_expansion_direction = "Neutral"
            htf_expansion_score = max(htf_long_expansion_score, htf_short_expansion_score)

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
                "htf_atr_value": htf_atr_value,
                "htf_atr_percentile": htf_atr_percentile,
                "htf_atr_compression_score": htf_atr_compression_score,
                "htf_atr_roc": htf_atr_roc,
                "htf_atr_expansion_score": htf_atr_expansion_score,
                "htf_breakout_distance_atr": htf_breakout_distance_atr,
                "htf_range_position": htf_range_position,
                "htf_range_width_atr": htf_range_width_atr,
                "htf_expansion_direction": htf_expansion_direction,
                "htf_expansion_score": float(np.clip(htf_expansion_score, 0.0, 100.0)),
                "htf_long_expansion_score": float(np.clip(htf_long_expansion_score, 0.0, 100.0)),
                "htf_short_expansion_score": float(np.clip(htf_short_expansion_score, 0.0, 100.0)),
                "btc_daily_regime": str(btc_regime["btc_daily_regime"]),
                "btc_daily_regime_score": float(btc_regime["btc_daily_regime_score"]),
                **daily_metrics,
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
        MOMENTUM_SCORE_WEIGHTS["alpha"] * out["alpha_score"]
        + MOMENTUM_SCORE_WEIGHTS["rs"] * out["relative_strength_score"]
        + MOMENTUM_SCORE_WEIGHTS["volume"] * out["volume_score"]
        + MOMENTUM_SCORE_WEIGHTS["trend"] * out["trend_score"]
        + MOMENTUM_SCORE_WEIGHTS["oi"] * out["oi_score"]
        + MOMENTUM_SCORE_WEIGHTS["funding"] * out["funding_quality_score"]
    )
    out["htf_momentum_score"] = (
        HTF_MOMENTUM_SCORE_WEIGHTS["alpha"] * out["htf_alpha_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["vol_adjusted"] * out["vol_adjusted_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["rs"] * out["htf_relative_strength_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["trend"] * out["htf_trend_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["oi"] * out["oi_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["funding"] * out["funding_trend_quality_score"]
    )
    out["overextension_score"] = _percentile_score(out["overextension_raw"])
    out["setup_score"] = (
        out["momentum_score"] - SETUP_OVEREXTENSION_PENALTY * out["overextension_score"]
    ).clip(lower=0.0)
    out["htf_setup_score"] = (
        out["htf_momentum_score"] - HTF_SETUP_OVEREXTENSION_PENALTY * out["overextension_score"]
    ).clip(lower=0.0)
    return out.sort_values(["momentum_score", "setup_score"], ascending=False).reset_index(drop=True)


@st.cache_data(ttl=LTF_CACHE_TTL, show_spinner=False)
def build_ltf_regime_metrics(
    symbols: tuple[str, ...],
    min_quote_volume: float,
    min_trades: float,
    min_oi_value: float,
) -> pd.DataFrame:
    ticker_stats = fetch_ticker_stats()
    candidates = _candidate_symbols(symbols, ticker_stats, min_quote_volume, min_trades)
    context_symbols = tuple(sorted(set(candidates + (ETH_SYMBOL,))))
    contexts = fetch_ltf_symbol_contexts(context_symbols)
    spot_contexts = fetch_ltf_spot_contexts(candidates)
    btc_context = contexts.get(BTC_SYMBOL)
    eth_context = contexts.get(ETH_SYMBOL)
    if not btc_context:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    btc_klines = btc_context.get("klines", {})
    eth_klines = eth_context.get("klines", {}) if eth_context else {}

    for symbol in candidates:
        if symbol == BTC_SYMBOL:
            continue
        context = contexts.get(symbol)
        if not context:
            continue

        klines = context.get("klines", {})
        oi_hist = context.get("oi_hist", {})
        spot_klines = spot_contexts.get(symbol, {}).get("klines", {})
        if not isinstance(klines, dict) or not isinstance(oi_hist, dict):
            continue
        if not isinstance(spot_klines, dict):
            spot_klines = {}

        latest_oi_values = [
            float(frame["oi_value"].iloc[-1])
            for frame in oi_hist.values()
            if isinstance(frame, pd.DataFrame) and not frame.empty
        ]
        oi_value = max(latest_oi_values) if latest_oi_values else 0.0
        if oi_value < min_oi_value:
            continue

        for interval in LTF_INTERVALS:
            df = klines.get(interval)
            btc_df = btc_klines.get(interval) if isinstance(btc_klines, dict) else None
            eth_df = eth_klines.get(interval) if isinstance(eth_klines, dict) else None
            spot_df = spot_klines.get(interval)
            oi_df = oi_hist.get(interval, pd.DataFrame())
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            if not isinstance(btc_df, pd.DataFrame) or btc_df.empty:
                continue
            if not isinstance(spot_df, pd.DataFrame):
                spot_df = None
            if not isinstance(oi_df, pd.DataFrame):
                oi_df = pd.DataFrame()
            row = _ltf_interval_metrics(symbol, interval, df, oi_df, btc_df, eth_df, spot_df)
            row["oi_value"] = oi_value
            row["quote_volume_24h"] = float(ticker_stats.get(symbol, {}).get("quote_volume_24h", 0.0))
            rows.append(row)

    interval_df = pd.DataFrame(rows)
    if interval_df.empty:
        return interval_df

    best_idx = interval_df.groupby("symbol")["ignition_score"].idxmax()
    out = interval_df.loc[best_idx].copy()
    out = out.rename(
        columns={
            "timeframe": "ignition_tf",
            "ignition_score": "ltf_ignition_score",
            "long_ignition_score": "ltf_long_ignition_score",
            "short_ignition_score": "ltf_short_ignition_score",
        }
    )

    for interval in LTF_INTERVALS:
        interval_slice = interval_df[interval_df["timeframe"] == interval].set_index("symbol")
        tf_scores = interval_slice["ignition_score"]
        out[f"ignition_score_{interval}"] = out["symbol"].map(tf_scores).fillna(0.0)
        out[f"direction_{interval}"] = out["symbol"].map(interval_slice["ignition_state"].map(_direction_from_state)).fillna("Neutral")
        out[f"fresh_{interval}"] = out["symbol"].map(interval_slice["trigger_fresh"]).fillna(False).astype(bool)
        out[f"bars_since_trigger_{interval}"] = out["symbol"].map(interval_slice["bars_since_trigger"]).fillna(999).astype(int)

    out["ltf_direction"] = out["ignition_state"].map(_direction_from_state)
    for direction in ("Long", "Short"):
        out[f"tf_{direction.lower()}_alignment"] = sum(
            (out.get(f"direction_{interval}", pd.Series("Neutral", index=out.index)) == direction).astype(int)
            for interval in LTF_INTERVALS
        )
    out["tf_alignment_score"] = np.select(
        [out["ltf_direction"].eq("Long"), out["ltf_direction"].eq("Short")],
        [out["tf_long_alignment"], out["tf_short_alignment"]],
        default=0,
    ).astype(int)
    out["tf_alignment_pass"] = out["tf_alignment_score"] >= MIN_TF_ALIGNMENT
    out["fresh_setup_pass"] = out["bars_since_trigger"] <= MAX_BEST_SETUP_TRIGGER_BARS
    return out.sort_values(["ltf_ignition_score", "volume_zscore", "oi_zscore"], ascending=False).reset_index(drop=True)


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


def _regime_scatter(df: pd.DataFrame, x: str, y: str, color: str, title: str, x_label: str, y_label: str):
    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=color,
        hover_name="symbol",
        hover_data={col: True for col in df.columns if col in {
            "ignition_state",
            "ignition_tf",
            "ltf_ignition_score",
            "htf_expansion_direction",
            "htf_expansion_score",
            "best_setup_score",
            "atr_percentile",
            "atr_roc",
            "volume_zscore",
            "oi_zscore",
            "taker_imbalance",
            "cvd_3bar_slope",
            "basis_bp",
            "basis_delta_3bar_bp",
            "bars_since_trigger",
            "tf_alignment_score",
            "breakout_distance_atr",
            "rs_vs_btc",
            "rs_vs_eth",
        }},
        labels={x: x_label, y: y_label, color: color.replace("_", " ").title()},
        title=title,
        color_continuous_scale=[
            [0.0, "#293528"],
            [0.35, "#59705a"],
            [0.7, "#9fab95"],
            [1.0, "#e4eadf"],
        ],
        template="plotly_dark",
        height=520,
    )
    fig.update_traces(marker=dict(size=8, opacity=0.86))
    fig.update_layout(
        plot_bgcolor=APP_PANEL,
        paper_bgcolor=APP_BG,
        font_color=APP_TEXT,
        title_font_size=17,
        margin=dict(l=30, r=20, t=60, b=30),
    )
    fig.update_xaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    fig.update_yaxes(showgrid=True, gridcolor=APP_GRID, zeroline=False, linecolor=APP_BORDER)
    return fig


def _format_percent_columns(table_df: pd.DataFrame, percent_cols: list[str]) -> pd.DataFrame:
    for col in percent_cols:
        if col in table_df:
            table_df[col] = table_df[col].map(lambda value: f"{float(value):+.2%}")
    return table_df


def _show_ltf_ignition_table(df: pd.DataFrame):
    if df.empty:
        st.info("No LTF ignition candidates match the current filters.")
        return

    cols = [
        "symbol",
        "ignition_state",
        "ignition_tf",
        "ltf_ignition_score",
        "atr_percentile",
        "atr_roc",
        "atr_compression_score",
        "atr_expansion_score",
        "volume_zscore",
        "oi_zscore",
        "taker_imbalance",
        "cvd_3bar_slope",
        "basis_bp",
        "basis_delta_3bar_bp",
        "price_distance_from_vwap_atr",
        "breakout_distance_atr",
        "break_hold_confirmed",
        "rs_vs_btc",
        "rs_vs_eth",
        "compression_recent_bars",
        "bars_since_trigger",
        "tf_alignment_score",
        "ignition_score_5m",
        "ignition_score_15m",
        "ignition_score_1h",
    ]
    table_df = _format_percent_columns(df[cols].copy(), ["atr_roc", "rs_vs_btc", "rs_vs_eth"])
    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        hide_index=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "ignition_state": st.column_config.TextColumn("State"),
            "ignition_tf": st.column_config.TextColumn("TF"),
            "ltf_ignition_score": st.column_config.NumberColumn("Ignition", format="%.1f"),
            "atr_percentile": st.column_config.NumberColumn("ATR %ile", format="%.1f"),
            "atr_roc": st.column_config.TextColumn("ATR ROC"),
            "atr_compression_score": st.column_config.NumberColumn("Compression", format="%.1f"),
            "atr_expansion_score": st.column_config.NumberColumn("Expansion", format="%.1f"),
            "volume_zscore": st.column_config.NumberColumn("Vol Z", format="%.2f"),
            "oi_zscore": st.column_config.NumberColumn("OI Z", format="%.2f"),
            "taker_imbalance": st.column_config.NumberColumn("Taker", format="%.2f"),
            "cvd_3bar_slope": st.column_config.NumberColumn("CVD 3", format="%.0f"),
            "basis_bp": st.column_config.NumberColumn("Basis bp", format="%.1f"),
            "basis_delta_3bar_bp": st.column_config.NumberColumn("Basis d3", format="%.1f"),
            "price_distance_from_vwap_atr": st.column_config.NumberColumn("VWAP Dist ATR", format="%.2f"),
            "breakout_distance_atr": st.column_config.NumberColumn("Breakout ATR", format="%.2f"),
            "break_hold_confirmed": st.column_config.CheckboxColumn("Hold"),
            "rs_vs_btc": st.column_config.TextColumn("RS BTC"),
            "rs_vs_eth": st.column_config.TextColumn("RS ETH"),
            "compression_recent_bars": st.column_config.NumberColumn("Comp Bars", format="%d"),
            "bars_since_trigger": st.column_config.NumberColumn("Age", format="%d"),
            "tf_alignment_score": st.column_config.NumberColumn("TF Align", format="%d"),
            "ignition_score_5m": st.column_config.NumberColumn("5m", format="%.1f"),
            "ignition_score_15m": st.column_config.NumberColumn("15m", format="%.1f"),
            "ignition_score_1h": st.column_config.NumberColumn("1h", format="%.1f"),
        },
    )


def _show_htf_expansion_table(df: pd.DataFrame):
    if df.empty:
        st.info("No HTF expansion candidates match the current filters.")
        return

    cols = [
        "symbol",
        "htf_expansion_direction",
        "htf_expansion_score",
        "htf_atr_percentile",
        "htf_atr_roc",
        "htf_atr_compression_score",
        "htf_atr_expansion_score",
        "htf_breakout_distance_atr",
        "htf_range_width_atr",
        "daily_structure_score",
        "daily_atr_percentile",
        "daily_volume_ratio",
        "daily_volume_persistence_days",
        "daily_oi_persistence_days",
        "daily_swing_high",
        "daily_swing_low",
        "daily_long_confirmed",
        "daily_short_confirmed",
        "btc_daily_regime",
        "btc_daily_regime_score",
        "htf_momentum_score",
        "htf_setup_score",
        "htf_relative_strength_score",
        "volume_score",
        "oi_score",
        "rs_24h",
        "rs_72h",
    ]
    table_df = _format_percent_columns(df[cols].copy(), ["htf_atr_roc", "rs_24h", "rs_72h"])
    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        hide_index=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "htf_expansion_direction": st.column_config.TextColumn("State"),
            "htf_expansion_score": st.column_config.NumberColumn("Expansion", format="%.1f"),
            "htf_atr_percentile": st.column_config.NumberColumn("ATR %ile", format="%.1f"),
            "htf_atr_roc": st.column_config.TextColumn("ATR ROC"),
            "htf_atr_compression_score": st.column_config.NumberColumn("Compression", format="%.1f"),
            "htf_atr_expansion_score": st.column_config.NumberColumn("ATR Expand", format="%.1f"),
            "htf_breakout_distance_atr": st.column_config.NumberColumn("Breakout ATR", format="%.2f"),
            "htf_range_width_atr": st.column_config.NumberColumn("Range ATR", format="%.2f"),
            "daily_structure_score": st.column_config.NumberColumn("Daily Struct", format="%.1f"),
            "daily_atr_percentile": st.column_config.NumberColumn("D ATR %ile", format="%.1f"),
            "daily_volume_ratio": st.column_config.NumberColumn("D Vol", format="%.2f"),
            "daily_volume_persistence_days": st.column_config.NumberColumn("Vol Days", format="%d"),
            "daily_oi_persistence_days": st.column_config.NumberColumn("OI Days", format="%d"),
            "daily_swing_high": st.column_config.NumberColumn("Swing High", format="%.4g"),
            "daily_swing_low": st.column_config.NumberColumn("Swing Low", format="%.4g"),
            "daily_long_confirmed": st.column_config.CheckboxColumn("D Long"),
            "daily_short_confirmed": st.column_config.CheckboxColumn("D Short"),
            "btc_daily_regime": st.column_config.TextColumn("BTC Regime"),
            "btc_daily_regime_score": st.column_config.NumberColumn("BTC Score", format="%.1f"),
            "htf_momentum_score": st.column_config.NumberColumn("HTF Momentum", format="%.1f"),
            "htf_setup_score": st.column_config.NumberColumn("HTF Setup", format="%.1f"),
            "htf_relative_strength_score": st.column_config.NumberColumn("HTF RS", format="%.1f"),
            "volume_score": st.column_config.NumberColumn("Vol Score", format="%.1f"),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f"),
            "rs_24h": st.column_config.TextColumn("RS 24H"),
            "rs_72h": st.column_config.TextColumn("RS 72H"),
        },
    )


def _build_best_setups(ltf_df: pd.DataFrame, htf_df: pd.DataFrame) -> pd.DataFrame:
    if ltf_df.empty or htf_df.empty:
        return pd.DataFrame()

    required_cols = {"tf_alignment_pass", "fresh_setup_pass", "ltf_direction"}
    if required_cols.issubset(ltf_df.columns):
        ltf_df = ltf_df[
            ltf_df["tf_alignment_pass"]
            & ltf_df["fresh_setup_pass"]
            & ltf_df["ltf_direction"].isin(["Long", "Short"])
        ].copy()
        if ltf_df.empty:
            return ltf_df

    htf_cols = [
        "symbol",
        "htf_expansion_direction",
        "htf_expansion_score",
        "htf_momentum_score",
        "htf_setup_score",
        "htf_atr_percentile",
        "htf_atr_roc",
        "htf_breakout_distance_atr",
        "daily_structure_score",
        "daily_long_confirmed",
        "daily_short_confirmed",
        "daily_volume_ratio",
        "daily_volume_persistence_days",
        "daily_oi_persistence_days",
        "daily_swing_high",
        "daily_swing_low",
        "btc_daily_regime",
        "btc_daily_regime_score",
        "rs_24h",
        "rs_72h",
    ]
    merged = ltf_df.merge(htf_df[htf_cols], on="symbol", how="inner")
    if merged.empty:
        return merged

    long_aligned = (merged["ltf_direction"] == "Long") & merged["htf_expansion_direction"].str.startswith("Long")
    short_aligned = (merged["ltf_direction"] == "Short") & merged["htf_expansion_direction"].str.startswith("Short")
    long_aligned = long_aligned & merged["daily_long_confirmed"].fillna(False)
    short_aligned = short_aligned & merged["daily_short_confirmed"].fillna(False)
    compression_context = merged["htf_expansion_direction"].eq("Compression")
    merged["alignment_score"] = np.select(
        [long_aligned | short_aligned, compression_context],
        [100.0, 70.0],
        default=35.0,
    )
    merged["best_setup_score"] = (
        0.55 * merged["ltf_ignition_score"]
        + 0.30 * merged["htf_expansion_score"]
        + 0.15 * merged["alignment_score"]
    ).clip(0.0, 100.0)
    merged["best_setup_state"] = np.select(
        [long_aligned, short_aligned, compression_context],
        ["Long best setup", "Short best setup", "LTF trigger in HTF compression"],
        default="Mixed setup",
    )
    return merged.sort_values("best_setup_score", ascending=False).reset_index(drop=True)


def _show_best_setups_table(df: pd.DataFrame):
    if df.empty:
        st.info("No combined best setups match the current filters.")
        return

    cols = [
        "symbol",
        "best_setup_state",
        "best_setup_score",
        "ignition_state",
        "ignition_tf",
        "ltf_ignition_score",
        "htf_expansion_direction",
        "htf_expansion_score",
        "daily_structure_score",
        "daily_volume_persistence_days",
        "daily_oi_persistence_days",
        "btc_daily_regime",
        "alignment_score",
        "tf_alignment_score",
        "bars_since_trigger",
        "volume_zscore",
        "oi_zscore",
        "taker_imbalance",
        "cvd_3bar_slope",
        "basis_bp",
        "basis_delta_3bar_bp",
        "atr_percentile",
        "atr_roc",
        "breakout_distance_atr",
        "rs_vs_btc",
        "rs_vs_eth",
    ]
    table_df = _format_percent_columns(df[cols].copy(), ["atr_roc", "rs_vs_btc", "rs_vs_eth"])
    st.dataframe(
        table_df,
        use_container_width=True,
        height=460,
        hide_index=True,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "best_setup_state": st.column_config.TextColumn("Best Setup"),
            "best_setup_score": st.column_config.NumberColumn("Score", format="%.1f"),
            "ignition_state": st.column_config.TextColumn("LTF State"),
            "ignition_tf": st.column_config.TextColumn("TF"),
            "ltf_ignition_score": st.column_config.NumberColumn("LTF Ignition", format="%.1f"),
            "htf_expansion_direction": st.column_config.TextColumn("HTF State"),
            "htf_expansion_score": st.column_config.NumberColumn("HTF Expansion", format="%.1f"),
            "daily_structure_score": st.column_config.NumberColumn("Daily Struct", format="%.1f"),
            "daily_volume_persistence_days": st.column_config.NumberColumn("Vol Days", format="%d"),
            "daily_oi_persistence_days": st.column_config.NumberColumn("OI Days", format="%d"),
            "btc_daily_regime": st.column_config.TextColumn("BTC Regime"),
            "alignment_score": st.column_config.NumberColumn("Align", format="%.1f"),
            "tf_alignment_score": st.column_config.NumberColumn("TF Align", format="%d"),
            "bars_since_trigger": st.column_config.NumberColumn("Age", format="%d"),
            "volume_zscore": st.column_config.NumberColumn("Vol Z", format="%.2f"),
            "oi_zscore": st.column_config.NumberColumn("OI Z", format="%.2f"),
            "taker_imbalance": st.column_config.NumberColumn("Taker", format="%.2f"),
            "cvd_3bar_slope": st.column_config.NumberColumn("CVD 3", format="%.0f"),
            "basis_bp": st.column_config.NumberColumn("Basis bp", format="%.1f"),
            "basis_delta_3bar_bp": st.column_config.NumberColumn("Basis d3", format="%.1f"),
            "atr_percentile": st.column_config.NumberColumn("ATR %ile", format="%.1f"),
            "atr_roc": st.column_config.TextColumn("ATR ROC"),
            "breakout_distance_atr": st.column_config.NumberColumn("Breakout ATR", format="%.2f"),
            "rs_vs_btc": st.column_config.TextColumn("RS BTC"),
            "rs_vs_eth": st.column_config.TextColumn("RS ETH"),
        },
    )


def _render_classic_altcoin_dashboard(
    df: pd.DataFrame,
    scoring_mode: str,
    min_score: int,
    max_overextension_score: int,
    top_n: int,
    view: str,
):
    is_htf = scoring_mode == "HTF Momentum"
    if is_htf:
        momentum_col = "htf_momentum_score"
        setup_col = "htf_setup_score"
        score_label = "HTF Momentum"
        sort_cols = ["htf_setup_score", "htf_momentum_score"]
    else:
        momentum_col = "momentum_score"
        setup_col = "setup_score"
        score_label = "LTF Scalping"
        sort_cols = ["setup_score", "momentum_score"]

    setups = df[
        (df[momentum_col] >= float(min_score))
        & (df["overextension_score"] <= float(max_overextension_score))
    ].sort_values(sort_cols, ascending=False)
    ranked_df = df.sort_values([momentum_col, setup_col], ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alts Scanned", f"{len(df):,}")
    c2.metric("Qualified Setups", f"{len(setups):,}")
    c3.metric(f"Top {score_label}", f"{df[momentum_col].max():.1f}")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    if view == "Momentum vs Overextension":
        fig = _scatter(
            ranked_df,
            momentum_col,
            "overextension_score",
            "alpha_score" if not is_htf else "htf_alpha_score",
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

    heading_col, glossary_col = st.columns([0.76, 0.24], vertical_alignment="bottom")
    with heading_col:
        st.subheader(f"Classic {score_label.lower()} dashboard ({len(setups)} assets)")
    with glossary_col:
        _render_glossary_jump()
    _show_table(setups.head(top_n), scoring_mode)

    with st.expander(f"Full classic screener ({len(df)} assets)"):
        _show_table(ranked_df, scoring_mode)


def _render_ltf_scalping_dashboard(
    df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    min_score: int,
    max_overextension_score: int,
    top_n: int,
    view: str,
):
    st.subheader("LTF Ignition")
    st.caption("Native 5m, 15m, and 1h ATR regime scan with fresh-trigger, taker/CVD, basis, and break-hold gates.")
    if ltf_df.empty:
        ignition_df = ltf_df
    else:
        ignition_df = ltf_df[
            (ltf_df["ltf_ignition_score"] >= float(min_score))
            & ltf_df["trigger_fresh"]
            & ltf_df["fresh_setup_pass"]
        ].sort_values(
            ["ltf_ignition_score", "tf_alignment_score", "volume_zscore", "oi_zscore"],
            ascending=False,
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LTF Assets Scanned", f"{len(ltf_df):,}")
    c2.metric("Ignition Candidates", f"{len(ignition_df):,}")
    c3.metric("Top Ignition", f"{ltf_df['ltf_ignition_score'].max():.1f}" if not ltf_df.empty else "0.0")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    if not ignition_df.empty:
        fig = _regime_scatter(
            ignition_df,
            "volume_zscore",
            "oi_zscore",
            "ltf_ignition_score",
            "LTF Ignition: Volume Spike vs OI Spike",
            "Volume Z-Score",
            "OI Z-Score",
        )
        st.plotly_chart(fig, use_container_width=True)
    _show_ltf_ignition_table(ignition_df.head(top_n))

    st.divider()
    _render_classic_altcoin_dashboard(df, "LTF Scalping", min_score, max_overextension_score, top_n, view)


def _render_htf_momentum_dashboard(
    df: pd.DataFrame,
    min_score: int,
    max_overextension_score: int,
    top_n: int,
    view: str,
):
    st.subheader("HTF Expansion")
    st.caption("Swing context: 1h expansion plus daily candle structure, pivot reclaim/rejection, BTC regime, OI persistence, and volume persistence.")
    expansion_df = df[df["htf_expansion_score"] >= float(min_score)].sort_values(
        ["htf_expansion_score", "daily_structure_score", "daily_oi_persistence_days"],
        ascending=False,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("HTF Assets Scanned", f"{len(df):,}")
    c2.metric("Expansion Candidates", f"{len(expansion_df):,}")
    c3.metric("Top Expansion", f"{df['htf_expansion_score'].max():.1f}")
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    if not expansion_df.empty:
        fig = _regime_scatter(
            expansion_df,
            "htf_atr_percentile",
            "htf_atr_roc",
            "htf_expansion_score",
            "HTF Expansion: ATR Percentile vs ATR ROC",
            "ATR Percentile",
            "ATR ROC",
        )
        st.plotly_chart(fig, use_container_width=True)
    _show_htf_expansion_table(expansion_df.head(top_n))

    st.divider()
    _render_classic_altcoin_dashboard(df, "HTF Momentum", min_score, max_overextension_score, top_n, view)


def _render_best_setups_dashboard(
    df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    min_score: int,
    top_n: int,
):
    st.subheader("Best Setups")
    st.caption("Fresh LTF triggers only: 2-of-3 timeframe alignment, CVD/taker confirmation, basis confirmation, and HTF context.")
    best_df = _build_best_setups(ltf_df, df)
    if not best_df.empty:
        best_df = best_df[best_df["best_setup_score"] >= float(min_score)].sort_values("best_setup_score", ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Combined Assets", f"{len(best_df):,}")
    c2.metric("Long Setups", f"{best_df['best_setup_state'].str.startswith('Long').sum() if not best_df.empty else 0:,}")
    c3.metric("Short Setups", f"{best_df['best_setup_state'].str.startswith('Short').sum() if not best_df.empty else 0:,}")
    c4.metric("Top Best Setup", f"{best_df['best_setup_score'].max():.1f}" if not best_df.empty else "0.0")

    if not best_df.empty:
        fig = _regime_scatter(
            best_df,
            "ltf_ignition_score",
            "htf_expansion_score",
            "best_setup_score",
            "Best Setups: LTF Ignition vs HTF Expansion",
            "LTF Ignition Score",
            "HTF Expansion Score",
        )
        st.plotly_chart(fig, use_container_width=True)
    _show_best_setups_table(best_df.head(top_n))


def _show_table(df: pd.DataFrame, scoring_mode: str):
    cols = [
        "symbol"
    ]
    if df.empty:
        st.info("No assets match the current filters.")
        return

    if scoring_mode in {"HTF Leadership", "HTF Momentum"}:
        cols = [
            "symbol",
            "htf_setup_score",
            "htf_momentum_score",
            "vol_adjusted_score",
            "htf_trend_score",
            "oi_score",
            "funding_trend_quality_score",
            "htf_relative_strength_score",
            "rs_24h",
            "rs_72h",
            "htf_alpha_score",
            "alpha_24h",
            "alpha_72h",
            "overall_ltf_strength",
        ]
        percent_cols = ["rs_24h", "rs_72h", "alpha_24h", "alpha_72h"]
        table_df = df.copy()
        table_df["overall_ltf_strength"] = table_df["momentum_score"]
        column_config = {
            "symbol": st.column_config.TextColumn("Symbol", help=_term_help("Symbol")),
            "htf_setup_score": st.column_config.NumberColumn("HTF Setup", format="%.1f", help=_term_help("HTF Setup")),
            "htf_momentum_score": st.column_config.NumberColumn("HTF Momentum", format="%.1f", help=_term_help("HTF Momentum")),
            "vol_adjusted_score": st.column_config.NumberColumn("Vol-Adj", format="%.1f", help=_term_help("Vol-Adj")),
            "htf_trend_score": st.column_config.NumberColumn("HTF Trend", format="%.1f", help=_term_help("HTF Trend")),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f", help=_term_help("OI Score")),
            "funding_trend_quality_score": st.column_config.NumberColumn("Funding Trend", format="%.1f", help=_term_help("Funding Trend")),
            "htf_relative_strength_score": st.column_config.NumberColumn("HTF RS", format="%.1f", help=_term_help("HTF RS")),
            "rs_24h": st.column_config.TextColumn("RS 24H", help=_term_help("RS 24H")),
            "rs_72h": st.column_config.TextColumn("RS 72H", help=_term_help("RS 72H")),
            "htf_alpha_score": st.column_config.NumberColumn("HTF Alpha", format="%.1f", help=_term_help("HTF Alpha")),
            "alpha_24h": st.column_config.TextColumn("Alpha 24H", help=_term_help("Alpha 24H")),
            "alpha_72h": st.column_config.TextColumn("Alpha 72H", help=_term_help("Alpha 72H")),
            "overall_ltf_strength": st.column_config.NumberColumn("Overall LTF Strength", format="%.1f", help=_term_help("Overall LTF Strength")),
        }
    else:
        cols = [
            "symbol",
            "setup_score",
            "momentum_score",
            "volume_score",
            "trend_score",
            "volume_ratio_1h",
            "volume_z_1h",
            "overextension_score",
            "oi_change_1h",
            "oi_score",
            "relative_strength_score",
            "rs_1h",
            "rs_4h",
            "rs_24h",
            "funding_quality_score",
            "alpha_score",
            "alpha_4h",
            "alpha_24h",
            "overall_htf_strength",
        ]
        percent_cols = ["rs_1h", "rs_4h", "rs_24h", "alpha_4h", "alpha_24h", "oi_change_1h"]
        table_df = df.copy()
        table_df["overall_htf_strength"] = table_df["htf_momentum_score"]
        column_config = {
            "symbol": st.column_config.TextColumn("Symbol", help=_term_help("Symbol")),
            "setup_score": st.column_config.NumberColumn("Scalp Setup", format="%.1f", help=_term_help("Setup")),
            "momentum_score": st.column_config.NumberColumn("LTF Scalping", format="%.1f", help=_term_help("Momentum")),
            "volume_score": st.column_config.NumberColumn("Vol Score", format="%.1f", help=_term_help("Vol Score")),
            "trend_score": st.column_config.NumberColumn("Trend Score", format="%.1f", help=_term_help("Trend Score")),
            "volume_ratio_1h": st.column_config.NumberColumn("Vol Ratio", format="%.2f", help=_term_help("Vol Ratio")),
            "volume_z_1h": st.column_config.NumberColumn("Vol Z", format="%.2f", help=_term_help("Vol Z")),
            "overextension_score": st.column_config.NumberColumn("Overext", format="%.1f", help=_term_help("Overext")),
            "oi_change_1h": st.column_config.TextColumn("OI 1H", help=_term_help("OI 1H")),
            "oi_score": st.column_config.NumberColumn("OI Score", format="%.1f", help=_term_help("OI Score")),
            "relative_strength_score": st.column_config.NumberColumn("RS Score", format="%.1f", help=_term_help("RS Score")),
            "rs_1h": st.column_config.TextColumn("RS 1H", help=_term_help("RS 1H")),
            "rs_4h": st.column_config.TextColumn("RS 4H", help=_term_help("RS 4H")),
            "rs_24h": st.column_config.TextColumn("RS 24H", help=_term_help("RS 24H")),
            "funding_quality_score": st.column_config.NumberColumn("Funding Score", format="%.1f", help=_term_help("Funding Score")),
            "alpha_score": st.column_config.NumberColumn("Alpha Score", format="%.1f", help=_term_help("Alpha Score")),
            "alpha_4h": st.column_config.TextColumn("Alpha 4H", help=_term_help("Alpha 4H")),
            "alpha_24h": st.column_config.TextColumn("Alpha 24H", help=_term_help("Alpha 24H")),
            "overall_htf_strength": st.column_config.NumberColumn("Overall HTF Strength", format="%.1f", help=_term_help("Overall HTF Strength")),
        }

    table_df = table_df[cols].copy()
    for col in percent_cols:
        table_df[col] = table_df[col].map(lambda value: f"{value:.2%}")

    st.dataframe(
        table_df,
        use_container_width=True,
        height=420,
        column_config=column_config,
        hide_index=True,
    )


def _render_bitcoin_section(bitcoin_mode: str, bubble_timeframe: str, bubble_lookback_days: int):
    config = BTC_BUBBLE_TIMEFRAMES[bubble_timeframe]
    progress_msg = st.empty()
    progress_msg.info(
        f"Building {str(config['label']).lower()} Bitcoin spot volume bubble map with auto-adjusted volume temperature..."
    )
    bubble_bundle = build_bitcoin_bubble_data(bubble_lookback_days, bubble_timeframe)
    progress_msg.empty()

    sources = bubble_bundle["sources"]
    aggregated = bubble_bundle["aggregated"]
    errors = bubble_bundle["errors"]
    aggregate_keys = bubble_bundle["aggregate_keys"]
    premium_df = bubble_bundle.get("coinbase_premium", pd.DataFrame())
    etf_tape = bubble_bundle.get("etf_tape", {})

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
    flow_summary = _spot_flow_summary(df, premium_df, etf_tape if isinstance(etf_tape, dict) else {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bars", f"{len(df):,}")
    c2.metric("Latest Price", f"{latest['close']:,.2f}")
    c3.metric("Spot Flow", str(flow_summary["state"]))
    c4.metric("Aggressor Imbalance", f"{_safe_float(latest.get('spot_imbalance')):+.1%}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Latest Spot Volume", f"{latest['quote_volume']:,.0f}")
    c6.metric("Volume State", str(latest["temperature"]))
    premium_latest = _safe_float(flow_summary.get("premium_latest"), np.nan)
    c7.metric("Coinbase Premium", "n/a" if not np.isfinite(premium_latest) else f"{premium_latest:+.1f} bp")
    etf_ratio = _safe_float(flow_summary.get("etf_proxy_ratio"), np.nan)
    c8.metric("ETF Tape Proxy", "n/a" if not np.isfinite(etf_ratio) else f"{etf_ratio:+.1%}")

    st.caption(
        f"Bubble size reflects {config['label']} total spot volume. When Binance taker flow is available, "
        "bubble color reflects aggressive buyer/seller imbalance: green = buyers lifting offers, red = sellers hitting bids."
    )
    st.caption(str(flow_summary["copy"]))
    if source_key == "aggregated":
        st.caption(
            "Aggregated view currently sums public spot data from: "
            + ", ".join(key.title() for key in aggregate_keys)
            + ". Aggressor color uses Binance taker-buy/taker-sell flow when present."
        )
    elif source_key in errors:
        st.caption(f"Fetch note for {source_key}: {errors[source_key]}")

    fig = _build_bitcoin_bubble_chart(df, title)
    st.plotly_chart(fig, use_container_width=True)

    if "spot_cvd" in df.columns and df["spot_cvd"].notna().any():
        st.plotly_chart(_build_spot_cvd_chart(df), use_container_width=True)
    else:
        st.info("Spot CVD is only available on Binance or aggregated views that include Binance taker-flow data.")

    if not premium_df.empty:
        st.plotly_chart(_build_coinbase_premium_chart(premium_df.tail(len(df))), use_container_width=True)
    else:
        st.info("Coinbase premium is unavailable right now because either Binance or Coinbase spot data did not load.")

    etf_df = etf_tape.get("data", pd.DataFrame()) if isinstance(etf_tape, dict) else pd.DataFrame()
    etf_error = etf_tape.get("error", "") if isinstance(etf_tape, dict) else ""
    if isinstance(etf_df, pd.DataFrame) and not etf_df.empty:
        st.plotly_chart(_build_etf_tape_chart(etf_df), use_container_width=True)
        st.caption(
            "ETF tape uses EODHD daily OHLCV for IBIT, FBTC, ARKB, and BITB. It is a signed-volume demand proxy, "
            "not official ETF creation/redemption net flow."
        )
    elif etf_error:
        st.info(etf_error)

    detail_cols = [
        "ts",
        "close",
        "quote_volume",
        "aggressive_buy_volume",
        "aggressive_sell_volume",
        "spot_imbalance",
        "spot_delta",
        "spot_cvd",
        "volume_z",
        "temperature",
        "flow_state",
    ]
    detail_df = df[[col for col in detail_cols if col in df.columns]].copy()
    ts_format = "%Y-%m-%d" if bubble_timeframe == "1D" else "%Y-%m-%d %H:%M"
    detail_df["ts"] = detail_df["ts"].dt.strftime(ts_format)
    st.dataframe(
        detail_df.sort_values("ts", ascending=False),
        use_container_width=True,
        height=360,
        column_config={
            "ts": st.column_config.TextColumn("Date"),
            "close": st.column_config.NumberColumn("BTC Price", format="%.2f"),
            "quote_volume": st.column_config.NumberColumn("Spot Volume (USD)", format="%.0f"),
            "aggressive_buy_volume": st.column_config.NumberColumn("Agg Buy Vol", format="%.0f"),
            "aggressive_sell_volume": st.column_config.NumberColumn("Agg Sell Vol", format="%.0f"),
            "spot_imbalance": st.column_config.NumberColumn("Agg Imbal", format="%.2f"),
            "spot_delta": st.column_config.NumberColumn("Spot Delta", format="%.0f"),
            "spot_cvd": st.column_config.NumberColumn("Spot CVD", format="%.0f"),
            "volume_z": st.column_config.NumberColumn("Volume Z", format="%.2f"),
            "temperature": st.column_config.TextColumn("State"),
            "flow_state": st.column_config.TextColumn("Flow"),
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

    if "app_page" not in st.session_state:
        st.session_state["app_page"] = "Altcoins"
    elif st.session_state["app_page"] == "BTC Options Cockpit":
        st.session_state["app_page"] = "BTC Options Screener"

    page = st.session_state["app_page"]
    options_anchor_mode = "Weekly Open"
    bitcoin_mode = "Bitcoin Spot Vol (Binance)"
    bubble_timeframe = "1D"
    bubble_lookback_days = 365
    altcoin_views = ["LTF Scalping", "HTF Momentum", "Best Setups"]
    if "altcoin_screener_mode" not in st.session_state:
        st.session_state["altcoin_screener_mode"] = "LTF Scalping"

    def _open_altcoin_screener():
        st.session_state["app_page"] = "Altcoins"

    with st.sidebar:
        if st.button("Force refresh"):
            st.cache_data.clear()
            st.rerun()

        if st.button("📖 Glossary / Term Guide", use_container_width=True):
            st.session_state["app_page"] = "Glossary / Term Guide"
            st.rerun()

        st.divider()

        with st.expander("Altcoins", expanded=page == "Altcoins"):
            altcoin_screener_mode = st.radio(
                "Altcoin Screener",
                altcoin_views,
                index=altcoin_views.index(st.session_state["altcoin_screener_mode"])
                if st.session_state["altcoin_screener_mode"] in altcoin_views
                else 0,
                key="altcoin_screener_mode",
                on_change=_open_altcoin_screener,
                label_visibility="collapsed",
            )

        with st.expander("Bitcoin", expanded=page in {"BITCOIN", "BTC Options Screener"}):
            if st.button("Bitcoin Spot Volume Bubblemap", key="page_bitcoin_bubble", use_container_width=True):
                st.session_state["app_page"] = "BITCOIN"
                st.rerun()
            if st.button("BTC Options Screener", key="page_btc_options", use_container_width=True):
                st.session_state["app_page"] = "BTC Options Screener"
                st.rerun()

        page = st.session_state["app_page"]

        if page == "Glossary / Term Guide":
            st.divider()
            st.caption("Reference page for all screener terms and score labels.")
        elif page == "BTC Options Screener":
            st.subheader("BTC Options")
            options_anchor_mode = st.radio(
                "Anchored VWAP",
                ["Weekly Open", "Monthly Open", "Prior 24H High", "Prior 24H Low"],
                index=0,
            )
            st.caption("Phase 1 uses public Deribit BTC options data plus Binance BTCUSDT perpetual context.")
        elif page == "BITCOIN":
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
            bubble_timeframe = st.radio("Timeframe", ["1D", "12H", "8H"], index=0, horizontal=True)
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
            st.subheader("Dashboard filters")
            min_dashboard_score = st.slider("Min dashboard score", 0, 100, 70, 1)
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

    if page == "Glossary / Term Guide":
        st.title("Binance Perp Scanner 2.0")
        _render_term_guide()
        return

    if page == "BTC Options Screener":
        _render_btc_options_cockpit(options_anchor_mode)
        return

    if page == "BITCOIN":
        st.title("Binance Perp Scanner 2.0")
        st.caption(
            "Altcoin momentum screener for Binance USDT-M perps using BTC-relative strength, "
            "volume expansion, EMA/VWAP trend, open interest, and funding quality."
        )
        _render_bitcoin_section(bitcoin_mode, bubble_timeframe, bubble_lookback_days)
        return

    st.title("Binance Perp Scanner 2.0")
    st.caption(
        "Altcoin momentum screener for Binance USDT-M perps using BTC-relative strength, "
        "volume expansion, EMA/VWAP trend, open interest, and funding quality."
    )

    with st.spinner("Fetching active Binance perpetuals..."):
        try:
            symbols = tuple(get_usdt_perpetuals())
        except Exception as e:
            st.error(f"Could not connect to Binance: {e}")
            st.stop()

    progress_msg = st.empty()
    progress_msg.info("Building core 1H momentum model for Binance altcoin perps...")
    df = build_metrics(symbols, min_quote_volume, min_trades, min_oi_value)

    if df.empty:
        progress_msg.empty()
        st.error("No altcoins matched the current gates. Lower the liquidity thresholds and try again.")
        st.stop()

    ltf_df = pd.DataFrame()
    if altcoin_screener_mode in {"LTF Scalping", "Best Setups"}:
        progress_msg.info("Building accurate native LTF ATR ignition model - this fetches 5m, 15m, and 1h data...")
        ltf_df = build_ltf_regime_metrics(symbols, min_quote_volume, min_trades, min_oi_value)
    progress_msg.empty()

    if altcoin_screener_mode == "LTF Scalping":
        _render_ltf_scalping_dashboard(
            df,
            ltf_df,
            min_dashboard_score,
            max_overextension_score,
            top_n,
            view,
        )
    elif altcoin_screener_mode == "HTF Momentum":
        _render_htf_momentum_dashboard(
            df,
            min_dashboard_score,
            max_overextension_score,
            top_n,
            view,
        )
    else:
        _render_best_setups_dashboard(
            df,
            ltf_df,
            min_dashboard_score,
            top_n,
        )


if __name__ == "__main__":
    main()
