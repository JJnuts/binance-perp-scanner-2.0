"""
Binance USDT Perpetual Scanner

Scans active USDT-M perpetual futures on Binance Futures and ranks altcoins by
momentum, relative strength vs BTC, and overextension.
No API key required.
"""

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import escape
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
DERIBIT_BASE = "https://www.deribit.com/api/v2"
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
BTC_OPTIONS_MAX_CONTRACTS = 180
BTC_OPTIONS_MAX_DAYS = 120
BTC_OPTIONS_KLINE_LIMIT = 576
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
        "HTF Momentum",
        "Higher-timeframe leadership score. It leans more heavily on 24H/72H/7D behavior, cleaner trend structure, and volatility-adjusted persistence rather than short-term ignition.",
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
            ("Momentum", "Primary LTF ranking. It blends alpha, RS vs BTC, volume, trend, OI, and funding quality into one 0-100 score."),
            ("HTF Momentum", "Higher-timeframe leadership score. It favors cleaner 24H to 7D strength over short bursts."),
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

            .jarvis-answer strong {{
                color: var(--app-accent);
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
    if rule:
        out = (
            out.set_index("ts")
            .resample(rule)
            .agg(close=("close", "last"), quote_volume=("quote_volume", "sum"))
            .dropna()
            .reset_index()
        )
    out["source"] = source_name
    return out[["ts", "close", "quote_volume", "source"]].sort_values("ts")


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
    scale_base = float(out["quote_volume"].median()) if not out.empty else 1.0
    scale_base = max(scale_base, 1.0)
    out["bubble_size"] = (
        np.sqrt(out["quote_volume"] / scale_base) * BUBBLE_SIZE_MULTIPLIER
    ).clip(lower=BUBBLE_SIZE_MIN, upper=BUBBLE_SIZE_MAX)
    return out


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
        chunk = pd.DataFrame(raw).iloc[:, [0, 4, 7]].copy()
        chunk.columns = ["ts", "close", "quote_volume"]
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
    df["source"] = "Binance spot"
    return df[["ts", "close", "quote_volume", "source"]].tail(limit)


def _fetch_coinbase_spot_btc(limit: int, granularity: int, rule: Optional[str]) -> pd.DataFrame:
    end = pd.Timestamp.utcnow().floor("h")
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
        frames = [sources[key][["ts", "close", "quote_volume"]] for key in aggregate_keys]
        merged = pd.concat(frames, ignore_index=True)
        aggregated = (
            merged.groupby("ts", as_index=False)
            .agg(close=("close", "mean"), quote_volume=("quote_volume", "sum"))
            .sort_values("ts")
        )
        aggregated["source"] = "Aggregated CEX spot"
        aggregated = _prepare_bubble_frame(aggregated.tail(limit), int(config["z_window"])).tail(display_bars)

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


def _format_gex_billions(gex_millions: float, signed: bool = False) -> str:
    value = _safe_float(gex_millions) / 1000.0
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.2f}B"


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
    now_ms = pd.Timestamp.utcnow().value // 10**6
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

    strike_map = (
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
        "perp_snapshot": perp_snapshot,
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
    fig = go.Figure()
    fig.add_bar(name="Call GEX", x=nearby["strike"], y=nearby["call_gex_b"], marker_color="#9fab95")
    fig.add_bar(name="Put GEX", x=nearby["strike"], y=-nearby["put_gex_b"], marker_color="#f472b6")
    fig.add_vline(x=spot, line_color="#e4eadf", line_dash="dash")
    fig.update_layout(
        barmode="relative",
        title="Strike GEX Map (Simple Approximation)",
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


def _build_jarvis_summary(bundle: dict[str, object], anchor_mode: str) -> str:
    spot = _safe_float(bundle.get("spot"))
    gamma_flip = bundle.get("gamma_flip")
    signed_gex = _safe_float(bundle.get("total_signed_gex"))
    call_gex = _safe_float(bundle.get("call_gex"))
    put_gex = _safe_float(bundle.get("put_gex"))
    total_abs_gex = _safe_float(bundle.get("total_abs_gex"))
    top5_concentration = _safe_float(bundle.get("top5_concentration"))
    support_levels = bundle.get("support_levels", pd.DataFrame())
    resistance_levels = bundle.get("resistance_levels", pd.DataFrame())
    perp = bundle.get("perp_snapshot", {})
    funding_rate = _safe_float(perp.get("last_funding_rate"))
    oi_change_1h = _safe_float(bundle.get("oi_change_1h"))
    atm_iv = bundle.get("atm_iv", pd.DataFrame())
    avwap_df = bundle.get("avwap_df", pd.DataFrame())
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
        big_picture = "Net GEX is close to balanced, so the strike map is less directional and VWAP/perp flow deserve more weight."

    flip_read = "The gamma flip is not available, so do not use it as a trigger today."
    if gamma_flip:
        flip = float(gamma_flip)
        if spot >= flip:
            flip_read = f"BTC is above the gamma flip near {flip:,.0f}. Staying above it supports a more constructive intraday read."
        else:
            flip_read = f"BTC is below the gamma flip near {flip:,.0f}. Reclaiming it would improve the bullish read; rejection keeps pressure on."

    vwap_label = "unavailable"
    vwap_explainer = "Anchored VWAP is unavailable in this snapshot."
    if isinstance(avwap_df, pd.DataFrame) and not avwap_df.empty:
        vwap_label, vwap_explainer = _jarvis_plain_vwap_state(avwap_df.iloc[-1])

    support_text = _jarvis_level_line(support_levels, "put_gex", "Support", "put GEX")
    resistance_text = _jarvis_level_line(resistance_levels, "call_gex", "Resistance", "call GEX")
    funding_text = _jarvis_funding_read(funding_rate, oi_change_1h)
    iv_text = _jarvis_iv_read(atm_iv)
    sweep_text = _jarvis_sweep_read(sweeps)

    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0

    bullish_case = (
        f"If BTC holds above {top_support:,.0f} and remains {vwap_label}, the cleaner long idea is a push toward {top_resistance:,.0f}."
        if top_support and top_resistance
        else f"If BTC holds above anchored VWAP, the bullish case improves; use the visible GEX resistance zones as upside checkpoints."
    )
    bearish_case = (
        f"If BTC loses {top_support:,.0f} with weak VWAP structure and negative OI/funding confirmation, the next support zones become more important."
        if top_support
        else "If BTC loses anchored VWAP with weak perp flow, the bearish case improves; use the visible support zones as downside checkpoints."
    )
    resistance_case = (
        f"If BTC reaches {top_resistance:,.0f}, watch whether it accepts above that level or rejects. Acceptance can open continuation; rejection makes it a fade/pullback zone."
        if top_resistance
        else "If BTC reaches a visible resistance zone, watch acceptance vs rejection rather than treating the level as guaranteed."
    )

    support_items = _jarvis_level_list(support_levels, "put_gex", "Support")
    resistance_items = _jarvis_level_list(resistance_levels, "call_gex", "Resistance")

    return f"""
        <h4>Big Picture</h4>
        <p><strong>Spot:</strong> {spot:,.2f}. <strong>Net GEX:</strong> {_format_gex_billions(signed_gex, signed=True)}. {escape(big_picture)}</p>
        <p>{escape(flip_read)} Top-5 GEX concentration is {top5_concentration:.1%}, so a small number of strikes are carrying a meaningful part of the options pressure.</p>

        <h4>What The Main Numbers Mean</h4>
        <ul>
            <li><strong>Total GEX:</strong> {_format_gex_billions(total_abs_gex)} tells you how large the visible gamma map is. Bigger values mean the options levels deserve more respect.</li>
            <li><strong>Call GEX:</strong> {_format_gex_billions(call_gex)} marks upside call-heavy areas. These can behave like resistance, pinning, or breakout trigger zones.</li>
            <li><strong>Put GEX:</strong> {_format_gex_billions(put_gex)} marks downside put-heavy areas. These can behave like support, magnets, or acceleration zones if broken.</li>
        </ul>

        <h4>Key Levels To Watch</h4>
        <p><strong>Support zones:</strong></p>
        <ul>{support_items}</ul>
        <p><strong>Resistance zones:</strong></p>
        <ul>{resistance_items}</ul>
        <p>{escape(support_text)} {escape(resistance_text)}</p>

        <h4>Intraday Read</h4>
        <ul>
            <li><strong>VWAP:</strong> {escape(vwap_explainer)}</li>
            <li><strong>Funding / OI:</strong> {escape(funding_text)}</li>
            <li><strong>IV:</strong> {escape(iv_text)}</li>
            <li><strong>Sweeps:</strong> {sweep_text}</li>
        </ul>

        <h4>How To Use This Today</h4>
        <ul>
            <li><strong>Bullish scenario:</strong> {escape(bullish_case)}</li>
            <li><strong>Bearish scenario:</strong> {escape(bearish_case)}</li>
            <li><strong>Resistance test:</strong> {escape(resistance_case)}</li>
        </ul>

        <h4>Simple Summary</h4>
        <p>Do not treat any GEX level as magic. Treat it as a map of where positioning is heavy. The best trade read comes when price reaction, VWAP, OI, funding, and sweep behavior agree around one of those levels.</p>
        <p class="jarvis-note">This is a plain-English interpretation of the visible public-data screener. The GEX values are still a simple Deribit chain approximation, not exact dealer inventory.</p>
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
    gamma_flip = bundle["gamma_flip"]
    perp = bundle["perp_snapshot"]
    strike_map = bundle["strike_map"]
    expiry_map = bundle["expiry_map"]
    atm_iv = bundle["atm_iv"]
    avwap_df = bundle["avwap_df"]
    sweeps = bundle["sweeps"]
    support_levels = bundle["support_levels"]
    resistance_levels = bundle["resistance_levels"]

    st.subheader("BTC Options Screener")
    st.caption(
        "Phase 1 public-data framework using Deribit BTC options and Binance BTCUSDT perpetuals. "
        "GEX here is a simple call-minus-put gamma approximation built from Deribit open interest and greeks."
    )

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("BTC Spot", f"{spot:,.2f}")
    c2.metric("ATM IV", f"{float(atm_iv['effective_iv'].iloc[0]):.1f}" if not atm_iv.empty else "n/a")
    c3.metric("Net GEX Approx", _format_gex_billions(float(bundle["total_signed_gex"]), signed=True))
    c4.metric("Gamma Flip", f"{gamma_flip:,.0f}" if gamma_flip else "n/a")
    c5.metric("Funding 8H", f"{_safe_float(perp.get('last_funding_rate')):.4%}")
    c6.metric("OI 1H", f"{float(bundle['oi_change_1h']):+.2%}")

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
        st.plotly_chart(_build_gex_strike_chart(strike_map, spot), use_container_width=True)
    with chart_right:
        st.plotly_chart(_build_expiry_map_chart(expiry_map), use_container_width=True)

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
            ]
        )
        st.markdown("### Options / Perp Snapshot")
        _render_options_snapshot_table(summary_rows)

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
        "Overall LTF Strength": "Single summary of the asset's lower-timeframe momentum profile. In this view it is the LTF Momentum score.",
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


def _show_table(df: pd.DataFrame, scoring_mode: str):
    cols = [
        "symbol"
    ]
    if df.empty:
        st.info("No assets match the current filters.")
        return

    if scoring_mode == "HTF Leadership":
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
            "setup_score": st.column_config.NumberColumn("Setup", format="%.1f", help=_term_help("Setup")),
            "momentum_score": st.column_config.NumberColumn("Momentum", format="%.1f", help=_term_help("Momentum")),
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
        f"Colors reflect {config['label']} spot-volume temperature from a rolling z-score window of "
        f"{int(config['z_window'])} bars (about 30 days of context): "
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

    if "app_page" not in st.session_state:
        st.session_state["app_page"] = "Altcoins"
    elif st.session_state["app_page"] == "BTC Options Cockpit":
        st.session_state["app_page"] = "BTC Options Screener"

    page = st.session_state["app_page"]
    options_anchor_mode = "Weekly Open"
    bitcoin_mode = "Bitcoin Spot Vol (Binance)"
    bubble_timeframe = "1D"
    bubble_lookback_days = 365

    with st.sidebar:
        if st.button("Force refresh"):
            st.cache_data.clear()
            st.rerun()

        if st.button("📖 Glossary / Term Guide", use_container_width=True):
            st.session_state["app_page"] = "Glossary / Term Guide"
            st.rerun()

        st.divider()

        with st.expander("Altcoins", expanded=page == "Altcoins"):
            if st.button("Altcoin Screener", key="page_altcoins", use_container_width=True):
                st.session_state["app_page"] = "Altcoins"
                st.rerun()

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

    if page == "Glossary / Term Guide":
        _render_term_guide()
        return

    if page == "BTC Options Screener":
        _render_btc_options_cockpit(options_anchor_mode)
        return

    if page == "BITCOIN":
        _render_bitcoin_section(bitcoin_mode, bubble_timeframe, bubble_lookback_days)
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

    heading_col, glossary_col = st.columns([0.76, 0.24], vertical_alignment="bottom")
    with heading_col:
        st.subheader(f"Top {score_label.lower()} setups ({len(setups)} assets)")
    with glossary_col:
        _render_glossary_jump()
    _show_table(setups.head(top_n), scoring_mode)

    with st.expander(f"Full screener ({len(df)} assets)"):
        _show_table(ranked_df, scoring_mode)


if __name__ == "__main__":
    main()
