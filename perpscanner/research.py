"""Signal research loop: snapshot logging and forward-return validation.

Logging side: every scored scan is appended to a local SQLite store
(throttled). Analysis side: forward returns are joined from later
snapshots of the same store - no extra API calls, no lookahead - and
turned into per-factor rank ICs and an ignition-trigger event study.

Run the report from the repo root:

    py -m perpscanner.research

The report only becomes meaningful once the app has been running (and
logging) for a while: at ~5-minute snapshots, a day of uptime gives
~280 cross-sections.
"""
import sqlite3
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .config import (
    RESEARCH_DB_PATH,
    RESEARCH_FACTOR_COLUMNS,
    RESEARCH_HORIZONS_HOURS,
    RESEARCH_LOG_MIN_INTERVAL_S,
    RESEARCH_LTF_COLUMNS,
    RESEARCH_MIN_GROUP_SIZE,
)
from .utils import _utc_now_naive

METRIC_TABLE = "metric_snapshots"
LTF_TABLE = "ltf_snapshots"


def _connect() -> sqlite3.Connection:
    RESEARCH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(RESEARCH_DB_PATH)


def _last_snapshot_ts(conn: sqlite3.Connection, table: str) -> Optional[pd.Timestamp]:
    try:
        row = conn.execute(f"SELECT MAX(scan_ts) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or row[0] is None:
        return None
    return pd.Timestamp(row[0])


def _snapshot_frame(df: pd.DataFrame, columns: Iterable[str], scan_ts: pd.Timestamp) -> pd.DataFrame:
    keep = ["symbol", "price"] + [c for c in columns]
    out = df.reindex(columns=keep)
    out.insert(0, "scan_ts", scan_ts.isoformat())
    return out


def log_scan_snapshot(metrics_df: Optional[pd.DataFrame], ltf_df: Optional[pd.DataFrame]) -> dict[str, int]:
    """Append the current scan to the research store, throttled.

    Never raises: research logging must not be able to break the app.
    Returns row counts written per table (0 = skipped or empty).
    """
    written = {METRIC_TABLE: 0, LTF_TABLE: 0}
    try:
        now = _utc_now_naive()
        conn = _connect()
        try:
            for table, df, cols in (
                (METRIC_TABLE, metrics_df, RESEARCH_FACTOR_COLUMNS),
                (LTF_TABLE, ltf_df, RESEARCH_LTF_COLUMNS),
            ):
                if df is None or df.empty or "symbol" not in df.columns:
                    continue
                last = _last_snapshot_ts(conn, table)
                if last is not None and (now - last).total_seconds() < RESEARCH_LOG_MIN_INTERVAL_S:
                    continue
                snap = _snapshot_frame(df, cols, now)
                snap.to_sql(table, conn, if_exists="append", index=False)
                written[table] = len(snap)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return written


def _load_table(table: str) -> pd.DataFrame:
    if not RESEARCH_DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()
    if df.empty:
        return df
    df["scan_ts"] = pd.to_datetime(df["scan_ts"])
    return df


def _forward_return_frame(prices: pd.DataFrame, horizon_hours: float, tolerance_frac: float = 0.35) -> pd.DataFrame:
    """Forward return per (scan_ts, symbol) using later snapshots as exits.

    The exit is the first snapshot at or after scan_ts + horizon, within a
    tolerance window, so returns come from the same store that produced
    the signal - no external data, no lookahead.
    """
    px = prices.dropna(subset=["price"]).sort_values("scan_ts")
    if px.empty:
        return pd.DataFrame(columns=["scan_ts", "symbol", "fwd_ret"])
    entries = px.copy()
    entries["target_ts"] = entries["scan_ts"] + pd.Timedelta(hours=horizon_hours)
    exits = px.rename(columns={"scan_ts": "future_ts", "price": "future_price"})
    merged = pd.merge_asof(
        entries.sort_values("target_ts"),
        exits.sort_values("future_ts"),
        left_on="target_ts",
        right_on="future_ts",
        by="symbol",
        direction="forward",
        tolerance=pd.Timedelta(hours=horizon_hours * tolerance_frac),
    )
    merged["fwd_ret"] = merged["future_price"] / merged["price"] - 1.0
    return merged[["scan_ts", "symbol", "fwd_ret"]]


def rank_ic_report(horizons: Iterable[float] = RESEARCH_HORIZONS_HOURS) -> pd.DataFrame:
    """Spearman rank IC of every factor score vs forward returns.

    One IC per scan cross-section (>= RESEARCH_MIN_GROUP_SIZE symbols),
    then mean/std/t-stat across cross-sections per factor and horizon.
    """
    snaps = _load_table(METRIC_TABLE)
    if snaps.empty:
        return pd.DataFrame()
    factors = [c for c in RESEARCH_FACTOR_COLUMNS if c in snaps.columns]
    prices = snaps[["scan_ts", "symbol", "price"]]
    rows = []
    for horizon in horizons:
        fwd = _forward_return_frame(prices, horizon)
        df = snaps.merge(fwd, on=["scan_ts", "symbol"]).dropna(subset=["fwd_ret"])
        if df.empty:
            continue
        grouped = [g for _, g in df.groupby("scan_ts") if len(g) >= RESEARCH_MIN_GROUP_SIZE]
        for factor in factors:
            ics = []
            for g in grouped:
                if g[factor].nunique() < 3:
                    continue
                ic = g[factor].corr(g["fwd_ret"], method="spearman")
                if pd.notna(ic):
                    ics.append(float(ic))
            if not ics:
                continue
            ic_arr = np.asarray(ics)
            std = float(ic_arr.std(ddof=1)) if len(ic_arr) > 1 else 0.0
            rows.append(
                {
                    "factor": factor,
                    "horizon_h": horizon,
                    "mean_ic": float(ic_arr.mean()),
                    "ic_std": std,
                    "t_stat": float(ic_arr.mean() / (std / np.sqrt(len(ic_arr)))) if std > 0 else 0.0,
                    "cross_sections": len(ic_arr),
                }
            )
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .sort_values(["horizon_h", "mean_ic"], ascending=[True, False])
        .reset_index(drop=True)
    )


