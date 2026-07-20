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
    CONFLUENCE_WEIGHTS,
    RESEARCH_DB_PATH,
    RESEARCH_FACTOR_COLUMNS,
    RESEARCH_HORIZONS_HOURS,
    RESEARCH_LOG_MIN_INTERVAL_S,
    RESEARCH_LTF_COLUMNS,
    RESEARCH_MIN_GROUP_SIZE,
    RESEARCH_WEIGHT_MIN_CROSS_SECTIONS,
    RESEARCH_WEIGHT_MIN_GROUP,
)
from .utils import _utc_now_naive

METRIC_TABLE = "metric_snapshots"
LTF_TABLE = "ltf_snapshots"

# Confluence trigger components as logged in the LTF table. Side-dependent
# components carry a signed net (long minus short); direction-neutral ones
# are plain [0, 1] gate fractions.
CONFLUENCE_COMPONENT_COLUMNS = {
    "expansion": "comp_expansion",
    "volume": "comp_volume",
    "oi": "comp_oi",
    "taker": "comp_taker_net",
    "basis": "comp_basis_net",
}


def _spearman_corr(left: pd.Series, right: pd.Series) -> float:
    """Spearman correlation without pandas' optional SciPy dependency."""
    paired = pd.concat(
        [pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")],
        axis=1,
    ).dropna()
    if len(paired) < 2:
        return float("nan")
    return float(paired.iloc[:, 0].rank().corr(paired.iloc[:, 1].rank()))


def _overlap_lag(timestamps: Iterable[object], horizon_hours: float) -> int:
    """Estimate how many adjacent scan ICs share the same return window."""
    ts = pd.Series(pd.to_datetime(list(timestamps))).dropna().sort_values()
    if len(ts) < 2:
        return 0
    spacing_s = ts.diff().dt.total_seconds().dropna()
    spacing_s = spacing_s[spacing_s > 0]
    if spacing_s.empty:
        return 0
    lag = int(np.ceil(horizon_hours * 3600.0 / float(spacing_s.median()))) - 1
    return max(0, min(lag, len(ts) - 1))


def _newey_west_t_stat(values: Iterable[float], max_lag: int) -> float:
    """T-stat for a mean with Bartlett-weighted autocorrelation correction."""
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return 0.0
    centered = arr - float(arr.mean())
    gamma0 = float(np.dot(centered, centered) / len(arr))
    if gamma0 <= 0.0:
        return 0.0
    lag_limit = max(0, min(int(max_lag), len(arr) - 1))
    long_run_variance = gamma0
    for lag in range(1, lag_limit + 1):
        covariance = float(np.dot(centered[lag:], centered[:-lag]) / len(arr))
        weight = 1.0 - lag / (lag_limit + 1.0)
        long_run_variance += 2.0 * weight * covariance
    if long_run_variance <= 0.0:
        return 0.0
    standard_error = float(np.sqrt(long_run_variance / len(arr)))
    return float(arr.mean() / standard_error) if standard_error > 0.0 else 0.0


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


def _ensure_table_columns(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> None:
    """ALTER an existing snapshot table so new columns keep logging.

    to_sql(if_exists="append") raises on columns the table has never seen,
    and log_scan_snapshot swallows all exceptions by design - so without
    this, adding a column to the schema would silently stop the research
    log on any store created before the change.
    """
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return
    if not info:
        return  # table does not exist yet; to_sql will create it in full
    existing = {row[1] for row in info}
    for column in df.columns:
        if column not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN "{column}"')


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
                _ensure_table_columns(conn, table, snap)
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
    # isoformat() omits microseconds when they are zero, so a store can
    # legitimately hold mixed ISO precisions; default inference raises on
    # that mix in pandas >= 2.
    df["scan_ts"] = pd.to_datetime(df["scan_ts"], format="ISO8601")
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
        grouped = [(ts, g) for ts, g in df.groupby("scan_ts") if len(g) >= RESEARCH_MIN_GROUP_SIZE]
        for factor in factors:
            ics = []
            ic_times = []
            for scan_ts, g in grouped:
                if g[factor].nunique() < 3:
                    continue
                ic = _spearman_corr(g[factor], g["fwd_ret"])
                if pd.notna(ic):
                    ics.append(float(ic))
                    ic_times.append(scan_ts)
            if not ics:
                continue
            ic_arr = np.asarray(ics)
            std = float(ic_arr.std(ddof=1)) if len(ic_arr) > 1 else 0.0
            overlap_lag = _overlap_lag(ic_times, horizon)
            rows.append(
                {
                    "factor": factor,
                    "horizon_h": horizon,
                    "mean_ic": float(ic_arr.mean()),
                    "ic_std": std,
                    "t_stat": _newey_west_t_stat(ic_arr, overlap_lag),
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


def confluence_component_ic(horizons: Iterable[float] = RESEARCH_HORIZONS_HOURS) -> pd.DataFrame:
    """Directional rank IC of each confluence trigger component.

    Rows are conditioned on a structural veto side being active - exactly
    the situations where the trigger actually consumes the components.
    Short-side rows flip both the forward return and the side-dependent
    components (taker, basis), so positive IC always reads "the component
    ranked the continuations correctly in the direction being considered".
    """
    ltf = _load_table(LTF_TABLE)
    if ltf.empty or "veto_side" not in ltf.columns:
        return pd.DataFrame()
    prices = ltf[["scan_ts", "symbol", "price"]].drop_duplicates(subset=["scan_ts", "symbol"])
    active = ltf[ltf["veto_side"].isin(["Long", "Short"])].copy()
    if active.empty:
        return pd.DataFrame()
    side_sign = np.where(active["veto_side"] == "Long", 1.0, -1.0)
    for column in ("comp_taker_net", "comp_basis_net"):
        if column in active.columns:
            active[column] = pd.to_numeric(active[column], errors="coerce") * side_sign
    rows = []
    for horizon in horizons:
        fwd = _forward_return_frame(prices, horizon)
        df = active.merge(fwd, on=["scan_ts", "symbol"]).dropna(subset=["fwd_ret"])
        if df.empty:
            continue
        df["directional_ret"] = df["fwd_ret"] * np.where(df["veto_side"] == "Long", 1.0, -1.0)
        grouped = [(ts, g) for ts, g in df.groupby("scan_ts") if len(g) >= RESEARCH_WEIGHT_MIN_GROUP]
        for component, column in CONFLUENCE_COMPONENT_COLUMNS.items():
            if column not in df.columns:
                continue
            ics = []
            ic_times = []
            for scan_ts, g in grouped:
                values = pd.to_numeric(g[column], errors="coerce")
                if values.nunique() < 3:
                    continue
                ic = _spearman_corr(values, g["directional_ret"])
                if pd.notna(ic):
                    ics.append(float(ic))
                    ic_times.append(scan_ts)
            if not ics:
                continue
            ic_arr = np.asarray(ics)
            std = float(ic_arr.std(ddof=1)) if len(ic_arr) > 1 else 0.0
            overlap_lag = _overlap_lag(ic_times, horizon)
            rows.append(
                {
                    "component": component,
                    "horizon_h": horizon,
                    "mean_ic": float(ic_arr.mean()),
                    "ic_std": std,
                    "t_stat": _newey_west_t_stat(ic_arr, overlap_lag),
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


def suggest_confluence_weights(ic_df: Optional[pd.DataFrame] = None) -> dict[str, object]:
    """IC-proportional CONFLUENCE_WEIGHTS suggestion, with guardrails.

    Pools each component's mean IC across horizons, floors negatives at
    zero, and renormalises to sum 1. Advisory only - nothing is applied
    automatically; the report prints a paste-ready config line instead.
    Until every component has RESEARCH_WEIGHT_MIN_CROSS_SECTIONS
    cross-sections behind it the current weights are returned unchanged,
    so thin early data cannot argue for rewriting the trigger.
    """
    if ic_df is None:
        ic_df = confluence_component_ic()
    current = {k: float(v) for k, v in CONFLUENCE_WEIGHTS.items()}
    if ic_df is None or ic_df.empty:
        return {"weights": current, "pooled": pd.DataFrame(), "ready": False, "reason": "no component IC data yet"}
    pooled = ic_df.groupby("component").agg(
        mean_ic=("mean_ic", "mean"),
        min_cross_sections=("cross_sections", "min"),
    )
    missing = [c for c in CONFLUENCE_WEIGHTS if c not in pooled.index]
    if missing:
        return {
            "weights": current,
            "pooled": pooled,
            "ready": False,
            "reason": f"components without measurable IC yet: {', '.join(missing)}",
        }
    thin = pooled[pooled["min_cross_sections"] < RESEARCH_WEIGHT_MIN_CROSS_SECTIONS]
    if not thin.empty:
        return {
            "weights": current,
            "pooled": pooled,
            "ready": False,
            "reason": (
                f"insufficient cross-sections (< {RESEARCH_WEIGHT_MIN_CROSS_SECTIONS}) for: "
                f"{', '.join(thin.index)}"
            ),
        }
    floored = pooled["mean_ic"].clip(lower=0.0)
    if float(floored.sum()) <= 0.0:
        return {
            "weights": current,
            "pooled": pooled,
            "ready": False,
            "reason": "no component shows positive directional IC; keep current weights and investigate",
        }
    normalised = floored / float(floored.sum())
    suggested = {k: round(float(normalised[k]), 4) for k in CONFLUENCE_WEIGHTS}
    return {"weights": suggested, "pooled": pooled, "ready": True, "reason": "ok"}


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
    comp_ic = confluence_component_ic()
    if comp_ic.empty:
        print("\nNo confluence component data yet (needs veto-active snapshots plus forward returns).")
    else:
        print("\nConfluence component IC (directional, veto-conditioned):")
        print(comp_ic.to_string(index=False, float_format=lambda v: f"{v: .4f}"))
        suggestion = suggest_confluence_weights(comp_ic)
        print("\nCurrent CONFLUENCE_WEIGHTS:", {k: float(v) for k, v in CONFLUENCE_WEIGHTS.items()})
        if suggestion["ready"]:
            print("Suggested (IC-proportional):", suggestion["weights"])
            print("To apply, review and paste into perpscanner/config.py:")
            print(f"CONFLUENCE_WEIGHTS = {suggestion['weights']}")
        else:
            print(f"No weight suggestion yet: {suggestion['reason']}")


if __name__ == "__main__":
    _print_report()
