"""Deterministic, side-effect-free primitives for Alt Screener HTF calibration.

This module contains no database or network access and no CLI entry point.  It
only scores caller-supplied frames and evaluates caller-supplied forward labels.
Partitioned data access remains behind the guards in ``calibration_ltf``.
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
    cross_section_rank_ic,
    exact_dashboard_excess,
    generate_single_transfer_candidates,
    paired_active_day_comparison,
    resolve_partition,
    validate_locked_protocol,
    validate_weight_vector,
    weighted_ic_objective,
)


HTF_FACTOR_COLUMNS = {
    "alpha": "htf_alpha_score",
    "vol_adjusted": "vol_adjusted_score",
    "relative_strength": "htf_relative_strength_score",
    "trend": "htf_trend_score",
    "oi": "oi_score",
    "funding_trend_quality": "funding_trend_quality_score",
}
HTF_CANDIDATE_RAW_COLUMNS = {
    "premium_level_candidate": "premium_bp",
    "premium_roc_candidate": "premium_roc_bp_h",
}
HTF_EVALUATION_HORIZONS = (1.0, 4.0, 24.0)
HTF_OBJECTIVE_HORIZON_WEIGHTS = {24.0: 0.70, 4.0: 0.30}
HTF_PRIMARY_HORIZON = 24.0
HTF_SETUP_OVEREXTENSION_PENALTY = 0.40


@dataclass(frozen=True)
class HTFParityResult:
    momentum_max_abs_error: float
    setup_max_abs_error: float


@dataclass(frozen=True)
class HTFCandidateEvaluation:
    candidate: WeightCandidate
    mean_ic_by_horizon: dict[float, float]
    dashboard_excess_by_horizon: dict[float, float]
    objective_ic: float
    epoch_objective: dict[int, float]
    rank_ic_by_horizon: dict[float, pd.DataFrame]
    dashboard_by_horizon: dict[float, pd.DataFrame]


@dataclass(frozen=True)
class HTFTrainingGate:
    objective_delta_minimum: float = 0.005
    primary_ic_minimum: float = 0.0
    maximum_epoch_objective_shortfall: float = 0.0025
    primary_dashboard_excess_delta_minimum: float = 0.0005


@dataclass(frozen=True)
class HTFCandidateGateDecision:
    candidate_id: str
    passed: bool
    reasons: tuple[str, ...]
    objective_delta: float
    primary_dashboard_excess_delta: float
    worst_epoch_objective: float
    l1_distance: float


@dataclass(frozen=True)
class HTFTrainingStageResult:
    partition_name: str
    evaluated: tuple[HTFCandidateEvaluation, ...]
    decisions: tuple[HTFCandidateGateDecision, ...]
    winner: HTFCandidateEvaluation | None
    baseline_parity: HTFParityResult
    candidate_count: int


def _expected_weight_keys() -> tuple[str, ...]:
    return tuple(HTF_FACTOR_COLUMNS) + tuple(HTF_CANDIDATE_RAW_COLUMNS)


def generate_htf_candidates(
    baseline: Mapping[str, float],
    bounds: Mapping[str, Sequence[float]],
    *,
    transfer_sizes: Iterable[float] = (0.05, 0.10, 0.15),
    maximum_l1_distance: float = 0.30,
    maximum_candidates: int = 127,
) -> list[WeightCandidate]:
    expected = _expected_weight_keys()
    if tuple(baseline) != expected or tuple(bounds) != expected:
        raise CalibrationGuardError(
            f"Unexpected HTF weight keys/order: {tuple(baseline)} / {tuple(bounds)}"
        )
    return generate_single_transfer_candidates(
        baseline,
        bounds,
        transfer_sizes=transfer_sizes,
        maximum_l1_distance=maximum_l1_distance,
        maximum_candidates=maximum_candidates,
    )


def score_htf_candidate(
    frame: pd.DataFrame,
    weights: Mapping[str, float],
    *,
    overextension_penalty: float = HTF_SETUP_OVEREXTENSION_PENALTY,
) -> pd.DataFrame:
    expected = _expected_weight_keys()
    if tuple(weights) != expected:
        raise CalibrationGuardError(f"Unexpected HTF weight keys/order: {tuple(weights)}")
    penalty = float(overextension_penalty)
    if not math.isfinite(penalty) or penalty < 0.0:
        raise CalibrationGuardError("HTF overextension penalty must be finite and nonnegative")

    required = {"scan_ts", "symbol", "overextension_score"}
    required.update(HTF_FACTOR_COLUMNS.values())
    for factor, raw_column in HTF_CANDIDATE_RAW_COLUMNS.items():
        if float(weights[factor]) > 0.0:
            required.add(raw_column)
    missing = required - set(frame.columns)
    if missing:
        raise CalibrationGuardError(
            f"HTF scoring frame is missing columns: {sorted(missing)}"
        )

    out = frame.copy()
    out["scan_ts"] = pd.to_datetime(out["scan_ts"], format="ISO8601")
    if out["scan_ts"].isna().any():
        raise CalibrationGuardError("HTF scoring frame contains invalid scan timestamps")
    score = pd.Series(0.0, index=out.index)
    for factor, column in HTF_FACTOR_COLUMNS.items():
        values = pd.to_numeric(out[column], errors="coerce")
        if float(weights[factor]) > 0.0 and not np.isfinite(
            values.to_numpy(dtype=float)
        ).all():
            raise CalibrationGuardError(f"Non-finite values in active HTF factor {factor}")
        score = score + float(weights[factor]) * values.fillna(0.0)
    for factor, raw_column in HTF_CANDIDATE_RAW_COLUMNS.items():
        weight = float(weights[factor])
        if weight <= 0.0:
            continue
        raw = pd.to_numeric(out[raw_column], errors="coerce")
        if not np.isfinite(raw.to_numpy(dtype=float)).all():
            raise CalibrationGuardError(f"Non-finite values in active HTF candidate {factor}")
        transformed = raw.groupby(out["scan_ts"]).rank(method="average", pct=True) * 100.0
        score = score + weight * transformed

    if not np.isfinite(score.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("HTF candidate score contains non-finite values")
    overextension = pd.to_numeric(out["overextension_score"], errors="coerce")
    if not np.isfinite(overextension.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("Non-finite overextension_score")
    out["candidate_score"] = score
    out["candidate_setup_score"] = (score - penalty * overextension).clip(lower=0.0)
    return out


def verify_htf_baseline_score_parity(
    frame: pd.DataFrame,
    baseline_weights: Mapping[str, float],
    *,
    stored_score_column: str = "htf_momentum_score",
    stored_setup_column: str = "htf_setup_score",
    absolute_tolerance: float = 1e-9,
) -> HTFParityResult:
    missing = {stored_score_column, stored_setup_column} - set(frame.columns)
    if missing:
        raise CalibrationGuardError(f"Missing stored HTF baseline columns: {sorted(missing)}")
    tolerance = float(absolute_tolerance)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise CalibrationGuardError("HTF parity tolerance must be finite and nonnegative")

    scored = score_htf_candidate(frame, baseline_weights)
    stored_score = pd.to_numeric(scored[stored_score_column], errors="coerce")
    stored_setup = pd.to_numeric(scored[stored_setup_column], errors="coerce")
    if not np.isfinite(stored_score.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("Stored HTF baseline score contains non-finite values")
    if not np.isfinite(stored_setup.to_numpy(dtype=float)).all():
        raise CalibrationGuardError("Stored HTF setup score contains non-finite values")
    score_error = float((scored["candidate_score"] - stored_score).abs().max())
    setup_error = float((scored["candidate_setup_score"] - stored_setup).abs().max())
    if score_error > tolerance or setup_error > tolerance:
        raise CalibrationGuardError(
            "HTF baseline parity failed: "
            f"momentum max abs error {score_error:.12g}, "
            f"setup max abs error {setup_error:.12g}"
        )
    return HTFParityResult(score_error, setup_error)


def _natural_epoch_map(timestamps: pd.Series, *, gap_hours: float = 24.0) -> pd.Series:
    unique = pd.Series(pd.to_datetime(timestamps, format="ISO8601").unique()).sort_values()
    if unique.empty:
        raise CalibrationGuardError("Cannot derive HTF epochs from empty timestamps")
    epoch = (unique.diff() > pd.Timedelta(hours=gap_hours)).cumsum().astype(int) + 1
    return pd.Series(epoch.to_numpy(), index=pd.DatetimeIndex(unique), name="epoch")


def evaluate_htf_candidate_metrics(
    metric_frame: pd.DataFrame,
    forward_by_horizon: Mapping[float, pd.DataFrame],
    candidate: WeightCandidate,
    *,
    objective_horizon_weights: Mapping[
        float, float
    ] = HTF_OBJECTIVE_HORIZON_WEIGHTS,
    evaluation_horizons: Sequence[float] = HTF_EVALUATION_HORIZONS,
    minimum_group_size: int = 10,
    epoch_gap_hours: float = 24.0,
) -> HTFCandidateEvaluation:
    scored = score_htf_candidate(metric_frame, candidate.weights)
    epoch_map = _natural_epoch_map(scored["scan_ts"], gap_hours=epoch_gap_hours)
    mean_ic: dict[float, float] = {}
    dashboard_excess: dict[float, float] = {}
    rank_reports: dict[float, pd.DataFrame] = {}
    dashboard_reports: dict[float, pd.DataFrame] = {}

    for horizon in evaluation_horizons:
        numeric_horizon = float(horizon)
        forward = forward_by_horizon.get(numeric_horizon)
        if forward is None:
            raise CalibrationGuardError(f"Missing HTF forward frame for {numeric_horizon}h")
        rank_report = cross_section_rank_ic(
            scored,
            forward,
            minimum_group_size=minimum_group_size,
        )
        dashboard_report = exact_dashboard_excess(scored, forward)
        if rank_report.empty:
            raise CalibrationGuardError(f"No measurable HTF rank IC at {numeric_horizon}h")
        if dashboard_report.empty:
            raise CalibrationGuardError(
                f"No default-dashboard HTF selections at {numeric_horizon}h"
            )
        rank_reports[numeric_horizon] = rank_report
        dashboard_reports[numeric_horizon] = dashboard_report
        mean_ic[numeric_horizon] = float(rank_report["rank_ic"].mean())
        dashboard_excess[numeric_horizon] = float(
            dashboard_report["excess_ret"].mean()
        )

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
                    f"HTF epoch {epoch_id} has no rank IC at objective horizon {horizon}"
                )
            value += float(weight) * float(part["rank_ic"].mean())
        epoch_objective[epoch_id] = float(value)

    return HTFCandidateEvaluation(
        candidate=candidate,
        mean_ic_by_horizon=mean_ic,
        dashboard_excess_by_horizon=dashboard_excess,
        objective_ic=float(objective),
        epoch_objective=epoch_objective,
        rank_ic_by_horizon=rank_reports,
        dashboard_by_horizon=dashboard_reports,
    )


def htf_training_gate_decision(
    baseline: HTFCandidateEvaluation,
    candidate: HTFCandidateEvaluation,
    *,
    primary_horizon: float = HTF_PRIMARY_HORIZON,
    gate: HTFTrainingGate = HTFTrainingGate(),
) -> HTFCandidateGateDecision:
    if candidate.candidate.candidate_id == "baseline":
        raise CalibrationGuardError("HTF baseline cannot be its own challenger")
    if set(candidate.epoch_objective) != set(baseline.epoch_objective):
        raise CalibrationGuardError("HTF candidate and baseline epoch sets differ")
    numeric_primary = float(primary_horizon)
    objective_delta = float(candidate.objective_ic - baseline.objective_ic)
    dashboard_delta = float(
        candidate.dashboard_excess_by_horizon[numeric_primary]
        - baseline.dashboard_excess_by_horizon[numeric_primary]
    )
    reasons = []
    if objective_delta < gate.objective_delta_minimum:
        reasons.append("objective_delta_below_minimum")
    if candidate.mean_ic_by_horizon[numeric_primary] < gate.primary_ic_minimum:
        reasons.append("primary_ic_below_minimum")
    for epoch_id, baseline_value in baseline.epoch_objective.items():
        if (
            candidate.epoch_objective[epoch_id]
            < baseline_value - gate.maximum_epoch_objective_shortfall
        ):
            reasons.append(f"epoch_{epoch_id}_objective_shortfall")
    if dashboard_delta < gate.primary_dashboard_excess_delta_minimum:
        reasons.append("primary_dashboard_excess_delta_below_minimum")
    baseline_weights = baseline.candidate.weights
    if tuple(candidate.candidate.weights) != tuple(baseline_weights):
        raise CalibrationGuardError("HTF candidate and baseline factor sets differ")
    l1 = sum(
        abs(float(candidate.candidate.weights[key]) - float(baseline_weights[key]))
        for key in baseline_weights
    )
    return HTFCandidateGateDecision(
        candidate_id=candidate.candidate.candidate_id,
        passed=not reasons,
        reasons=tuple(reasons),
        objective_delta=objective_delta,
        primary_dashboard_excess_delta=dashboard_delta,
        worst_epoch_objective=float(min(candidate.epoch_objective.values())),
        l1_distance=float(l1),
    )


def select_htf_training_winner(
    evaluations: Sequence[HTFCandidateEvaluation],
    *,
    primary_horizon: float = HTF_PRIMARY_HORIZON,
    gate: HTFTrainingGate = HTFTrainingGate(),
) -> tuple[tuple[HTFCandidateGateDecision, ...], HTFCandidateEvaluation | None]:
    baselines = [
        item for item in evaluations if item.candidate.candidate_id == "baseline"
    ]
    if len(baselines) != 1:
        raise CalibrationGuardError("HTF training selection requires exactly one baseline")
    baseline = baselines[0]
    indexed = {
        item.candidate.candidate_id: (index, item)
        for index, item in enumerate(evaluations)
    }
    if len(indexed) != len(evaluations):
        raise CalibrationGuardError("HTF training evaluations contain duplicate candidate ids")
    decisions = tuple(
        htf_training_gate_decision(
            baseline,
            item,
            primary_horizon=primary_horizon,
            gate=gate,
        )
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


def _utc_naive(value: object, label: str) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise CalibrationGuardError(f"Invalid HTF {label}: {value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed


def _assert_training_frame(
    frame: pd.DataFrame, partition: Mapping[str, object]
) -> pd.DataFrame:
    if frame.empty or "scan_ts" not in frame.columns:
        raise CalibrationGuardError("HTF training frame is empty or lacks scan_ts")
    start = _utc_naive(partition.get("start_inclusive_utc"), "training start")
    end = _utc_naive(partition.get("end_inclusive_utc"), "training end")
    timestamps = pd.to_datetime(frame["scan_ts"], format="ISO8601")
    if timestamps.isna().any() or (timestamps < start).any() or (timestamps > end).any():
        raise CalibrationGuardError("HTF training frame contains rows outside training")
    out = frame.copy()
    out["scan_ts"] = timestamps
    return out


def _locked_htf_experiment(
    protocol: Mapping[str, object],
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    validate_locked_protocol(protocol)
    ranking = protocol.get("ranking_experiments")
    if not isinstance(ranking, Mapping):
        raise CalibrationGuardError("Protocol has no ranking experiments")
    experiment = ranking.get("htf")
    rules = ranking.get("candidate_generation")
    if not isinstance(experiment, Mapping) or experiment.get("experiment_id") != "alt-htf-rank-v1":
        raise CalibrationGuardError("Protocol has no locked HTF ranking experiment")
    if not isinstance(rules, Mapping):
        raise CalibrationGuardError("Protocol has no candidate-generation rules")
    expected_horizons = {
        "primary_horizon_hours": HTF_PRIMARY_HORIZON,
        "secondary_horizon_hours": 4.0,
        "safety_horizon_hours": 1.0,
    }
    for key, expected in expected_horizons.items():
        if key in experiment and float(experiment[key]) != expected:
            raise CalibrationGuardError(f"Unexpected locked HTF {key}")
    fixed = experiment.get("fixed_during_experiment")
    if isinstance(fixed, Mapping) and (
        float(fixed.get("overextension_penalty", float("nan")))
        != HTF_SETUP_OVEREXTENSION_PENALTY
    ):
        raise CalibrationGuardError("Unexpected locked HTF overextension penalty")
    return experiment, rules


def run_htf_training_stage(
    protocol: Mapping[str, object],
    metric_frame: pd.DataFrame,
) -> HTFTrainingStageResult:
    partition = resolve_partition(protocol, "training", purpose="candidate_selection")
    training = _assert_training_frame(metric_frame, partition)
    if training.duplicated(["scan_ts", "symbol"]).any():
        raise CalibrationGuardError(
            "HTF training frame contains duplicate (scan_ts, symbol) rows"
        )
    expected_metric = partition.get("metric")
    if isinstance(expected_metric, Mapping):
        observed = {
            "rows": len(training),
            "scans": training["scan_ts"].nunique(),
            "symbols": training["symbol"].nunique(),
        }
        for key in ("rows", "scans", "symbols"):
            if key in expected_metric and int(expected_metric[key]) != int(observed[key]):
                raise CalibrationGuardError(
                    f"HTF training frame does not match locked {key} count"
                )

    experiment, rules = _locked_htf_experiment(protocol)
    baseline_weights = validate_weight_vector(
        experiment["baseline_weights"], experiment["bounds"]
    )
    candidates = generate_htf_candidates(
        baseline_weights,
        experiment["bounds"],
        maximum_l1_distance=float(rules["maximum_l1_distance_from_baseline"]),
        maximum_candidates=int(
            rules["maximum_candidates_per_experiment_including_baseline"]
        ),
    )
    parity = verify_htf_baseline_score_parity(training, baseline_weights)
    prices = training[["scan_ts", "symbol", "price"]]
    isolation = protocol.get("partition_isolation")
    tolerance_fraction = 0.35
    if isinstance(isolation, Mapping):
        if "forward_horizons_hours" in isolation and tuple(
            float(value) for value in isolation["forward_horizons_hours"]
        ) != HTF_EVALUATION_HORIZONS:
            raise CalibrationGuardError("Unexpected locked HTF forward horizons")
        tolerance_fraction = float(
            isolation.get("forward_join_tolerance_fraction", tolerance_fraction)
        )
    forward_by_horizon = {
        horizon: build_protocol_partition_forward_returns(
            prices,
            horizon,
            protocol,
            "training",
            purpose="candidate_selection",
            tolerance_fraction=tolerance_fraction,
        )
        for horizon in HTF_EVALUATION_HORIZONS
    }
    evaluations = tuple(
        evaluate_htf_candidate_metrics(training, forward_by_horizon, candidate)
        for candidate in candidates
    )
    decisions, winner = select_htf_training_winner(evaluations)
    return HTFTrainingStageResult(
        partition_name="training",
        evaluated=evaluations,
        decisions=decisions,
        winner=winner,
        baseline_parity=parity,
        candidate_count=len(candidates),
    )


def build_htf_training_comparison_report(
    result: HTFTrainingStageResult,
    *,
    bootstrap_draws: int = 5000,
    seed: int = 20260903,
) -> dict[str, object]:
    if result.partition_name != "training":
        raise CalibrationGuardError("HTF report requires a training-stage result")
    baselines = [
        item for item in result.evaluated if item.candidate.candidate_id == "baseline"
    ]
    if len(baselines) != 1:
        raise CalibrationGuardError("HTF report requires exactly one baseline")
    baseline = baselines[0]
    decisions = {decision.candidate_id: decision for decision in result.decisions}
    if len(decisions) != len(result.decisions):
        raise CalibrationGuardError("HTF result has duplicate candidate decisions")
    expected_decisions = {
        item.candidate.candidate_id
        for item in result.evaluated
        if item.candidate.candidate_id != "baseline"
    }
    if set(decisions) != expected_decisions:
        raise CalibrationGuardError("HTF result decision set does not match challengers")
    challengers = []
    for evaluation in result.evaluated:
        if evaluation.candidate.candidate_id == "baseline":
            continue
        decision = decisions.get(evaluation.candidate.candidate_id)
        if decision is None:
            raise CalibrationGuardError("HTF result is missing a candidate decision")
        rank_paired = {}
        dashboard_paired = {}
        for horizon in HTF_EVALUATION_HORIZONS:
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
                "primary_dashboard_excess_delta": (
                    decision.primary_dashboard_excess_delta
                ),
                "worst_epoch_objective": decision.worst_epoch_objective,
                "l1_distance": decision.l1_distance,
                "mean_ic_by_horizon": {
                    str(key): value
                    for key, value in evaluation.mean_ic_by_horizon.items()
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
        "experiment_id": "alt-htf-rank-v1",
        "candidate_count": result.candidate_count,
        "challenger_count": len(challengers),
        "baseline_parity": {
            "momentum_max_abs_error": result.baseline_parity.momentum_max_abs_error,
            "setup_max_abs_error": result.baseline_parity.setup_max_abs_error,
        },
        "baseline": {
            "weights": dict(baseline.candidate.weights),
            "objective_ic": baseline.objective_ic,
            "mean_ic_by_horizon": {
                str(key): value for key, value in baseline.mean_ic_by_horizon.items()
            },
            "dashboard_excess_by_horizon": {
                str(key): value
                for key, value in baseline.dashboard_excess_by_horizon.items()
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
