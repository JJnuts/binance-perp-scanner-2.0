"""
Binance USDT Perpetual Scanner

Scans active USDT-M perpetual futures on Binance Futures, computes VWAP
z-scores, clusters assets, and auto-refreshes every 5 minutes.
No API key required.
"""

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from streamlit_autorefresh import st_autorefresh
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore")

# Prefer sklearn's HDBSCAN, then the standalone package, then a KMeans fallback.
try:
    from sklearn.cluster import HDBSCAN as _HDBSCAN
    from sklearn.preprocessing import StandardScaler

    def _run_hdbscan(X):
        return _HDBSCAN(min_cluster_size=12, min_samples=5).fit_predict(X)

except ImportError:
    try:
        import hdbscan as _hdbscan_pkg
        from sklearn.preprocessing import StandardScaler

        def _run_hdbscan(X):
            return _hdbscan_pkg.HDBSCAN(min_cluster_size=12, min_samples=5).fit_predict(X)

    except ImportError:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        def _run_hdbscan(X):
            clusters = min(8, len(X))
            if clusters <= 1:
                return [0 for _ in range(len(X))]
            return KMeans(n_clusters=clusters, random_state=42, n_init=10).fit_predict(X)


# Constants
BINANCE_BASE = "https://fapi.binance.com"
INTERVAL = "4h"
CANDLES_2D = 12
CANDLES_5D = 30
CANDLES_7D = 42
MAX_WORKERS = 20
REFRESH_MS = 5 * 60 * 1000
CACHE_TTL = 280
API_TIMEOUT = 15


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
    session.headers.update({"User-Agent": "binance-perp-scanner/1.0"})
    return session


_SESSION = _make_session()


