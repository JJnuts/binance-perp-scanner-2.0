"""Side-effect-free primitives for Alt Screener confluence calibration.

This module has no database or network access and no CLI entry point.  It
scores only caller-supplied frames.  Partition access and forward-label
construction remain behind the guards in :mod:`perpscanner.calibration_ltf`.
The production trigger threshold and veto definitions are deliberately not
part of this evaluator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .calibration_ltf import (
    CalibrationGuardError,
    WeightCandidate,
    build_protocol_partition_forward_returns,
    generate_single_transfer_candidates,
    resolve_partition,
    validate_locked_protocol,
    validate_weight_vector,
    weighted_ic_objective,
)


CONFLUENCE_COMPONENT_COLUMNS = {
    "expansion": "comp_expansion",
    "volume": "comp_volume",
    "oi": "comp_oi",
    "taker": "comp_taker_net",
    "basis": "comp_basis_net",
}
CONFLUENCE_DIRECTIONAL_COMPONENTS = frozenset({"taker", "basis"})
CONFLUENCE_EVALUATION_HORIZONS = (1.0, 4.0, 24.0)
CONFLUENCE_OBJECTIVE_HORIZON_WEIGHTS = {1.0: 0.60, 4.0: 0.40}
CONFLUENCE_MINIMUM_GROUP_SIZE = 6
CONFLUENCE_MINIMUM_CROSS_SECTIONS = 30
CONFLUENCE_TRAINING_OBJECTIVE_DELTA_MINIMUM = 0.01
CONFLUENCE_INTERNAL_VALIDATION_OBJECTIVE_DELTA_MINIMUM = 0.005
CONFLUENCE_BASELINE_WEIGHTS = {
    "expansion": 0.30,
    "volume": 0.25,
    "oi": 0.20,
    "taker": 0.15,
    "basis": 0.10,
}
CONFLUENCE_WEIGHT_BOUNDS = {
    "expansion": (0.05, 0.40),
    "volume": (0.05, 0.40),
    "oi": (0.05, 0.30),
    "taker": (0.05, 0.30),
    "basis": (0.05, 0.25),
}
FROZEN_CONFLUENCE_WINNER_ID = "transfer:expansion->basis:0.10"
FROZEN_CONFLUENCE_WINNER_WEIGHTS = {
    "expansion": 0.20,
    "volume": 0.25,
    "oi": 0.20,
    "taker": 0.15,
    "basis": 0.20,
}


@dataclass(frozen=True)
class ConfluenceCandidateEvaluation:
    candidate: WeightCandidate
    mean_ic_by_horizon: dict[float, float]
    objective_ic: float
    rank_ic_by_horizon: dict[float, pd.DataFrame]
    component_rank_ic_by_horizon: dict[float, dict[str, pd.DataFrame]]


@dataclass(frozen=True)
class ConfluenceTrainingGateDecision:
    candidate_id: str
    passed: bool
    reasons: tuple[str, ...]
    objective_delta: float
    safety_horizon_ic_delta: float
    l1_distance: float


@dataclass(frozen=True)
class ConfluenceTrainingStageResult:
    partition_name: str
    evaluated: tuple[ConfluenceCandidateEvaluation, ...]
    decisions: tuple[ConfluenceTrainingGateDecision, ...]
    winner: ConfluenceCandidateEvaluation | None
    candidate_count: int


@dataclass(frozen=True)
class ConfluenceInternalValidationDecision:
    candidate_id: str
    passed: bool
    reasons: tuple[str, ...]
    objective_delta: float
    safety_horizon_ic_delta: float


@dataclass(frozen=True)
class ConfluenceInternalValidationResult:
    partition_name: str
    baseline: ConfluenceCandidateEvaluation
    candidate: ConfluenceCandidateEvaluation
    decision: ConfluenceInternalValidationDecision


def _expected_weight_keys() -> tuple[str, ...]:
    return tuple(CONFLUENCE_COMPONENT_COLUMNS)


def _validate_scoring_weights(weights: Mapping[str, float]) -> dict[str, float]:
    expected = _expected_weight_keys()
    if tuple(weights) != expected:
        raise CalibrationGuardError(
            f"Unexpected confluence weight keys/order: {tuple(weights)}"
        )
    broad_bounds = {component: (0.0, 1.0) for component in expected}
    return validate_weight_vector(weights, broad_bounds)


def generate_confluence_candidates(
    baseline: Mapping[str, float],
    bounds: Mapping[str, Sequence[float]],
    *,
    transfer_sizes: Iterable[float] = (0.05, 0.10),
    maximum_l1_distance: float = 0.20,
    maximum_candidates: int = 37,
) -> list[WeightCandidate]:
    """Generate the locked baseline-plus-one-transfer candidate grid."""

    expected = _expected_weight_keys()
    if tuple(baseline) != expected or tuple(bounds) != expected:
        raise CalibrationGuardError(
            "Unexpected confluence baseline/bound keys or order: "
            f"{tuple(baseline)} / {tuple(bounds)}"
        )
    return generate_single_transfer_candidates(
        baseline,
        bounds,
        transfer_sizes=transfer_sizes,
        maximum_l1_distance=maximum_l1_distance,
        maximum_candidates=maximum_candidates,
    )


def directionalize_confluence_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep veto-active rows and orient every component to its veto side."""

    required = {"scan_ts", "symbol", "veto_side"}
    required.update(CONFLUENCE_COMPONENT_COLUMNS.values())
    missing = required - set(frame.columns)
    if missing:
        raise CalibrationGuardError(
            f"Confluence scoring frame is missing columns: {sorted(missing)}"
        )
    if frame.empty:
        raise CalibrationGuardError("Confluence scoring frame is empty")

    out = frame.copy()
    out["scan_ts"] = pd.to_datetime(out["scan_ts"], format="ISO8601", errors="coerce")
    if out["scan_ts"].isna().any():
        raise CalibrationGuardError("Confluence scoring frame contains invalid timestamps")
    if out[["scan_ts", "symbol"]].duplicated().any():
        raise CalibrationGuardError(
            "Confluence scoring frame contains duplicate (scan_ts, symbol) rows"
        )
    valid_sides = {"Long", "Short", "None"}
    observed_sides = set(out["veto_side"].dropna().unique())
    if out["veto_side"].isna().any() or not observed_sides.issubset(valid_sides):
        raise CalibrationGuardError("Confluence scoring frame contains an invalid veto side")

    out = out[out["veto_side"].isin(["Long", "Short"])].copy()
    if out.empty:
        raise CalibrationGuardError("Confluence scoring frame has no veto-active rows")
    out["direction_sign"] = np.where(out["veto_side"] == "Long", 1.0, -1.0)

    for component, column in CONFLUENCE_COMPONENT_COLUMNS.items():
        values = pd.to_numeric(out[column], errors="coerce")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise CalibrationGuardError(
                f"Non-finite values in confluence component {component}"
            )
        lower = -1.0 if component in CONFLUENCE_DIRECTIONAL_COMPONENTS else 0.0
        if (values < lower).any() or (values > 1.0).any():
            raise CalibrationGuardError(
                f"Confluence component {component} is outside [{lower}, 1.0]"
            )
        directional = values * out["direction_sign"] if component in CONFLUENCE_DIRECTIONAL_COMPONENTS else values
        out[f"directional_{component}"] = directional.astype(float)
    return out


