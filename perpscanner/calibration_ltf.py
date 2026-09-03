"""Fail-closed helpers for the staged Alt Screener LTF calibration.

This module is deliberately not imported by the live scanner or UI.  It has no
CLI entry point and never falls back to the live research database.  Callers
must supply a checksum-verified locked protocol and explicitly select one of
its bounded partitions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_PARTITIONS = ("training", "internal_validation", "locked_confirmation")
_EXPECTED_ROLES = {
    "candidate_selection": "training",
    "internal_validation": "internal_validation",
    "locked_confirmation": "locked_confirmation",
}

LTF_FACTOR_COLUMNS = {
    "alpha": "alpha_score",
    "relative_strength": "relative_strength_score",
    "volume": "volume_score",
    "trend": "trend_score",
    "oi": "oi_score",
    "funding_quality": "funding_quality_score",
}
LTF_CANDIDATE_RAW_COLUMNS = {
    "premium_level_candidate": "premium_bp",
    "premium_roc_candidate": "premium_roc_bp_h",
}
LTF_EVALUATION_HORIZONS = (1.0, 4.0, 24.0)
LTF_OBJECTIVE_HORIZON_WEIGHTS = {1.0: 0.70, 4.0: 0.30}
LTF_PRIMARY_HORIZON = 1.0


class CalibrationGuardError(RuntimeError):
    """Raised whenever a calibration safety invariant is not satisfied."""


@dataclass(frozen=True)
class WeightCandidate:
    candidate_id: str
    weights: dict[str, float]
    source_factor: str | None
    destination_factor: str | None
    transfer: float


@dataclass(frozen=True)
class LTFCandidateEvaluation:
    candidate: WeightCandidate
    mean_ic_by_horizon: dict[float, float]
    dashboard_excess_by_horizon: dict[float, float]
    objective_ic: float
    epoch_objective: dict[int, float]
    rank_ic_by_horizon: dict[float, pd.DataFrame]
    dashboard_by_horizon: dict[float, pd.DataFrame]


@dataclass(frozen=True)
class TrainingGate:
    objective_delta_minimum: float = 0.005
    primary_ic_minimum: float = 0.0
    maximum_epoch_objective_shortfall: float = 0.0025
    primary_dashboard_excess_delta_minimum: float = 0.0005


@dataclass(frozen=True)
class CandidateGateDecision:
    candidate_id: str
    passed: bool
    reasons: tuple[str, ...]
    objective_delta: float
    primary_dashboard_excess_delta: float
    worst_epoch_objective: float
    l1_distance: float


@dataclass(frozen=True)
class TrainingStageResult:
    partition_name: str
    evaluated: tuple[LTFCandidateEvaluation, ...]
    decisions: tuple[CandidateGateDecision, ...]
    winner: LTFCandidateEvaluation | None
    baseline_max_abs_error: float
    candidate_count: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_utc_naive(value: object, label: str) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise CalibrationGuardError(f"Invalid {label}: {value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed


def _partition_map(protocol: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    rows = protocol.get("partitions")
    if not isinstance(rows, list):
        raise CalibrationGuardError("Protocol partitions must be a list")
    mapped: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
            raise CalibrationGuardError("Every partition requires a string name")
        name = str(row["name"])
        if name in mapped:
            raise CalibrationGuardError(f"Duplicate partition: {name}")
        mapped[name] = row
    if tuple(mapped) != _REQUIRED_PARTITIONS:
        raise CalibrationGuardError(
            f"Partitions must appear exactly as {_REQUIRED_PARTITIONS}; got {tuple(mapped)}"
        )
    return mapped


def validate_locked_protocol(protocol: Mapping[str, object]) -> None:
    if protocol.get("state") != "locked_before_optimization":
        raise CalibrationGuardError("Protocol is not locked before optimization")
    if protocol.get("optimization_performed") is not False:
        raise CalibrationGuardError("Protocol already records optimization")
    protected = protocol.get("protected_inputs")
    if not isinstance(protected, Mapping):
        raise CalibrationGuardError("Protocol has no protected_inputs object")
    if protected.get("sealed_holdout_accessed") is not False:
        raise CalibrationGuardError("Protocol does not attest that the holdout is sealed")
    cutoff = _parse_utc_naive(protected.get("cutoff_inclusive_utc"), "cutoff")
    partitions = _partition_map(protocol)
    prior_end: pd.Timestamp | None = None
    for name in _REQUIRED_PARTITIONS:
        row = partitions[name]
        start = _parse_utc_naive(row.get("start_inclusive_utc"), f"{name} start")
        end = _parse_utc_naive(row.get("end_inclusive_utc"), f"{name} end")
        if start > end:
            raise CalibrationGuardError(f"Partition {name} starts after it ends")
        if end > cutoff:
            raise CalibrationGuardError(f"Partition {name} crosses the sealed cutoff")
        if prior_end is not None and start <= prior_end:
            raise CalibrationGuardError(f"Partition {name} overlaps its predecessor")
        prior_end = end
    ranking = protocol.get("ranking_experiments")
    if not isinstance(ranking, Mapping) or not isinstance(ranking.get("ltf"), Mapping):
        raise CalibrationGuardError("Protocol has no LTF ranking experiment")
    if ranking["ltf"].get("experiment_id") != "alt-ltf-rank-v1":
        raise CalibrationGuardError("Unexpected LTF experiment id")


def load_locked_protocol(path: Path, expected_sha256: str) -> dict[str, object]:
    if not path.is_file():
        raise CalibrationGuardError(f"Protocol file does not exist: {path}")
    observed = sha256_file(path)
    if observed.lower() != str(expected_sha256).lower():
        raise CalibrationGuardError("Protocol SHA-256 mismatch")
    try:
        protocol = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationGuardError("Protocol is not valid UTF-8 JSON") from exc
    if not isinstance(protocol, dict):
        raise CalibrationGuardError("Protocol root must be an object")
    validate_locked_protocol(protocol)
    return protocol


def resolve_partition(
    protocol: Mapping[str, object],
    name: str,
    *,
    purpose: str,
) -> Mapping[str, object]:
    validate_locked_protocol(protocol)
    expected = _EXPECTED_ROLES.get(purpose)
    if expected is None:
        raise CalibrationGuardError(f"Unsupported evaluation purpose: {purpose}")
    if name != expected:
        raise CalibrationGuardError(
            f"Purpose {purpose!r} is restricted to partition {expected!r}, not {name!r}"
        )
    return _partition_map(protocol)[name]


@contextmanager
def connect_verified_database(path: Path, expected_sha256: str) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise CalibrationGuardError(f"Frozen database does not exist: {path}")
    if sha256_file(path).lower() != str(expected_sha256).lower():
        raise CalibrationGuardError("Frozen database SHA-256 mismatch")
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise CalibrationGuardError("Could not open frozen database read-only") from exc
    try:
        yield connection
    finally:
        connection.close()


def open_protocol_database(protocol: Mapping[str, object]) -> Iterator[sqlite3.Connection]:
    validate_locked_protocol(protocol)
    protected = protocol["protected_inputs"]
    return connect_verified_database(
        Path(str(protected["database"])),
        str(protected["database_sha256"]),
    )


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise CalibrationGuardError(f"Unsafe SQL identifier: {value!r}")
    return value


def _load_partition_frame(
    connection: sqlite3.Connection,
    partition: Mapping[str, object],
    *,
    table: str,
    columns: Sequence[str],
) -> pd.DataFrame:
    if table not in {"metric_snapshots", "ltf_snapshots"}:
        raise CalibrationGuardError(f"Unsupported table: {table}")
    if not columns or "scan_ts" not in columns:
        raise CalibrationGuardError("Partition reads must include scan_ts")
    safe_columns = [_safe_identifier(str(column)) for column in columns]
    start = _parse_utc_naive(partition.get("start_inclusive_utc"), "partition start")
    end = _parse_utc_naive(partition.get("end_inclusive_utc"), "partition end")
    sql = (
        f'SELECT {", ".join(f"\"{column}\"" for column in safe_columns)} '
        f'FROM "{table}" WHERE scan_ts >= ? AND scan_ts <= ? ORDER BY scan_ts, symbol'
    )
    try:
        frame = pd.read_sql_query(sql, connection, params=(start.isoformat(), end.isoformat()))
    except Exception as exc:
        raise CalibrationGuardError(f"Could not read {table} partition") from exc
    if frame.empty:
        raise CalibrationGuardError(f"Partition read returned no rows from {table}")
    frame["scan_ts"] = pd.to_datetime(frame["scan_ts"], format="ISO8601")
    if frame["scan_ts"].min() < start or frame["scan_ts"].max() > end:
        raise CalibrationGuardError("Loaded rows escaped the requested partition")
    return frame


def load_protocol_partition_frame(
    connection: sqlite3.Connection,
    protocol: Mapping[str, object],
    partition_name: str,
    *,
    purpose: str,
    table: str,
    columns: Sequence[str],
) -> pd.DataFrame:
    partition = resolve_partition(protocol, partition_name, purpose=purpose)
    return _load_partition_frame(
        connection,
        partition,
        table=table,
        columns=columns,
    )


def _build_partition_forward_returns(
    prices: pd.DataFrame,
    horizon_hours: float,
    partition: Mapping[str, object],
    *,
    tolerance_fraction: float = 0.35,
) -> pd.DataFrame:
    required = {"scan_ts", "symbol", "price"}
    if not required.issubset(prices.columns):
        raise CalibrationGuardError(f"Prices are missing columns: {sorted(required - set(prices.columns))}")
    if horizon_hours <= 0 or tolerance_fraction < 0:
        raise CalibrationGuardError("Forward horizon and tolerance must be nonnegative")
    start = _parse_utc_naive(partition.get("start_inclusive_utc"), "partition start")
    end = _parse_utc_naive(partition.get("end_inclusive_utc"), "partition end")
    px = prices[["scan_ts", "symbol", "price"]].copy()
    px["scan_ts"] = pd.to_datetime(px["scan_ts"], format="ISO8601")
    px["price"] = pd.to_numeric(px["price"], errors="coerce")
    px = px[(px["scan_ts"] >= start) & (px["scan_ts"] <= end)]
    if px.empty:
        raise CalibrationGuardError("No price rows remain inside the partition")
    if px.duplicated(["scan_ts", "symbol"]).any():
        raise CalibrationGuardError("Duplicate (scan_ts, symbol) price rows")
    if (
        not np.isfinite(px["price"].to_numpy(dtype=float)).all()
        or (px["price"] <= 0).any()
    ):
        raise CalibrationGuardError("Prices must be finite and positive")
    px = px.sort_values("scan_ts")
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
        tolerance=pd.Timedelta(hours=horizon_hours * tolerance_fraction),
    )
    valid = merged["future_ts"].notna()
    if (merged.loc[valid, "future_ts"] > end).any() or (merged.loc[valid, "future_ts"] < start).any():
        raise CalibrationGuardError("A forward label crossed its partition boundary")
    if (merged.loc[valid, "future_ts"] < merged.loc[valid, "target_ts"]).any():
        raise CalibrationGuardError("A forward label precedes its target timestamp")
    merged["fwd_ret"] = merged["future_price"] / merged["price"] - 1.0
    return merged[["scan_ts", "symbol", "target_ts", "future_ts", "fwd_ret"]]


def build_protocol_partition_forward_returns(
    prices: pd.DataFrame,
    horizon_hours: float,
    protocol: Mapping[str, object],
    partition_name: str,
    *,
    purpose: str,
    tolerance_fraction: float = 0.35,
) -> pd.DataFrame:
    partition = resolve_partition(protocol, partition_name, purpose=purpose)
    return _build_partition_forward_returns(
        prices,
        horizon_hours,
        partition,
        tolerance_fraction=tolerance_fraction,
    )


def validate_weight_vector(
    weights: Mapping[str, float],
    bounds: Mapping[str, Sequence[float]],
    *,
    baseline: Mapping[str, float] | None = None,
    maximum_l1_distance: float | None = None,
    tolerance: float = 1e-9,
) -> dict[str, float]:
    if tuple(weights) != tuple(bounds):
        raise CalibrationGuardError("Weight and bound factor order/key sets differ")
    normalised: dict[str, float] = {}
    for factor, value in weights.items():
        numeric = float(value)
        if not math.isfinite(numeric):
            raise CalibrationGuardError(f"Non-finite weight for {factor}")
        interval = bounds[factor]
        if len(interval) != 2:
            raise CalibrationGuardError(f"Invalid bounds for {factor}")
        lower, upper = map(float, interval)
        if numeric < lower - tolerance or numeric > upper + tolerance:
            raise CalibrationGuardError(f"Weight for {factor} is outside [{lower}, {upper}]")
        normalised[factor] = round(numeric, 10)
    if abs(sum(normalised.values()) - 1.0) > tolerance:
        raise CalibrationGuardError("Weights must sum to one")
    if baseline is not None and maximum_l1_distance is not None:
        if tuple(baseline) != tuple(weights):
            raise CalibrationGuardError("Baseline and candidate factor order/key sets differ")
        l1 = sum(abs(float(weights[key]) - float(baseline[key])) for key in weights)
        if l1 > float(maximum_l1_distance) + tolerance:
            raise CalibrationGuardError("Candidate exceeds maximum L1 distance")
    return normalised


def generate_single_transfer_candidates(
    baseline: Mapping[str, float],
    bounds: Mapping[str, Sequence[float]],
    *,
    transfer_sizes: Iterable[float] = (0.05, 0.10, 0.15),
    maximum_l1_distance: float = 0.30,
    maximum_candidates: int = 127,
) -> list[WeightCandidate]:
    baseline_checked = validate_weight_vector(baseline, bounds)
    factor_order = tuple(baseline_checked)
    candidates = [WeightCandidate("baseline", baseline_checked, None, None, 0.0)]
    for source in factor_order:
        if baseline_checked[source] <= 0:
            continue
        for destination in factor_order:
            if destination == source:
                continue
            for transfer in transfer_sizes:
                amount = round(float(transfer), 10)
                if amount <= 0:
                    raise CalibrationGuardError("Transfer sizes must be positive")
                weights = dict(baseline_checked)
                weights[source] = round(weights[source] - amount, 10)
                weights[destination] = round(weights[destination] + amount, 10)
                try:
                    checked = validate_weight_vector(
                        weights,
                        bounds,
                        baseline=baseline_checked,
                        maximum_l1_distance=maximum_l1_distance,
                    )
                except CalibrationGuardError:
                    continue
                candidates.append(
                    WeightCandidate(
                        f"transfer:{source}->{destination}:{amount:.2f}",
                        checked,
                        source,
                        destination,
                        amount,
                    )
                )
    ids = [candidate.candidate_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise CalibrationGuardError("Candidate generation produced duplicate ids")
    if len(candidates) > int(maximum_candidates):
        raise CalibrationGuardError(
            f"Candidate count {len(candidates)} exceeds locked maximum {maximum_candidates}"
        )
    return candidates


def score_ltf_candidate(frame: pd.DataFrame, weights: Mapping[str, float]) -> pd.DataFrame:
    expected = tuple(LTF_FACTOR_COLUMNS) + tuple(LTF_CANDIDATE_RAW_COLUMNS)
    if tuple(weights) != expected:
        raise CalibrationGuardError(f"Unexpected LTF weight keys/order: {tuple(weights)}")
    required = {"scan_ts", "symbol", "overextension_score"}
    required.update(LTF_FACTOR_COLUMNS.values())
    for factor, raw_column in LTF_CANDIDATE_RAW_COLUMNS.items():
        if float(weights[factor]) > 0:
            required.add(raw_column)
    missing = required - set(frame.columns)
    if missing:
        raise CalibrationGuardError(f"LTF scoring frame is missing columns: {sorted(missing)}")
    out = frame.copy()
    out["scan_ts"] = pd.to_datetime(out["scan_ts"], format="ISO8601")
    score = pd.Series(0.0, index=out.index)
    for factor, column in LTF_FACTOR_COLUMNS.items():
        values = pd.to_numeric(out[column], errors="coerce")
        if float(weights[factor]) > 0 and not np.isfinite(values.to_numpy(dtype=float)).all():
            raise CalibrationGuardError(f"Non-finite values in active factor {factor}")
        score = score + float(weights[factor]) * values.fillna(0.0)
    for factor, raw_column in LTF_CANDIDATE_RAW_COLUMNS.items():
        weight = float(weights[factor])
        if weight <= 0:
            continue
        raw = pd.to_numeric(out[raw_column], errors="coerce")
        if not np.isfinite(raw.to_numpy(dtype=float)).all():
            raise CalibrationGuardError(f"Non-finite values in active candidate {factor}")
        transformed = raw.groupby(out["scan_ts"]).rank(method="average", pct=True) * 100.0
        score = score + weight * transformed
    out["candidate_score"] = score
    overextension = pd.to_numeric(out["overextension_score"], errors="coerce")
    if not np.isfinite(overextension.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("Non-finite overextension_score")
    out["candidate_setup_score"] = (out["candidate_score"] - 0.45 * overextension).clip(lower=0.0)
    return out


def verify_baseline_score_parity(
    frame: pd.DataFrame,
    baseline_weights: Mapping[str, float],
    *,
    stored_column: str = "momentum_score",
    absolute_tolerance: float = 1e-9,
) -> float:
    if stored_column not in frame.columns:
        raise CalibrationGuardError(f"Missing stored baseline column: {stored_column}")
    scored = score_ltf_candidate(frame, baseline_weights)
    stored = pd.to_numeric(scored[stored_column], errors="coerce")
    if not np.isfinite(stored.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("Stored baseline score contains non-finite values")
    maximum_error = float((scored["candidate_score"] - stored).abs().max())
    if maximum_error > float(absolute_tolerance):
        raise CalibrationGuardError(
            f"Baseline score parity failed: max abs error {maximum_error:.12g}"
        )
    return maximum_error


def cross_section_rank_ic(
    scored: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    score_column: str = "candidate_score",
    minimum_group_size: int = 10,
) -> pd.DataFrame:
    required_scored = {"scan_ts", "symbol", score_column}
    required_forward = {"scan_ts", "symbol", "fwd_ret"}
    if not required_scored.issubset(scored.columns):
        raise CalibrationGuardError("Scored frame lacks rank-IC columns")
    if not required_forward.issubset(forward.columns):
        raise CalibrationGuardError("Forward frame lacks rank-IC columns")
    merged = scored[list(required_scored)].merge(
        forward[list(required_forward)], on=["scan_ts", "symbol"], how="inner"
    )
    merged[score_column] = pd.to_numeric(merged[score_column], errors="coerce")
    merged["fwd_ret"] = pd.to_numeric(merged["fwd_ret"], errors="coerce")
    merged = merged.dropna(subset=[score_column, "fwd_ret"])
    rows = []
    for scan_ts, group in merged.groupby("scan_ts"):
        if len(group) < int(minimum_group_size) or group[score_column].nunique() < 3:
            continue
        ic = group[score_column].rank().corr(group["fwd_ret"].rank())
        if pd.notna(ic):
            rows.append({"scan_ts": pd.Timestamp(scan_ts), "rank_ic": float(ic), "symbols": int(len(group))})
    return pd.DataFrame(rows, columns=["scan_ts", "rank_ic", "symbols"])


def exact_dashboard_excess(
    scored: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    score_floor: float = 70.0,
    overextension_ceiling: float = 65.0,
    top_n: int = 30,
) -> pd.DataFrame:
    required = {"scan_ts", "symbol", "candidate_score", "candidate_setup_score", "overextension_score"}
    if not required.issubset(scored.columns):
        raise CalibrationGuardError("Scored frame lacks dashboard columns")
    merged = scored[list(required)].merge(
        forward[["scan_ts", "symbol", "fwd_ret"]], on=["scan_ts", "symbol"], how="inner"
    ).dropna(subset=["fwd_ret"])
    universe = merged.groupby("scan_ts")["fwd_ret"].mean().rename("universe_ret")
    selected = merged[
        (merged["candidate_score"] >= float(score_floor))
        & (merged["overextension_score"] <= float(overextension_ceiling))
    ]
    selected = (
        selected.sort_values(
            ["scan_ts", "candidate_setup_score", "candidate_score"],
            ascending=[True, False, False],
        )
        .groupby("scan_ts", sort=False)
        .head(int(top_n))
    )
    if selected.empty:
        return pd.DataFrame(columns=["scan_ts", "selected", "selected_ret", "universe_ret", "excess_ret"])
    per_scan = selected.groupby("scan_ts")["fwd_ret"].agg(selected="size", selected_ret="mean")
    per_scan = per_scan.join(universe, how="left")
    per_scan["excess_ret"] = per_scan["selected_ret"] - per_scan["universe_ret"]
    return per_scan.reset_index()


def weighted_ic_objective(ic_by_horizon: Mapping[float, pd.DataFrame], horizon_weights: Mapping[float, float]) -> float:
    if abs(sum(float(value) for value in horizon_weights.values()) - 1.0) > 1e-9:
        raise CalibrationGuardError("Objective horizon weights must sum to one")
    result = 0.0
    for horizon, weight in horizon_weights.items():
        report = ic_by_horizon.get(float(horizon))
        if report is None or report.empty or "rank_ic" not in report.columns:
            raise CalibrationGuardError(f"Missing rank IC for objective horizon {horizon}")
        mean_ic = float(pd.to_numeric(report["rank_ic"], errors="coerce").mean())
        if not math.isfinite(mean_ic):
            raise CalibrationGuardError(f"Non-finite rank IC for objective horizon {horizon}")
        result += float(weight) * mean_ic
    return float(result)


def _assert_frame_inside_partition(
    frame: pd.DataFrame,
    partition: Mapping[str, object],
    *,
    label: str,
) -> pd.DataFrame:
    if frame.empty or "scan_ts" not in frame.columns:
        raise CalibrationGuardError(f"{label} is empty or lacks scan_ts")
    start = _parse_utc_naive(partition.get("start_inclusive_utc"), "partition start")
    end = _parse_utc_naive(partition.get("end_inclusive_utc"), "partition end")
    timestamps = pd.to_datetime(frame["scan_ts"], format="ISO8601")
    if timestamps.isna().any() or (timestamps < start).any() or (timestamps > end).any():
        raise CalibrationGuardError(f"{label} contains rows outside the authorized partition")
    out = frame.copy()
    out["scan_ts"] = timestamps
    return out


def _natural_epoch_map(timestamps: pd.Series, *, gap_hours: float = 24.0) -> pd.Series:
    unique = pd.Series(pd.to_datetime(timestamps, format="ISO8601").unique()).sort_values()
    if unique.empty:
        raise CalibrationGuardError("Cannot derive epochs from empty timestamps")
    epoch = (unique.diff() > pd.Timedelta(hours=gap_hours)).cumsum().astype(int) + 1
    return pd.Series(epoch.to_numpy(), index=pd.DatetimeIndex(unique), name="epoch")


def evaluate_ltf_candidate_metrics(
    metric_frame: pd.DataFrame,
    forward_by_horizon: Mapping[float, pd.DataFrame],
    candidate: WeightCandidate,
    *,
    objective_horizon_weights: Mapping[float, float] = LTF_OBJECTIVE_HORIZON_WEIGHTS,
    evaluation_horizons: Sequence[float] = LTF_EVALUATION_HORIZONS,
    minimum_group_size: int = 10,
    epoch_gap_hours: float = 24.0,
) -> LTFCandidateEvaluation:
    scored = score_ltf_candidate(metric_frame, candidate.weights)
    epoch_map = _natural_epoch_map(scored["scan_ts"], gap_hours=epoch_gap_hours)
    mean_ic: dict[float, float] = {}
    dashboard_excess: dict[float, float] = {}
    rank_reports: dict[float, pd.DataFrame] = {}
    dashboard_reports: dict[float, pd.DataFrame] = {}
    for horizon in evaluation_horizons:
        numeric_horizon = float(horizon)
        forward = forward_by_horizon.get(numeric_horizon)
        if forward is None:
            raise CalibrationGuardError(f"Missing forward frame for {numeric_horizon}h")
        rank_report = cross_section_rank_ic(
            scored,
            forward,
            minimum_group_size=minimum_group_size,
        )
        dashboard_report = exact_dashboard_excess(scored, forward)
        if rank_report.empty:
            raise CalibrationGuardError(f"No measurable rank IC at {numeric_horizon}h")
        if dashboard_report.empty:
            raise CalibrationGuardError(f"No default-dashboard selections at {numeric_horizon}h")
        rank_reports[numeric_horizon] = rank_report
        dashboard_reports[numeric_horizon] = dashboard_report
        mean_ic[numeric_horizon] = float(rank_report["rank_ic"].mean())
        dashboard_excess[numeric_horizon] = float(dashboard_report["excess_ret"].mean())
    objective = weighted_ic_objective(rank_reports, objective_horizon_weights)
    epoch_objective: dict[int, float] = {}
    for epoch_id in sorted(int(value) for value in epoch_map.unique()):
        epoch_timestamps = epoch_map[epoch_map == epoch_id].index
        value = 0.0
        for horizon, weight in objective_horizon_weights.items():
            report = rank_reports[float(horizon)]
            part = report[report["scan_ts"].isin(epoch_timestamps)]
            if part.empty:
                raise CalibrationGuardError(
                    f"Epoch {epoch_id} has no rank IC at objective horizon {horizon}"
                )
            value += float(weight) * float(part["rank_ic"].mean())
        epoch_objective[epoch_id] = float(value)
    return LTFCandidateEvaluation(
        candidate=candidate,
        mean_ic_by_horizon=mean_ic,
        dashboard_excess_by_horizon=dashboard_excess,
        objective_ic=float(objective),
        epoch_objective=epoch_objective,
        rank_ic_by_horizon=rank_reports,
        dashboard_by_horizon=dashboard_reports,
    )


def paired_active_day_comparison(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    value_column: str,
    timestamp_column: str = "scan_ts",
    bootstrap_draws: int = 5000,
    seed: int = 20260903,
) -> dict[str, object]:
    if bootstrap_draws <= 0:
        raise CalibrationGuardError("bootstrap_draws must be positive")
    for label, frame in (("baseline", baseline), ("candidate", candidate)):
        if timestamp_column not in frame.columns or value_column not in frame.columns:
            raise CalibrationGuardError(f"{label} comparison frame lacks required columns")

    def daily(frame: pd.DataFrame) -> pd.Series:
        timestamps = pd.to_datetime(frame[timestamp_column], format="ISO8601")
        values = pd.to_numeric(frame[value_column], errors="coerce")
        finite = np.isfinite(values.to_numpy(dtype=float)) & timestamps.notna().to_numpy()
        clean = pd.DataFrame({"date": timestamps[finite].dt.date, "value": values[finite]})
        return clean.groupby("date")["value"].mean().sort_index()

    baseline_daily = daily(baseline)
    candidate_daily = daily(candidate)
    paired = pd.concat(
        [baseline_daily.rename("baseline"), candidate_daily.rename("candidate")],
        axis=1,
        join="inner",
    ).dropna()
    if paired.empty:
        raise CalibrationGuardError("No common active days for paired comparison")
    delta = (paired["candidate"] - paired["baseline"]).to_numpy(dtype=float)
    rng = np.random.default_rng(int(seed) + len(delta))
    draws = rng.choice(delta, size=(int(bootstrap_draws), len(delta)), replace=True).mean(axis=1)
    return {
        "common_active_days": int(len(paired)),
        "baseline_only_days": int(len(baseline_daily.index.difference(candidate_daily.index))),
        "candidate_only_days": int(len(candidate_daily.index.difference(baseline_daily.index))),
        "mean_daily_delta": float(delta.mean()),
        "median_daily_delta": float(np.median(delta)),
        "positive_day_fraction": float((delta > 0).mean()),
        "ci90_low": float(np.quantile(draws, 0.05)),
        "ci90_high": float(np.quantile(draws, 0.95)),
        "ci95_low": float(np.quantile(draws, 0.025)),
        "ci95_high": float(np.quantile(draws, 0.975)),
        "bootstrap_draws": int(bootstrap_draws),
        "seed": int(seed),
    }


def training_gate_decision(
    baseline: LTFCandidateEvaluation,
    candidate: LTFCandidateEvaluation,
    *,
    primary_horizon: float = LTF_PRIMARY_HORIZON,
    gate: TrainingGate = TrainingGate(),
) -> CandidateGateDecision:
    if candidate.candidate.candidate_id == "baseline":
        raise CalibrationGuardError("Baseline cannot be evaluated as its own challenger")
    if set(candidate.epoch_objective) != set(baseline.epoch_objective):
        raise CalibrationGuardError("Candidate and baseline epoch sets differ")
    objective_delta = float(candidate.objective_ic - baseline.objective_ic)
    dashboard_delta = float(
        candidate.dashboard_excess_by_horizon[float(primary_horizon)]
        - baseline.dashboard_excess_by_horizon[float(primary_horizon)]
    )
    reasons = []
    if objective_delta < gate.objective_delta_minimum:
        reasons.append("objective_delta_below_minimum")
    if candidate.mean_ic_by_horizon[float(primary_horizon)] < gate.primary_ic_minimum:
        reasons.append("primary_ic_below_minimum")
    for epoch_id, baseline_value in baseline.epoch_objective.items():
        if candidate.epoch_objective[epoch_id] < baseline_value - gate.maximum_epoch_objective_shortfall:
            reasons.append(f"epoch_{epoch_id}_objective_shortfall")
    if dashboard_delta < gate.primary_dashboard_excess_delta_minimum:
        reasons.append("primary_dashboard_excess_delta_below_minimum")
    baseline_weights = baseline.candidate.weights
    if tuple(candidate.candidate.weights) != tuple(baseline_weights):
        raise CalibrationGuardError("Candidate and baseline factor sets differ")
    l1 = sum(
        abs(float(candidate.candidate.weights[key]) - float(baseline_weights[key]))
        for key in baseline_weights
    )
    return CandidateGateDecision(
        candidate_id=candidate.candidate.candidate_id,
        passed=not reasons,
        reasons=tuple(reasons),
        objective_delta=objective_delta,
        primary_dashboard_excess_delta=dashboard_delta,
        worst_epoch_objective=float(min(candidate.epoch_objective.values())),
        l1_distance=float(l1),
    )


def select_training_winner(
    evaluations: Sequence[LTFCandidateEvaluation],
    *,
    primary_horizon: float = LTF_PRIMARY_HORIZON,
    gate: TrainingGate = TrainingGate(),
) -> tuple[tuple[CandidateGateDecision, ...], LTFCandidateEvaluation | None]:
    baselines = [item for item in evaluations if item.candidate.candidate_id == "baseline"]
    if len(baselines) != 1:
        raise CalibrationGuardError("Training selection requires exactly one baseline")
    baseline = baselines[0]
    indexed = {item.candidate.candidate_id: (index, item) for index, item in enumerate(evaluations)}
    if len(indexed) != len(evaluations):
        raise CalibrationGuardError("Training evaluations contain duplicate candidate ids")
    decisions = tuple(
        training_gate_decision(baseline, item, primary_horizon=primary_horizon, gate=gate)
        for item in evaluations
        if item.candidate.candidate_id != "baseline"
    )
    passing = [decision for decision in decisions if decision.passed]
    if not passing:
        return decisions, None
    passing.sort(
        key=lambda decision: (
            -decision.worst_epoch_objective,
            -indexed[decision.candidate_id][1].objective_ic,
            decision.l1_distance,
            indexed[decision.candidate_id][0],
        )
    )
    return decisions, indexed[passing[0].candidate_id][1]


def run_training_stage(
    protocol: Mapping[str, object],
    metric_frame: pd.DataFrame,
) -> TrainingStageResult:
    partition = resolve_partition(protocol, "training", purpose="candidate_selection")
    training = _assert_frame_inside_partition(metric_frame, partition, label="training metric frame")
    if training.duplicated(["scan_ts", "symbol"]).any():
        raise CalibrationGuardError("Training frame contains duplicate (scan_ts, symbol) rows")
    expected_metric = partition.get("metric")
    if isinstance(expected_metric, Mapping):
        expected_rows = int(expected_metric.get("rows", -1))
        expected_scans = int(expected_metric.get("scans", -1))
        if len(training) != expected_rows or training["scan_ts"].nunique() != expected_scans:
            raise CalibrationGuardError("Training frame does not match locked row/scan counts")
    ranking = protocol["ranking_experiments"]
    experiment = ranking["ltf"]
    rules = ranking["candidate_generation"]
    baseline_weights = validate_weight_vector(
        experiment["baseline_weights"], experiment["bounds"]
    )
    candidates = generate_single_transfer_candidates(
        baseline_weights,
        experiment["bounds"],
        maximum_l1_distance=float(rules["maximum_l1_distance_from_baseline"]),
        maximum_candidates=int(rules["maximum_candidates_per_experiment_including_baseline"]),
    )
    parity = verify_baseline_score_parity(training, baseline_weights)
    prices = training[["scan_ts", "symbol", "price"]]
    forward_by_horizon = {
        horizon: build_protocol_partition_forward_returns(
            prices,
            horizon,
            protocol,
            "training",
            purpose="candidate_selection",
        )
        for horizon in LTF_EVALUATION_HORIZONS
    }
    evaluations = tuple(
        evaluate_ltf_candidate_metrics(training, forward_by_horizon, candidate)
        for candidate in candidates
    )
    decisions, winner = select_training_winner(evaluations)
    return TrainingStageResult(
        partition_name="training",
        evaluated=evaluations,
        decisions=decisions,
        winner=winner,
        baseline_max_abs_error=parity,
        candidate_count=len(candidates),
    )


def build_training_comparison_report(
    result: TrainingStageResult,
    *,
    bootstrap_draws: int = 5000,
    seed: int = 20260903,
) -> dict[str, object]:
    if result.partition_name != "training":
        raise CalibrationGuardError("Training report requires a training-stage result")
    baselines = [
        item for item in result.evaluated if item.candidate.candidate_id == "baseline"
    ]
    if len(baselines) != 1:
        raise CalibrationGuardError("Training report requires exactly one baseline")
    baseline = baselines[0]
    decisions = {decision.candidate_id: decision for decision in result.decisions}
    challengers = []
    for evaluation in result.evaluated:
        if evaluation.candidate.candidate_id == "baseline":
            continue
        decision = decisions.get(evaluation.candidate.candidate_id)
        if decision is None:
            raise CalibrationGuardError("Training result is missing a candidate gate decision")
        rank_paired = {}
        dashboard_paired = {}
        for horizon in LTF_EVALUATION_HORIZONS:
            rank_paired[str(horizon)] = paired_active_day_comparison(
                baseline.rank_ic_by_horizon[horizon],
                evaluation.rank_ic_by_horizon[horizon],
                value_column="rank_ic",
                bootstrap_draws=bootstrap_draws,
                seed=seed,
            )
            dashboard_paired[str(horizon)] = paired_active_day_comparison(
                baseline.dashboard_by_horizon[horizon],
                evaluation.dashboard_by_horizon[horizon],
                value_column="excess_ret",
                bootstrap_draws=bootstrap_draws,
                seed=seed,
            )
        challengers.append(
            {
                "candidate_id": evaluation.candidate.candidate_id,
                "weights": dict(evaluation.candidate.weights),
                "passed_training_gate": decision.passed,
                "failure_reasons": list(decision.reasons),
                "objective_ic": evaluation.objective_ic,
                "objective_delta": decision.objective_delta,
                "primary_dashboard_excess_delta": decision.primary_dashboard_excess_delta,
                "worst_epoch_objective": decision.worst_epoch_objective,
                "l1_distance": decision.l1_distance,
                "mean_ic_by_horizon": {
                    str(key): value for key, value in evaluation.mean_ic_by_horizon.items()
                },
                "dashboard_excess_by_horizon": {
                    str(key): value
                    for key, value in evaluation.dashboard_excess_by_horizon.items()
                },
                "epoch_objective": {
                    str(key): value for key, value in evaluation.epoch_objective.items()
                },
                "paired_active_day_rank_ic": rank_paired,
                "paired_active_day_dashboard_excess": dashboard_paired,
            }
        )
    return {
        "stage": "training",
        "candidate_count": result.candidate_count,
        "challenger_count": len(challengers),
        "baseline_max_abs_error": result.baseline_max_abs_error,
        "baseline": {
            "weights": dict(baseline.candidate.weights),
            "objective_ic": baseline.objective_ic,
            "mean_ic_by_horizon": {
                str(key): value for key, value in baseline.mean_ic_by_horizon.items()
            },
            "dashboard_excess_by_horizon": {
                str(key): value for key, value in baseline.dashboard_excess_by_horizon.items()
            },
            "epoch_objective": {
                str(key): value for key, value in baseline.epoch_objective.items()
            },
        },
        "winner_candidate_id": (
            result.winner.candidate.candidate_id if result.winner is not None else None
        ),
        "bootstrap": {
            "unit": "UTC active day",
            "comparison": "candidate minus baseline",
            "draws": int(bootstrap_draws),
            "seed": int(seed),
        },
        "challengers": challengers,
    }