def _get_json(path: str, params: Optional[dict] = None, timeout: int = API_TIMEOUT):
    response = _SESSION.get(f"{BINANCE_BASE}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_usdt_perpetuals() -> list[str]:
    """Return sorted list of actively trading USDT-M perpetual symbols."""
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


def _fetch_one(symbol: str) -> tuple[str, Optional[pd.DataFrame]]:
    """Fetch OHLCV candles for a single symbol."""
    params = {"symbol": symbol, "interval": INTERVAL, "limit": CANDLES_7D}
    try:
        raw = _get_json("/fapi/v1/klines", params=params, timeout=10)
        if not isinstance(raw, list) or len(raw) < CANDLES_5D:
            return symbol, None

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
        for col in ("open", "high", "low", "close", "vol", "quote_vol"):
            df[col] = df[col].astype(float)

        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("ts")[["open", "high", "low", "close", "vol", "quote_vol"]]
        return symbol, df
    except Exception:
        return symbol, None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_all_klines(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Parallel fetch of candles for every symbol."""
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_one, s): s for s in symbols}
        for fut in as_completed(futures):
            sym, df = fut.result()
            if df is not None:
                out[sym] = df
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_funding_rates() -> dict[str, float]:
    """Latest funding rate for every USDT-M symbol."""
    try:
        raw = _get_json("/fapi/v1/premiumIndex", timeout=10)
        return {
            item["symbol"]: float(item["lastFundingRate"])
            for item in raw
            if str(item.get("symbol", "")).endswith("USDT")
            and item.get("lastFundingRate") not in (None, "")
        }
    except Exception:
        return {}


def _vwap(df: pd.DataFrame, n: int) -> float:
    tail = df.tail(n)
    tp = (tail["high"] + tail["low"] + tail["close"]) / 3
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
    return float((df["close"].iloc[-1] - vwap) / std)


def _weekly_return(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    return float((df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0])


def _btc_corr(asset_df: pd.DataFrame, btc_df: pd.DataFrame, n: int = 30) -> float:
    try:
        a = asset_df["close"].pct_change().dropna().tail(n)
        b = btc_df["close"].pct_change().dropna().tail(n)
        merged = pd.concat([a, b], axis=1, join="inner").dropna()
        if len(merged) < 5:
            return 0.0
        return float(merged.iloc[:, 0].corr(merged.iloc[:, 1]))
    except Exception:
        return 0.0


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def build_metrics(symbols: tuple[str, ...]) -> pd.DataFrame:
    """Fetch all data and compute per-asset metrics in one cached call."""
    klines = fetch_all_klines(symbols)
    funding_rates = fetch_funding_rates()
    btc_df = klines.get("BTCUSDT")

    rows = []
    for sym, df in klines.items():
        try:
            rows.append(
                {
                    "symbol": sym,
                    "price": float(df["close"].iloc[-1]),
                    "z2d": _vwap_zscore(df, CANDLES_2D),
                    "z5d": _vwap_zscore(df, CANDLES_5D),
                    "z7d": _vwap_zscore(df, CANDLES_7D),
                    "weekly_ret": _weekly_return(df),
                    "btc_corr": _btc_corr(df, btc_df) if btc_df is not None else 0.0,
                    "funding_rate": funding_rates.get(sym, 0.0),
                }
            )
        except Exception:
            continue

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out["z_diff"] = out["z2d"] - out["z5d"]
    fr_std = out["funding_rate"].std()
    out["funding_z"] = (out["funding_rate"] - out["funding_rate"].mean()) / (fr_std + 1e-10)
    return out


def apply_clustering(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    feats = ["z2d", "z5d", "weekly_ret", "btc_corr", "funding_z"]
    X = StandardScaler().fit_transform(df[feats].fillna(0))
    labels = _run_hdbscan(X)

    df = df.copy()
    df["cluster"] = labels
    df["cluster_label"] = df["cluster"].apply(lambda x: f"Cluster {x}" if x >= 0 else "Noise")
    return df


_FMT = {
    "price": ":,.4f",
    "z2d": ":.2f",
    "z5d": ":.2f",
    "weekly_ret": ":.2%",
    "funding_rate": ":.5f",
    "btc_corr": ":.2f",
}


def _scatter(df: pd.DataFrame, x: str, y: str, xl: str, yl: str, title: str):
    hover = {k: v for k, v in _FMT.items() if k in df.columns}
    hover["cluster_label"] = False

    fig = px.scatter(
        df,
        x=x,
        y=y,
        color="cluster_label",
        hover_name="symbol",
        hover_data=hover,
        labels={x: xl, y: yl},
        title=title,
        template="plotly_dark",
        height=660,
    )
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.2)")
    fig.add_vline(x=0, line_dash="dot", line_color="rgba(255,255,255,0.2)")
    fig.update_traces(marker=dict(size=7, opacity=0.82))
    fig.update_layout(
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font_color="white",
        legend_title="Cluster",
        title_font_size=17,
    )
    return fig


def _show_table(df: pd.DataFrame):
    cols = [
        "symbol",
        "price",
        "z2d",
        "z5d",
        "weekly_ret",
        "funding_rate",
        "btc_corr",
        "cluster_label",
    ]
    if df.empty:
        st.info("No assets match the current filters.")
        return

    table_df = df[cols].copy()
    st.dataframe(
        table_df,
        use_container_width=True,
        height=360,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "price": st.column_config.NumberColumn("Price", format="%.4f"),
            "z2d": st.column_config.NumberColumn("Z2D", format="%.2f"),
            "z5d": st.column_config.NumberColumn("Z5D", format="%.2f"),
            "weekly_ret": st.column_config.NumberColumn("Weekly Return", format="%.2%"),
            "funding_rate": st.column_config.NumberColumn("Funding Rate", format="%.5f"),
            "btc_corr": st.column_config.NumberColumn("BTC Corr", format="%.2f"),
            "cluster_label": st.column_config.TextColumn("Cluster"),
        },
        hide_index=True,
    )


def main():
    st.set_page_config(
        page_title="Binance Perp Scanner",
        page_icon=":satellite:",
        layout="wide",
    )

    st_autorefresh(interval=REFRESH_MS, key="scanner_refresh")

    st.title("Binance USDT Perpetual Scanner")
    st.caption(
        "Scans all USDT-M perpetual futures - VWAP z-scores - HDBSCAN clustering - "
        "refreshes every 5 min - no API key required"
    )

    with st.sidebar:
        st.header("Controls")

        if st.button("Force refresh"):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.subheader("Filters")
        min_z = st.slider("Outlier threshold |z| >", 0.0, 5.0, 2.0, 0.25)
        show_noise = st.checkbox("Show noise cluster", value=True)

        st.divider()
        st.subheader("View mode")
        view = st.radio(
            "Chart",
            [
                "2D / 5D VWAP",
                "2D / 7D VWAP",
                "Weekly Return vs Z-Divergence",
                "VWAP / Funding Rate",
            ],
            index=0,
        )

        st.divider()
        st.caption("Data: Binance Futures public API")
        st.caption("No account or API key needed")

    with st.spinner("Fetching symbol list..."):
        try:
            symbols = tuple(get_usdt_perpetuals())
        except Exception as e:
            st.error(f"Could not connect to Binance: {e}")
            st.stop()

    progress_msg = st.empty()
    progress_msg.info(f"Loading candle data for **{len(symbols)}** perpetuals - first load takes ~15 sec...")

    df = build_metrics(symbols)
    progress_msg.empty()

    if df.empty:
        st.error("No data returned. Check your internet connection and try again.")
        st.stop()

    df = apply_clustering(df)

    if not show_noise:
        df = df[df["cluster"] >= 0]

    n_clusters = int(df[df["cluster"] >= 0]["cluster"].nunique())
    n_outliers = int(((df["z2d"].abs() > min_z) | (df["z5d"].abs() > min_z)).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Assets Scanned", f"{len(df):,}")
    c2.metric("HDBSCAN Clusters", n_clusters)
    c3.metric(f"Outliers |z| > {min_z}", n_outliers)
    c4.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

    st.divider()

    if view == "2D / 5D VWAP":
        fig = _scatter(
            df,
            "z2d",
            "z5d",
            "2-Day VWAP Z-Score",
            "5-Day VWAP Z-Score",
            "2D vs 5D VWAP Z-Score - Regime Detection",
        )
    elif view == "2D / 7D VWAP":
        fig = _scatter(
            df,
            "z2d",
            "z7d",
            "2-Day VWAP Z-Score",
            "7-Day VWAP Z-Score",
            "2D vs 7D VWAP Z-Score - Weekly Perspective",
        )
    elif view == "Weekly Return vs Z-Divergence":
        fig = _scatter(
            df,
            "weekly_ret",
            "z_diff",
            "Weekly Return",
            "2D - 5D Z-Score Divergence",
            "Weekly Losers vs Short-Term Pullbacks",
        )
    else:
        fig = _scatter(
            df,
            "z2d",
            "funding_z",
            "2-Day VWAP Z-Score",
            "Funding Rate Z-Score",
            "VWAP Extension vs Funding Rate Crowding",
        )

    st.plotly_chart(fig, use_container_width=True)

    outliers = df[(df["z2d"].abs() > min_z) | (df["z5d"].abs() > min_z)].sort_values(
        "z2d", ascending=False
    )
    st.subheader(f"Outliers |z| > {min_z} ({len(outliers)} assets)")
    _show_table(outliers)

    with st.expander(f"Full asset table ({len(df)} assets)"):
        _show_table(df.sort_values("z2d", ascending=False))


if __name__ == "__main__":
    main()