def trigger_event_study(horizons: Iterable[float] = RESEARCH_HORIZONS_HOURS) -> pd.DataFrame:
    """Forward returns after fresh ignition triggers vs the cross-section.

    Returns are direction-adjusted (short triggers flip sign). Excess is
    measured against the same-timestamp cross-sectional mean, so a long
    trigger during a market-wide pump gets no credit for beta.
    """
    ltf = _load_table(LTF_TABLE)
    if ltf.empty:
        return pd.DataFrame()
    prices = ltf[["scan_ts", "symbol", "price"]].drop_duplicates(subset=["scan_ts", "symbol"])
    triggers = ltf[
        (ltf.get("trigger_fresh", 0).astype(bool))
        & (ltf.get("trigger_direction", "").isin(["Long", "Short"]))
    ]
    if triggers.empty:
        return pd.DataFrame()
    rows = []
    for horizon in horizons:
        fwd = _forward_return_frame(prices, horizon)
        cs_mean = fwd.groupby("scan_ts")["fwd_ret"].mean().rename("cs_mean_ret")
        df = triggers.merge(fwd, on=["scan_ts", "symbol"]).dropna(subset=["fwd_ret"])
        if df.empty:
            continue
        df = df.join(cs_mean, on="scan_ts")
        sign = np.where(df["trigger_direction"] == "Long", 1.0, -1.0)
        df["signed_ret"] = df["fwd_ret"] * sign
        df["excess_ret"] = df["signed_ret"] - df["cs_mean_ret"] * sign
        rows.append(
            {
                "horizon_h": horizon,
                "triggers": int(len(df)),
                "mean_signed_ret": float(df["signed_ret"].mean()),
                "median_signed_ret": float(df["signed_ret"].median()),
                "hit_rate": float((df["signed_ret"] > 0).mean()),
                "mean_excess_ret": float(df["excess_ret"].mean()),
                "excess_hit_rate": float((df["excess_ret"] > 0).mean()),
            }
        )
    return pd.DataFrame(rows)


def snapshot_counts() -> dict[str, object]:
    out: dict[str, object] = {}
    for table in (METRIC_TABLE, LTF_TABLE):
        df = _load_table(table)
        out[table] = {
            "rows": int(len(df)),
            "cross_sections": int(df["scan_ts"].nunique()) if not df.empty else 0,
            "first": str(df["scan_ts"].min()) if not df.empty else "-",
            "last": str(df["scan_ts"].max()) if not df.empty else "-",
        }
    return out


def _print_report() -> None:
    counts = snapshot_counts()
    print("Research store:", RESEARCH_DB_PATH)
    for table, info in counts.items():
        print(f"  {table}: {info['rows']} rows, {info['cross_sections']} cross-sections ({info['first']} .. {info['last']})")
    ic = rank_ic_report()
    if ic.empty:
        print("\nNo rank-IC data yet. Leave the app running so snapshots accumulate.")
    else:
        print("\nRank IC (Spearman, factor score vs forward return):")
        print(ic.to_string(index=False, float_format=lambda v: f"{v: .4f}"))
    ev = trigger_event_study()
    if ev.empty:
        print("\nNo trigger event-study data yet (needs fresh triggers plus forward snapshots).")
    else:
        print("\nIgnition trigger event study (direction-adjusted):")
        print(ev.to_string(index=False, float_format=lambda v: f"{v: .4f}"))


if __name__ == "__main__":
    _print_report()
