"""Constants, thresholds, weights, palette, and term guide."""

from pathlib import Path


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
# Ignition trigger: hard vetoes (VWAP side + break-and-hold) plus a
# weighted confluence of the remaining confirmations. The old 7-way AND
# almost never fired outside broad market-wide moves.
CONFLUENCE_TRIGGER_THRESHOLD = 0.60
CONFLUENCE_WEIGHTS = {"expansion": 0.30, "volume": 0.25, "oi": 0.20, "taker": 0.15, "basis": 0.10}
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
# Repo root (one level above the perpscanner package) so the data dir
# stays where the monolithic scanner.py kept it.
REPO_ROOT = Path(__file__).resolve().parent.parent
BTC_OPTIONS_HISTORY_PATH = REPO_ROOT / "data" / "btc_options_history.csv"
BTC_OPTIONS_BLOCK_DB_PATH = REPO_ROOT / "data" / "deribit_block_trades.sqlite"
# Research loop: every scored scan snapshot is appended here so the
# scores can be validated against forward returns (rank IC, trigger
# event studies) instead of staying vibe-calibrated.
RESEARCH_DB_PATH = REPO_ROOT / "data" / "research_snapshots.sqlite"
RESEARCH_LOG_MIN_INTERVAL_S = 240
RESEARCH_HORIZONS_HOURS = (1.0, 4.0, 24.0)
RESEARCH_MIN_GROUP_SIZE = 10
RESEARCH_FACTOR_COLUMNS = [
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
    "momentum_score",
    "htf_momentum_score",
    "setup_score",
    "htf_setup_score",
    "overextension_score",
    "htf_expansion_score",
]
RESEARCH_LTF_COLUMNS = [
    "ignition_tf",
    "ignition_state",
    "trigger_direction",
    "trigger_fresh",
    "bars_since_trigger",
    "ltf_ignition_score",
    "confluence_long",
    "confluence_short",
    "volume_zscore",
    "oi_zscore",
    "taker_imbalance",
    "regime",
]
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