def score_confluence_candidate(
    frame: pd.DataFrame,
    weights: Mapping[str, float],
) -> pd.DataFrame:
    """Compute a continuous, side-aligned confluence score on active vetoes."""

    checked = _validate_scoring_weights(weights)
    out = directionalize_confluence_frame(frame)
    score = pd.Series(0.0, index=out.index, dtype=float)
    for component, weight in checked.items():
        score = score + weight * out[f"directional_{component}"]
    if not np.isfinite(score.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("Confluence candidate score contains non-finite values")
    out["candidate_score"] = score
    return out


def directional_cross_section_rank_ic(
    scored: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    score_column: str = "candidate_score",
    minimum_group_size: int = CONFLUENCE_MINIMUM_GROUP_SIZE,
) -> pd.DataFrame:
    """Rank a side-aligned score against side-adjusted forward returns."""

    if int(minimum_group_size) < 2:
        raise CalibrationGuardError("Confluence minimum group size must be at least two")
    required_scored = {"scan_ts", "symbol", "veto_side", score_column}
    required_forward = {"scan_ts", "symbol", "fwd_ret"}
    if not required_scored.issubset(scored.columns):
        raise CalibrationGuardError("Scored frame lacks directional rank-IC columns")
    if not required_forward.issubset(forward.columns):
        raise CalibrationGuardError("Forward frame lacks directional rank-IC columns")
    if scored[["scan_ts", "symbol"]].duplicated().any():
        raise CalibrationGuardError("Scored frame contains duplicate keys")
    if forward[["scan_ts", "symbol"]].duplicated().any():
        raise CalibrationGuardError("Forward frame contains duplicate keys")

    merged = scored[list(required_scored)].merge(
        forward[list(required_forward)], on=["scan_ts", "symbol"], how="inner"
    )
    if not set(merged["veto_side"].dropna().unique()).issubset({"Long", "Short"}):
        raise CalibrationGuardError("Directional rank IC received a non-active veto side")
    merged[score_column] = pd.to_numeric(merged[score_column], errors="coerce")
    merged["fwd_ret"] = pd.to_numeric(merged["fwd_ret"], errors="coerce")
    merged = merged.dropna(subset=[score_column, "fwd_ret", "veto_side"])
    merged["directional_ret"] = merged["fwd_ret"] * np.where(
        merged["veto_side"] == "Long", 1.0, -1.0
    )

    rows: list[dict[str, object]] = []
    for scan_ts, group in merged.groupby("scan_ts", sort=True):
        if len(group) < int(minimum_group_size) or group[score_column].nunique() < 3:
            continue
        rank_ic = group[score_column].rank(method="average").corr(
            group["directional_ret"].rank(method="average")
        )
        if pd.notna(rank_ic):
            rows.append(
                {
                    "scan_ts": pd.Timestamp(scan_ts),
                    "rank_ic": float(rank_ic),
                    "symbols": int(len(group)),
                }
            )
    return pd.DataFrame(rows, columns=["scan_ts", "rank_ic", "symbols"])


def component_directional_rank_ic(
    directional_frame: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    minimum_group_size: int = CONFLUENCE_MINIMUM_GROUP_SIZE,
) -> dict[str, pd.DataFrame]:
    """Measure each side-aligned component using identical IC semantics."""

    reports: dict[str, pd.DataFrame] = {}
    for component in CONFLUENCE_COMPONENT_COLUMNS:
        column = f"directional_{component}"
        reports[component] = directional_cross_section_rank_ic(
            directional_frame,
            forward,
            score_column=column,
            minimum_group_size=minimum_group_size,
        )
    return reports


def evaluate_confluence_candidate_metrics(
    frame: pd.DataFrame,
    forward_by_horizon: Mapping[float, pd.DataFrame],
    candidate: WeightCandidate,
    *,
    objective_horizon_weights: Mapping[
        float, float
    ] = CONFLUENCE_OBJECTIVE_HORIZON_WEIGHTS,
    evaluation_horizons: Sequence[float] = CONFLUENCE_EVALUATION_HORIZONS,
    minimum_group_size: int = CONFLUENCE_MINIMUM_GROUP_SIZE,
    minimum_cross_sections: int = CONFLUENCE_MINIMUM_CROSS_SECTIONS,
) -> ConfluenceCandidateEvaluation:
    """Evaluate one caller-supplied candidate and enforce component support."""

    if int(minimum_cross_sections) <= 0:
        raise CalibrationGuardError("Minimum confluence cross-sections must be positive")
    directional = directionalize_confluence_frame(frame)
    component_reports = _build_component_reports_by_horizon(
        directional,
        forward_by_horizon,
        evaluation_horizons=evaluation_horizons,
        minimum_group_size=minimum_group_size,
        minimum_cross_sections=minimum_cross_sections,
    )
    return _evaluate_confluence_candidate_with_component_reports(
        frame,
        forward_by_horizon,
        candidate,
        component_reports,
        objective_horizon_weights=objective_horizon_weights,
        evaluation_horizons=evaluation_horizons,
        minimum_group_size=minimum_group_size,
        minimum_cross_sections=minimum_cross_sections,
    )


def _build_component_reports_by_horizon(
    directional_frame: pd.DataFrame,
    forward_by_horizon: Mapping[float, pd.DataFrame],
    *,
    evaluation_horizons: Sequence[float],
    minimum_group_size: int,
    minimum_cross_sections: int,
) -> dict[float, dict[str, pd.DataFrame]]:
    reports_by_horizon: dict[float, dict[str, pd.DataFrame]] = {}
    for horizon in evaluation_horizons:
        numeric_horizon = float(horizon)
        forward = forward_by_horizon.get(numeric_horizon)
        if forward is None:
            raise CalibrationGuardError(
                f"Missing confluence forward frame for {numeric_horizon}h"
            )
        per_component = component_directional_rank_ic(
            directional_frame, forward, minimum_group_size=minimum_group_size
        )
        for component, report in per_component.items():
            if len(report) < int(minimum_cross_sections):
                raise CalibrationGuardError(
                    f"Confluence component {component} has {len(report)} measurable "
                    f"cross-sections at {numeric_horizon}h; "
                    f"requires {int(minimum_cross_sections)}"
                )
        reports_by_horizon[numeric_horizon] = per_component
    return reports_by_horizon


def _evaluate_confluence_candidate_with_component_reports(
    frame: pd.DataFrame,
    forward_by_horizon: Mapping[float, pd.DataFrame],
    candidate: WeightCandidate,
    component_reports: Mapping[float, dict[str, pd.DataFrame]],
    *,
    objective_horizon_weights: Mapping[float, float],
    evaluation_horizons: Sequence[float],
    minimum_group_size: int,
    minimum_cross_sections: int,
) -> ConfluenceCandidateEvaluation:
    scored = score_confluence_candidate(frame, candidate.weights)
    mean_ic: dict[float, float] = {}
    rank_reports: dict[float, pd.DataFrame] = {}
    for horizon in evaluation_horizons:
        numeric_horizon = float(horizon)
        forward = forward_by_horizon.get(numeric_horizon)
        if forward is None or numeric_horizon not in component_reports:
            raise CalibrationGuardError(
                f"Missing prevalidated confluence reports for {numeric_horizon}h"
            )
        report = directional_cross_section_rank_ic(
            scored, forward, minimum_group_size=minimum_group_size
        )
        if len(report) < int(minimum_cross_sections):
            raise CalibrationGuardError(
                f"Confluence candidate has {len(report)} measurable cross-sections "
                f"at {numeric_horizon}h; requires {int(minimum_cross_sections)}"
            )
        value = float(report["rank_ic"].mean())
        if not math.isfinite(value):
            raise CalibrationGuardError(
                f"Confluence candidate has non-finite mean IC at {numeric_horizon}h"
            )
        rank_reports[numeric_horizon] = report
        mean_ic[numeric_horizon] = value

    objective = weighted_ic_objective(rank_reports, objective_horizon_weights)
    return ConfluenceCandidateEvaluation(
        candidate=candidate,
        mean_ic_by_horizon=mean_ic,
        objective_ic=float(objective),
        rank_ic_by_horizon=rank_reports,
        component_rank_ic_by_horizon=component_reports,
    )


def confluence_training_gate_decision(
    baseline: ConfluenceCandidateEvaluation,
    candidate: ConfluenceCandidateEvaluation,
    *,
    objective_delta_minimum: float = CONFLUENCE_TRAINING_OBJECTIVE_DELTA_MINIMUM,
    safety_horizon: float = 24.0,
) -> ConfluenceTrainingGateDecision:
    """Apply only the training gate explicitly locked for confluence v1."""

    if candidate.candidate.candidate_id == "baseline":
        raise CalibrationGuardError("Baseline cannot be its own confluence challenger")
    minimum = float(objective_delta_minimum)
    if not math.isfinite(minimum):
        raise CalibrationGuardError("Confluence training objective gate must be finite")
    horizon = float(safety_horizon)
    if horizon not in baseline.mean_ic_by_horizon or horizon not in candidate.mean_ic_by_horizon:
        raise CalibrationGuardError("Confluence safety-horizon IC is missing")
    if tuple(candidate.candidate.weights) != tuple(baseline.candidate.weights):
        raise CalibrationGuardError("Confluence candidate and baseline factors differ")

    objective_delta = float(candidate.objective_ic - baseline.objective_ic)
    safety_delta = float(
        candidate.mean_ic_by_horizon[horizon] - baseline.mean_ic_by_horizon[horizon]
    )
    if not math.isfinite(objective_delta) or not math.isfinite(safety_delta):
        raise CalibrationGuardError("Confluence training deltas must be finite")
    l1_distance = sum(
        abs(
            float(candidate.candidate.weights[component])
            - float(baseline.candidate.weights[component])
        )
        for component in baseline.candidate.weights
    )
    reasons = () if objective_delta >= minimum else ("objective_delta_below_minimum",)
    return ConfluenceTrainingGateDecision(
        candidate_id=candidate.candidate.candidate_id,
        passed=not reasons,
        reasons=reasons,
        objective_delta=objective_delta,
        safety_horizon_ic_delta=safety_delta,
        l1_distance=float(l1_distance),
    )


def select_confluence_training_winner(
    evaluations: Sequence[ConfluenceCandidateEvaluation],
    *,
    objective_delta_minimum: float = CONFLUENCE_TRAINING_OBJECTIVE_DELTA_MINIMUM,
) -> tuple[
    tuple[ConfluenceTrainingGateDecision, ...],
    ConfluenceCandidateEvaluation | None,
]:
    """Return at most one deterministic training winner."""

    baselines = [item for item in evaluations if item.candidate.candidate_id == "baseline"]
    if len(baselines) != 1:
        raise CalibrationGuardError(
            "Confluence training selection requires exactly one baseline"
        )
    indexed = {
        item.candidate.candidate_id: (index, item)
        for index, item in enumerate(evaluations)
    }
    if len(indexed) != len(evaluations):
        raise CalibrationGuardError(
            "Confluence training evaluations contain duplicate candidate IDs"
        )
    baseline = baselines[0]
    decisions = tuple(
        confluence_training_gate_decision(
            baseline,
            item,
            objective_delta_minimum=objective_delta_minimum,
        )
        for item in evaluations
        if item.candidate.candidate_id != "baseline"
    )
    passing = [decision for decision in decisions if decision.passed]
    if not passing:
        return decisions, None
    passing.sort(
        key=lambda decision: (
            -indexed[decision.candidate_id][1].objective_ic,
            decision.l1_distance,
            indexed[decision.candidate_id][0],
        )
    )
    return decisions, indexed[passing[0].candidate_id][1]


def _utc_naive(value: object, label: str) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise CalibrationGuardError(f"Invalid confluence {label}: {value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed


def _assert_confluence_partition_frame(
    frame: pd.DataFrame,
    partition: Mapping[str, object],
    *,
    label: str,
) -> pd.DataFrame:
    required = {"scan_ts", "symbol", "veto_side", "price"}
    if frame.empty or not required.issubset(frame.columns):
        raise CalibrationGuardError(
            f"Confluence {label} frame is empty or lacks required identity columns"
        )
    start = _utc_naive(partition.get("start_inclusive_utc"), f"{label} start")
    end = _utc_naive(partition.get("end_inclusive_utc"), f"{label} end")
    timestamps = pd.to_datetime(frame["scan_ts"], format="ISO8601", errors="coerce")
    if timestamps.isna().any() or (timestamps < start).any() or (timestamps > end).any():
        raise CalibrationGuardError(
            f"Confluence {label} frame contains rows outside {label}"
        )
    out = frame.copy()
    out["scan_ts"] = timestamps
    if out[["scan_ts", "symbol"]].duplicated().any():
        raise CalibrationGuardError(
            "Confluence training frame contains duplicate (scan_ts, symbol) rows"
        )
    return out


def _assert_confluence_training_frame(
    frame: pd.DataFrame,
    partition: Mapping[str, object],
) -> pd.DataFrame:
    return _assert_confluence_partition_frame(
        frame,
        partition,
        label="training",
    )


def _locked_confluence_experiment(
    protocol: Mapping[str, object],
) -> Mapping[str, object]:
    validate_locked_protocol(protocol)
    experiment = protocol.get("confluence_experiment")
    if not isinstance(experiment, Mapping) or experiment.get("experiment_id") != "alt-confluence-v1":
        raise CalibrationGuardError("Protocol has no locked confluence experiment")

    if tuple(experiment.get("baseline_weights", {})) != _expected_weight_keys():
        raise CalibrationGuardError("Locked confluence baseline factors/order changed")
    baseline = validate_weight_vector(
        experiment["baseline_weights"], experiment.get("bounds", {})
    )
    if baseline != CONFLUENCE_BASELINE_WEIGHTS:
        raise CalibrationGuardError("Locked confluence baseline weights changed")
    locked_bounds = {
        component: tuple(float(value) for value in interval)
        for component, interval in experiment["bounds"].items()
    }
    if locked_bounds != CONFLUENCE_WEIGHT_BOUNDS:
        raise CalibrationGuardError("Locked confluence weight bounds changed")
    if float(experiment.get("safety_horizon_hours", float("nan"))) != 24.0:
        raise CalibrationGuardError("Locked confluence safety horizon changed")
    if int(
        experiment.get(
            "minimum_cross_sections_per_component_per_horizon_per_partition", -1
        )
    ) != CONFLUENCE_MINIMUM_CROSS_SECTIONS:
        raise CalibrationGuardError("Locked confluence support minimum changed")
    if float(
        experiment.get("training_objective_delta_minimum", float("nan"))
    ) != CONFLUENCE_TRAINING_OBJECTIVE_DELTA_MINIMUM:
        raise CalibrationGuardError("Locked confluence training gate changed")
    if float(
        experiment.get("internal_validation_objective_delta_minimum", float("nan"))
    ) != CONFLUENCE_INTERNAL_VALIDATION_OBJECTIVE_DELTA_MINIMUM:
        raise CalibrationGuardError("Locked confluence validation gate changed")
    if experiment.get("native_20260903_suggestion_eligible") is not False:
        raise CalibrationGuardError("Rejected native confluence suggestion became eligible")
    return experiment


def run_confluence_training_stage(
    protocol: Mapping[str, object],
    ltf_frame: pd.DataFrame,
) -> ConfluenceTrainingStageResult:
    """Run confluence v1 on an explicitly supplied, locked training frame."""

    partition = resolve_partition(protocol, "training", purpose="candidate_selection")
    training = _assert_confluence_training_frame(ltf_frame, partition)
    expected_ltf = partition.get("ltf")
    if not isinstance(expected_ltf, Mapping):
        raise CalibrationGuardError("Protocol has no locked LTF training counts")
    observed = {
        "rows": len(training),
        "scans": training["scan_ts"].nunique(),
        "symbols": training["symbol"].nunique(),
        "veto_rows": int(training["veto_side"].isin(["Long", "Short"]).sum()),
    }
    for key, value in observed.items():
        if key not in expected_ltf or int(expected_ltf[key]) != int(value):
            raise CalibrationGuardError(
                f"Confluence training frame does not match locked {key} count"
            )
    if partition.get("confluence_fields_complete") is not True:
        raise CalibrationGuardError("Locked training partition lacks confluence completeness")

    experiment = _locked_confluence_experiment(protocol)
    candidates = generate_confluence_candidates(
        experiment["baseline_weights"],
        experiment["bounds"],
    )
    if len(candidates) != 37:
        raise CalibrationGuardError("Locked confluence candidate count is not 37")

    isolation = protocol.get("partition_isolation")
    if not isinstance(isolation, Mapping):
        raise CalibrationGuardError("Protocol has no partition-isolation rules")
    locked_horizons = tuple(
        float(value) for value in isolation.get("forward_horizons_hours", ())
    )
    if locked_horizons != CONFLUENCE_EVALUATION_HORIZONS:
        raise CalibrationGuardError("Locked confluence forward horizons changed")
    tolerance_fraction = float(
        isolation.get("forward_join_tolerance_fraction", float("nan"))
    )
    if not math.isfinite(tolerance_fraction) or tolerance_fraction < 0.0:
        raise CalibrationGuardError("Invalid confluence forward-label tolerance")
    prices = training[["scan_ts", "symbol", "price"]]
    forward_by_horizon = {
        horizon: build_protocol_partition_forward_returns(
            prices,
            horizon,
            protocol,
            "training",
            purpose="candidate_selection",
            tolerance_fraction=tolerance_fraction,
        )
        for horizon in CONFLUENCE_EVALUATION_HORIZONS
    }
    directional = directionalize_confluence_frame(training)
    component_reports = _build_component_reports_by_horizon(
        directional,
        forward_by_horizon,
        evaluation_horizons=CONFLUENCE_EVALUATION_HORIZONS,
        minimum_group_size=CONFLUENCE_MINIMUM_GROUP_SIZE,
        minimum_cross_sections=CONFLUENCE_MINIMUM_CROSS_SECTIONS,
    )
    evaluations = tuple(
        _evaluate_confluence_candidate_with_component_reports(
            training,
            forward_by_horizon,
            candidate,
            component_reports,
            objective_horizon_weights=CONFLUENCE_OBJECTIVE_HORIZON_WEIGHTS,
            evaluation_horizons=CONFLUENCE_EVALUATION_HORIZONS,
            minimum_group_size=CONFLUENCE_MINIMUM_GROUP_SIZE,
            minimum_cross_sections=CONFLUENCE_MINIMUM_CROSS_SECTIONS,
        )
        for candidate in candidates
    )
    decisions, winner = select_confluence_training_winner(
        evaluations,
        objective_delta_minimum=float(
            experiment["training_objective_delta_minimum"]
        ),
    )
    return ConfluenceTrainingStageResult(
        partition_name="training",
        evaluated=evaluations,
        decisions=decisions,
        winner=winner,
        candidate_count=len(candidates),
    )


def confluence_internal_validation_decision(
    baseline: ConfluenceCandidateEvaluation,
    candidate: ConfluenceCandidateEvaluation,
    *,
    objective_delta_minimum: float = (
        CONFLUENCE_INTERNAL_VALIDATION_OBJECTIVE_DELTA_MINIMUM
    ),
    safety_horizon: float = 24.0,
) -> ConfluenceInternalValidationDecision:
    """Apply the sole numeric gate locked for confluence validation."""

    if candidate.candidate.candidate_id == "baseline":
        raise CalibrationGuardError("Baseline cannot validate itself")
    minimum = float(objective_delta_minimum)
    if not math.isfinite(minimum):
        raise CalibrationGuardError("Confluence validation objective gate must be finite")
    horizon = float(safety_horizon)
    if horizon not in baseline.mean_ic_by_horizon or horizon not in candidate.mean_ic_by_horizon:
        raise CalibrationGuardError("Confluence validation safety IC is missing")
    objective_delta = float(candidate.objective_ic - baseline.objective_ic)
    safety_delta = float(
        candidate.mean_ic_by_horizon[horizon] - baseline.mean_ic_by_horizon[horizon]
    )
    if not math.isfinite(objective_delta) or not math.isfinite(safety_delta):
        raise CalibrationGuardError("Confluence validation deltas must be finite")
    reasons = () if objective_delta >= minimum else ("objective_delta_below_minimum",)
    return ConfluenceInternalValidationDecision(
        candidate_id=candidate.candidate.candidate_id,
        passed=not reasons,
        reasons=reasons,
        objective_delta=objective_delta,
        safety_horizon_ic_delta=safety_delta,
    )


def _validate_frozen_confluence_winner(
    candidate: WeightCandidate,
    experiment: Mapping[str, object],
) -> WeightCandidate:
    generated = {
        item.candidate_id: item
        for item in generate_confluence_candidates(
            experiment["baseline_weights"],
            experiment["bounds"],
        )
    }
    expected = generated.get(FROZEN_CONFLUENCE_WINNER_ID)
    if expected is None or expected.weights != FROZEN_CONFLUENCE_WINNER_WEIGHTS:
        raise CalibrationGuardError("Frozen confluence winner is absent from locked grid")
    if candidate != expected:
        raise CalibrationGuardError("Validation candidate is not the frozen training winner")
    return expected


def run_confluence_internal_validation_stage(
    protocol: Mapping[str, object],
    ltf_frame: pd.DataFrame,
    candidate: WeightCandidate,
) -> ConfluenceInternalValidationResult:
    """Evaluate only baseline and the frozen winner on internal validation."""

    partition = resolve_partition(
        protocol,
        "internal_validation",
        purpose="internal_validation",
    )
    validation = _assert_confluence_partition_frame(
        ltf_frame,
        partition,
        label="internal validation",
    )
    expected_ltf = partition.get("ltf")
    if not isinstance(expected_ltf, Mapping):
        raise CalibrationGuardError("Protocol has no locked validation LTF counts")
    observed = {
        "rows": len(validation),
        "scans": validation["scan_ts"].nunique(),
        "symbols": validation["symbol"].nunique(),
        "veto_rows": int(validation["veto_side"].isin(["Long", "Short"]).sum()),
    }
    for key, value in observed.items():
        if key not in expected_ltf or int(expected_ltf[key]) != int(value):
            raise CalibrationGuardError(
                f"Confluence validation frame does not match locked {key} count"
            )
    if partition.get("confluence_fields_complete") is not True:
        raise CalibrationGuardError(
            "Locked validation partition lacks confluence completeness"
        )

    experiment = _locked_confluence_experiment(protocol)
    frozen_candidate = _validate_frozen_confluence_winner(candidate, experiment)
    baseline_candidate = WeightCandidate(
        "baseline",
        dict(experiment["baseline_weights"]),
        None,
        None,
        0.0,
    )
    isolation = protocol.get("partition_isolation")
    if not isinstance(isolation, Mapping):
        raise CalibrationGuardError("Protocol has no partition-isolation rules")
    locked_horizons = tuple(
        float(value) for value in isolation.get("forward_horizons_hours", ())
    )
    if locked_horizons != CONFLUENCE_EVALUATION_HORIZONS:
        raise CalibrationGuardError("Locked confluence forward horizons changed")
    tolerance_fraction = float(
        isolation.get("forward_join_tolerance_fraction", float("nan"))
    )
    if not math.isfinite(tolerance_fraction) or tolerance_fraction < 0.0:
        raise CalibrationGuardError("Invalid confluence forward-label tolerance")
    prices = validation[["scan_ts", "symbol", "price"]]
    forward_by_horizon = {
        horizon: build_protocol_partition_forward_returns(
            prices,
            horizon,
            protocol,
            "internal_validation",
            purpose="internal_validation",
            tolerance_fraction=tolerance_fraction,
        )
        for horizon in CONFLUENCE_EVALUATION_HORIZONS
    }
    directional = directionalize_confluence_frame(validation)
    component_reports = _build_component_reports_by_horizon(
        directional,
        forward_by_horizon,
        evaluation_horizons=CONFLUENCE_EVALUATION_HORIZONS,
        minimum_group_size=CONFLUENCE_MINIMUM_GROUP_SIZE,
        minimum_cross_sections=CONFLUENCE_MINIMUM_CROSS_SECTIONS,
    )
    baseline = _evaluate_confluence_candidate_with_component_reports(
        validation,
        forward_by_horizon,
        baseline_candidate,
        component_reports,
        objective_horizon_weights=CONFLUENCE_OBJECTIVE_HORIZON_WEIGHTS,
        evaluation_horizons=CONFLUENCE_EVALUATION_HORIZONS,
        minimum_group_size=CONFLUENCE_MINIMUM_GROUP_SIZE,
        minimum_cross_sections=CONFLUENCE_MINIMUM_CROSS_SECTIONS,
    )
    evaluated_candidate = _evaluate_confluence_candidate_with_component_reports(
        validation,
        forward_by_horizon,
        frozen_candidate,
        component_reports,
        objective_horizon_weights=CONFLUENCE_OBJECTIVE_HORIZON_WEIGHTS,
        evaluation_horizons=CONFLUENCE_EVALUATION_HORIZONS,
        minimum_group_size=CONFLUENCE_MINIMUM_GROUP_SIZE,
        minimum_cross_sections=CONFLUENCE_MINIMUM_CROSS_SECTIONS,
    )
    decision = confluence_internal_validation_decision(
        baseline,
        evaluated_candidate,
        objective_delta_minimum=float(
            experiment["internal_validation_objective_delta_minimum"]
        ),
    )
    return ConfluenceInternalValidationResult(
        partition_name="internal_validation",
        baseline=baseline,
        candidate=evaluated_candidate,
        decision=decision,
    )
