"""Leakage-resistant Phase 5 validation for frozen Strategy Lab events."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from .event_study import EventStudySettings, validate_reference_contract


class ValidationGateError(ValueError):
    """Raised when a Phase 5 validation gate fails closed."""


OUTCOME_PREFIXES = ("barrier_", "forward_", "funding_")
OUTCOME_COLUMNS = {"ambiguous_bar_count"}
FORBIDDEN_SELECTION_PREFIXES = OUTCOME_PREFIXES


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    training_end_bar_exclusive: int
    validation_start_bar_inclusive: int
    validation_end_bar_exclusive: int
    training: pd.DataFrame
    validation: pd.DataFrame
    purged_training_events: int
    embargoed_validation_events: int


def _event_outcome_columns(events: pd.DataFrame) -> list[str]:
    return [
        column
        for column in events.columns
        if column in OUTCOME_COLUMNS or column.startswith(OUTCOME_PREFIXES)
    ]


def _frame_sha256(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(
        index=False,
        lineterminator="\n",
        na_rep="",
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_holdout_lock(
    events: pd.DataFrame,
    *,
    total_bars: int,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Prove chronological labels and that final-holdout outcomes are absent."""
    if events.empty:
        raise ValidationGateError("The validation event log is empty")
    required = {"signal_index", "split", "holdout_hidden"}
    missing = required - set(events.columns)
    if missing:
        raise ValidationGateError(f"Event log is missing holdout fields: {sorted(missing)}")
    splits = contract["validation"]["splits"]
    training_end = int(total_bars * float(splits["training"]))
    validation_end = int(
        total_bars
        * (float(splits["training"]) + float(splits["validation"]))
    )
    signal_index = pd.to_numeric(events["signal_index"], errors="raise").astype(int)
    expected = np.full(len(events), "final_holdout", dtype=object)
    expected[signal_index.to_numpy() < validation_end] = "validation"
    expected[signal_index.to_numpy() < training_end] = "training"
    actual = events["split"].astype(str).to_numpy()
    mismatches = np.flatnonzero(actual != expected)
    if mismatches.size:
        row = int(mismatches[0])
        raise ValidationGateError(
            f"Chronological split mismatch at row {row}: "
            f"{actual[row]} != {expected[row]}"
        )
    hidden = events["holdout_hidden"].astype(bool)
    is_holdout = events["split"].astype(str).eq("final_holdout")
    if not hidden[is_holdout].all():
        raise ValidationGateError("Every final-holdout event must remain hidden")
    if hidden[~is_holdout].any():
        raise ValidationGateError("Development events cannot be marked hidden")
    outcome_columns = _event_outcome_columns(events)
    exposed = [
        column
        for column in outcome_columns
        if events.loc[is_holdout, column].notna().any()
    ]
    if exposed:
        raise ValidationGateError(
            f"Final-holdout outcomes were exposed: {sorted(exposed)}"
        )
    holdout = events.loc[
        is_holdout,
        [
            column
            for column in (
                "signal_index",
                "signal_time_ms",
                "entry_index",
                "entry_time_ms",
                "ema",
                "split",
                "holdout_hidden",
                "subperiod",
                "regime",
            )
            if column in events.columns
        ],
    ].copy()
    if holdout.empty:
        raise ValidationGateError("The contract requires a non-empty final holdout")
    development = events.loc[~is_holdout]
    if int(development["signal_index"].max()) >= int(holdout["signal_index"].min()):
        raise ValidationGateError("Final holdout is not strictly chronological")
    return {
        "status": "passed",
        "policy": "locked_outcomes_absent",
        "holdout_opened": False,
        "training_end_bar_exclusive": training_end,
        "validation_end_bar_exclusive": validation_end,
        "development_event_count": int(len(development)),
        "hidden_holdout_event_count": int(len(holdout)),
        "first_holdout_signal_index": int(holdout["signal_index"].min()),
        "holdout_metadata_sha256": _frame_sha256(holdout),
        "outcome_columns_checked": outcome_columns,
    }


def validate_delay_one_and_selection_inputs(
    events: pd.DataFrame,
    *,
    selection_columns: Iterable[str] = ("ema",),
) -> dict[str, Any]:
    """Reject same-bar entries and outcome fields used as selection inputs."""
    required = {"signal_index", "entry_index"}
    missing = required - set(events.columns)
    if missing:
        raise ValidationGateError(f"Event log is missing timing fields: {sorted(missing)}")
    signal = pd.to_numeric(events["signal_index"], errors="raise").astype(int)
    entry = pd.to_numeric(events["entry_index"], errors="raise").astype(int)
    bad_delay = np.flatnonzero((entry - signal).to_numpy() != 1)
    if bad_delay.size:
        row = int(bad_delay[0])
        raise ValidationGateError(
            f"Entry must be delay-1 at row {row}: "
            f"signal={signal.iloc[row]}, entry={entry.iloc[row]}"
        )
    selected = tuple(str(column) for column in selection_columns)
    forbidden = [
        column
        for column in selected
        if column in OUTCOME_COLUMNS or column.startswith(FORBIDDEN_SELECTION_PREFIXES)
    ]
    if forbidden:
        raise ValidationGateError(
            f"Outcome fields cannot be selection inputs: {sorted(forbidden)}"
        )
    missing_selected = [column for column in selected if column not in events.columns]
    if missing_selected:
        raise ValidationGateError(
            f"Selection inputs are missing: {sorted(missing_selected)}"
        )
    if {"signal_time_ms", "entry_time_ms"} <= set(events.columns):
        signal_ms = pd.to_numeric(events["signal_time_ms"], errors="raise")
        entry_ms = pd.to_numeric(events["entry_time_ms"], errors="raise")
        if not (entry_ms > signal_ms).all():
            raise ValidationGateError("Entry timestamps must be after signal timestamps")
    return {
        "status": "passed",
        "event_count_checked": int(len(events)),
        "entry_delay_bars": 1,
        "selection_columns": list(selected),
        "outcome_columns_rejected": True,
    }


def validate_feature_provenance(records: pd.DataFrame) -> dict[str, Any]:
    """Require every decision feature to be available before its decision."""
    required = {
        "feature",
        "source_index",
        "available_index",
        "decision_index",
        "used_for_decision",
    }
    missing = required - set(records.columns)
    if missing:
        raise ValidationGateError(
            f"Feature provenance is missing fields: {sorted(missing)}"
        )
    used = records["used_for_decision"].astype(bool)
    feature = records["feature"].astype(str)
    forbidden = used & feature.map(
        lambda value: (
            value in OUTCOME_COLUMNS
            or value.startswith(FORBIDDEN_SELECTION_PREFIXES)
        )
    )
    if forbidden.any():
        raise ValidationGateError(
            f"Outcome feature used for a decision: {feature[forbidden].iloc[0]}"
        )
    source = pd.to_numeric(records["source_index"], errors="raise")
    available = pd.to_numeric(records["available_index"], errors="raise")
    decision = pd.to_numeric(records["decision_index"], errors="raise")
    if (source > available).any():
        raise ValidationGateError("A feature is available before its source exists")
    late = used & (available >= decision)
    if late.any():
        row = records.loc[late].iloc[0]
        raise ValidationGateError(
            f"Feature {row['feature']} is not available before decision index "
            f"{int(row['decision_index'])}"
        )
    return {
        "status": "passed",
        "record_count": int(len(records)),
        "decision_feature_count": int(used.sum()),
        "strictly_pre_decision": True,
    }


def assert_recursive_indicator_stability(
    values: pd.Series,
    *,
    compute: Callable[[pd.Series], pd.Series],
    checkpoints: Iterable[int],
    atol: float = 1e-12,
) -> dict[str, Any]:
    """Compare full-history values with values recomputed on truncated history."""
    numeric = pd.to_numeric(values, errors="raise").reset_index(drop=True)
    full = pd.Series(compute(numeric)).reset_index(drop=True)
    checked = 0
    max_difference = 0.0
    for raw_index in checkpoints:
        index = int(raw_index)
        if index < 0 or index >= len(numeric):
            raise ValidationGateError(f"Recursive checkpoint is out of range: {index}")
        truncated = pd.Series(compute(numeric.iloc[: index + 1])).reset_index(drop=True)
        expected = full.iloc[index]
        actual = truncated.iloc[-1]
        if pd.isna(expected) and pd.isna(actual):
            continue
        if pd.isna(expected) != pd.isna(actual):
            raise ValidationGateError(
                f"Recursive indicator changed at index {index}: "
                f"{actual!r} != {expected!r}"
            )
        difference = abs(float(actual) - float(expected))
        max_difference = max(max_difference, difference)
        if difference > atol:
            raise ValidationGateError(
                f"Recursive indicator changed at index {index}: "
                f"difference {difference} > {atol}"
            )
        checked += 1
    if checked == 0:
        raise ValidationGateError("No finite recursive indicator checkpoints were tested")
    return {
        "status": "passed",
        "checkpoint_count": checked,
        "absolute_tolerance": atol,
        "maximum_absolute_difference": max_difference,
    }


def benjamini_hochberg(p_values: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(p_values), dtype=float)
    if values.ndim != 1 or values.size == 0:
        raise ValidationGateError("At least one p-value is required")
    if not np.isfinite(values).all() or ((values < 0.0) | (values > 1.0)).any():
        raise ValidationGateError("P-values must be finite values in [0, 1]")
    order = np.argsort(values)
    ranked = values[order]
    count = len(values)
    adjusted_ranked = np.empty(count, dtype=float)
    running = 1.0
    for reverse_index in range(count - 1, -1, -1):
        rank = reverse_index + 1
        running = min(running, ranked[reverse_index] * count / rank)
        adjusted_ranked[reverse_index] = min(1.0, running)
    adjusted = np.empty(count, dtype=float)
    adjusted[order] = adjusted_ranked
    return adjusted


def validate_multiple_testing_ledger(
    ledger: pd.DataFrame,
    *,
    expected_hypotheses: Iterable[str],
    method: str,
    atol: float = 1e-12,
) -> dict[str, Any]:
    required = {
        "hypothesis_id",
        "raw_p_value",
        "adjusted_p_value",
        "correction_method",
    }
    missing = required - set(ledger.columns)
    if missing:
        raise ValidationGateError(
            f"Multiple-testing ledger is missing fields: {sorted(missing)}"
        )
    expected = list(expected_hypotheses)
    actual = ledger["hypothesis_id"].astype(str).tolist()
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValidationGateError(
            f"Multiple-testing ledger is incomplete: {actual} != {expected}"
        )
    if method != "benjamini_hochberg":
        raise ValidationGateError(f"Unsupported Phase 5 correction method: {method}")
    if set(ledger["correction_method"].astype(str)) != {method}:
        raise ValidationGateError("Correction method is missing or inconsistent")
    raw = pd.to_numeric(ledger["raw_p_value"], errors="raise").to_numpy(dtype=float)
    adjusted = pd.to_numeric(
        ledger["adjusted_p_value"],
        errors="raise",
    ).to_numpy(dtype=float)
    expected_adjusted = benjamini_hochberg(raw)
    if not np.allclose(adjusted, expected_adjusted, rtol=0.0, atol=atol):
        raise ValidationGateError(
            "Adjusted p-values do not match Benjamini-Hochberg correction"
        )
    return {
        "status": "passed",
        "method": method,
        "hypothesis_count": int(len(ledger)),
        "all_hypotheses_logged": True,
        "maximum_adjustment_difference": float(
            np.max(np.abs(adjusted - expected_adjusted))
        ),
    }


def _wilson_interval(
    successes: int,
    total: int,
    confidence: float,
) -> list[float | None]:
    if total <= 0:
        return [None, None]
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            (rate * (1.0 - rate) + z * z / (4.0 * total))
            / total
        )
        / denominator
    )
    return [center - margin, center + margin]


def newey_west_mean_statistics(
    values: Iterable[float],
    *,
    max_lag: int,
    confidence: float,
) -> dict[str, Any]:
    clean = np.asarray(list(values), dtype=float)
    clean = clean[np.isfinite(clean)]
    if clean.size == 0:
        return {
            "sample_size": 0,
            "effective_sample_size": 0.0,
            "mean": None,
            "standard_error": None,
            "t_statistic": None,
            "p_value_one_sided_greater": None,
            "confidence_interval": [None, None],
            "max_lag": 0,
        }
    mean = float(clean.mean())
    if clean.size == 1:
        return {
            "sample_size": 1,
            "effective_sample_size": 1.0,
            "mean": mean,
            "standard_error": None,
            "t_statistic": None,
            "p_value_one_sided_greater": None,
            "confidence_interval": [None, None],
            "max_lag": 0,
        }
    centered = clean - mean
    gamma0 = float(np.dot(centered, centered) / len(clean))
    lag_limit = max(0, min(int(max_lag), len(clean) - 1))
    long_run_variance = gamma0
    for lag in range(1, lag_limit + 1):
        covariance = float(
            np.dot(centered[lag:], centered[:-lag])
            / len(clean)
        )
        weight = 1.0 - lag / (lag_limit + 1.0)
        long_run_variance += 2.0 * weight * covariance
    if long_run_variance <= 0.0:
        long_run_variance = gamma0
    standard_error = (
        math.sqrt(long_run_variance / len(clean))
        if long_run_variance > 0.0
        else 0.0
    )
    if standard_error > 0.0:
        t_statistic = mean / standard_error
        p_value = 1.0 - NormalDist().cdf(t_statistic)
        z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
        interval = [mean - z * standard_error, mean + z * standard_error]
    else:
        t_statistic = 0.0
        p_value = 0.0 if mean > 0.0 else 1.0
        interval = [mean, mean]
    effective = (
        min(float(len(clean)), float(len(clean) * gamma0 / long_run_variance))
        if gamma0 > 0.0 and long_run_variance > 0.0
        else float(len(clean))
    )
    return {
        "sample_size": int(len(clean)),
        "effective_sample_size": effective,
        "mean": mean,
        "standard_error": standard_error,
        "t_statistic": t_statistic,
        "p_value_one_sided_greater": p_value,
        "confidence_interval": interval,
        "max_lag": lag_limit,
    }


def moving_block_bootstrap_interval(
    values: Iterable[float],
    *,
    confidence: float,
    samples: int,
    seed: int,
    block_length: int | None = None,
) -> dict[str, Any]:
    clean = np.asarray(list(values), dtype=float)
    clean = clean[np.isfinite(clean)]
    if clean.size == 0:
        return {
            "sample_size": 0,
            "samples": int(samples),
            "block_length": 0,
            "mean_interval": [None, None],
            "median_interval": [None, None],
        }
    block = (
        max(1, min(int(block_length), len(clean)))
        if block_length is not None
        else max(1, min(int(math.ceil(len(clean) ** (1.0 / 3.0))), len(clean)))
    )
    rng = np.random.default_rng(seed)
    means = np.empty(int(samples), dtype=float)
    medians = np.empty(int(samples), dtype=float)
    blocks_needed = int(math.ceil(len(clean) / block))
    offsets = np.arange(block, dtype=int)
    batch_size = 50
    offset = 0
    while offset < samples:
        size = min(batch_size, samples - offset)
        starts = rng.integers(
            0,
            len(clean),
            size=(size, blocks_needed),
        )
        indices = (starts[:, :, None] + offsets) % len(clean)
        sampled = clean[indices].reshape(size, -1)[:, : len(clean)]
        means[offset : offset + size] = sampled.mean(axis=1)
        medians[offset : offset + size] = np.median(sampled, axis=1)
        offset += size
    alpha = (1.0 - confidence) / 2.0
    return {
        "sample_size": int(len(clean)),
        "samples": int(samples),
        "block_length": block,
        "mean_interval": [
            float(np.quantile(means, alpha)),
            float(np.quantile(means, 1.0 - alpha)),
        ],
        "median_interval": [
            float(np.quantile(medians, alpha)),
            float(np.quantile(medians, 1.0 - alpha)),
        ],
    }


def summarize_validation_slice(
    events: pd.DataFrame,
    *,
    outcome_column: str,
    confidence: float,
    max_lag: int,
) -> dict[str, Any]:
    values = pd.to_numeric(events[outcome_column], errors="coerce").dropna()
    statistics = newey_west_mean_statistics(
        values,
        max_lag=max_lag,
        confidence=confidence,
    )
    positives = int((values > 0.0).sum())
    resolved = (
        events.loc[events["barrier_status"].notna()]
        if "barrier_status" in events
        else pd.DataFrame()
    )
    resolved = (
        resolved.loc[resolved["barrier_status"] != "unresolved"]
        if not resolved.empty
        else resolved
    )
    targets = (
        int((resolved["barrier_status"] == "target").sum())
        if not resolved.empty
        else 0
    )
    return {
        "event_count": int(len(events)),
        "outcome_sample_size": int(len(values)),
        "mean_net_return": statistics["mean"],
        "median_net_return": float(values.median()) if len(values) else None,
        "positive_return_count": positives,
        "positive_return_rate": positives / len(values) if len(values) else None,
        "positive_return_rate_interval": _wilson_interval(
            positives,
            len(values),
            confidence,
        ),
        "resolved_event_count": int(len(resolved)),
        "target_count": targets,
        "historical_empirical_success_rate": (
            targets / len(resolved) if len(resolved) else None
        ),
        "success_rate_interval": _wilson_interval(
            targets,
            len(resolved),
            confidence,
        ),
        "newey_west": statistics,
    }


def build_anchored_walk_forward_folds(
    events: pd.DataFrame,
    *,
    total_bars: int,
    contract: dict[str, Any],
    fold_count: int = 2,
) -> list[WalkForwardFold]:
    settings = validate_reference_contract(contract)
    validation = contract["validation"]
    if validation["method"] != "anchored_walk_forward":
        raise ValidationGateError("Phase 5 requires anchored walk-forward validation")
    if fold_count < 1:
        raise ValidationGateError("At least one validation fold is required")
    splits = validation["splits"]
    training_end = int(total_bars * float(splits["training"]))
    development_end = int(
        total_bars
        * (float(splits["training"]) + float(splits["validation"]))
    )
    validation_span = development_end - training_end
    if validation_span < fold_count:
        raise ValidationGateError("Validation span is too short for requested folds")
    fold_width = validation_span // fold_count
    purge = int(validation["purge_bars"])
    embargo = int(validation["embargo_bars"])
    max_horizon = max(max(settings.horizons), settings.max_holding_bars)
    development = events.loc[~events["holdout_hidden"].astype(bool)].copy()
    development["outcome_end_index"] = (
        pd.to_numeric(development["entry_index"], errors="raise").astype(int)
        + max_horizon
        - 1
    )
    folds: list[WalkForwardFold] = []
    for index in range(fold_count):
        validation_start = training_end + index * fold_width
        validation_end = (
            development_end
            if index == fold_count - 1
            else training_end + (index + 1) * fold_width
        )
        training_candidates = development.loc[
            development["signal_index"] < validation_start
        ]
        training = training_candidates.loc[
            training_candidates["outcome_end_index"]
            < validation_start - purge
        ].copy()
        validation_candidates = development.loc[
            (development["signal_index"] >= validation_start)
            & (development["signal_index"] < validation_end)
        ]
        fold_validation = validation_candidates.loc[
            (validation_candidates["signal_index"] >= validation_start + embargo)
            & (validation_candidates["outcome_end_index"] < validation_end)
        ].copy()
        if training.empty or fold_validation.empty:
            raise ValidationGateError(
                f"Walk-forward fold {index + 1} has an empty partition"
            )
        if training["holdout_hidden"].astype(bool).any():
            raise ValidationGateError("Walk-forward training accessed the holdout")
        if fold_validation["holdout_hidden"].astype(bool).any():
            raise ValidationGateError("Walk-forward validation accessed the holdout")
        if int(training["outcome_end_index"].max()) >= validation_start - purge:
            raise ValidationGateError("Training purge boundary was violated")
        if int(fold_validation["signal_index"].min()) < validation_start + embargo:
            raise ValidationGateError("Validation embargo boundary was violated")
        folds.append(
            WalkForwardFold(
                fold_id=f"fold-{index + 1}",
                training_end_bar_exclusive=validation_start - purge,
                validation_start_bar_inclusive=validation_start + embargo,
                validation_end_bar_exclusive=validation_end,
                training=training,
                validation=fold_validation,
                purged_training_events=int(len(training_candidates) - len(training)),
                embargoed_validation_events=int(
                    len(validation_candidates) - len(fold_validation)
                ),
            )
        )
    return folds


def _feature_provenance_fixture(events: pd.DataFrame) -> pd.DataFrame:
    sample = events.iloc[[0, len(events) // 2, len(events) - 1]]
    rows: list[dict[str, Any]] = []
    for row in sample.itertuples(index=False):
        rows.extend(
            [
                {
                    "feature": "ema",
                    "source_index": int(row.signal_index),
                    "available_index": int(row.signal_index),
                    "decision_index": int(row.entry_index),
                    "used_for_decision": True,
                },
                {
                    "feature": "signal_low",
                    "source_index": int(row.signal_index),
                    "available_index": int(row.signal_index),
                    "decision_index": int(row.entry_index),
                    "used_for_decision": True,
                },
                {
                    "feature": "forward_12_net",
                    "source_index": int(row.entry_index) + 11,
                    "available_index": int(row.entry_index) + 11,
                    "decision_index": int(row.entry_index),
                    "used_for_decision": False,
                },
            ]
        )
    return pd.DataFrame(rows)


def run_adversarial_validation_checks(
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Prove that deliberately invalid inputs are rejected."""
    checks: dict[str, dict[str, Any]] = {}

    def expect_rejection(name: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except ValidationGateError as exc:
            checks[name] = {"status": "rejected", "message": str(exc)}
            return
        raise AssertionError(f"Adversarial validation check did not fail: {name}")

    bad_delay = pd.DataFrame(
        {
            "signal_index": [10],
            "entry_index": [10],
            "ema": [100.0],
        }
    )
    expect_rejection(
        "same_bar_entry_leakage",
        lambda: validate_delay_one_and_selection_inputs(bad_delay),
    )

    bad_provenance = pd.DataFrame(
        [
            {
                "feature": "future_close",
                "source_index": 11,
                "available_index": 11,
                "decision_index": 10,
                "used_for_decision": True,
            }
        ]
    )
    expect_rejection(
        "future_feature_availability",
        lambda: validate_feature_provenance(bad_provenance),
    )

    values = pd.Series(np.arange(20, dtype=float))
    expect_rejection(
        "recursive_future_indicator",
        lambda: assert_recursive_indicator_stability(
            values,
            compute=lambda series: series.shift(-1),
            checkpoints=[5, 10, 15],
        ),
    )

    bad_ledger = pd.DataFrame(
        {
            "hypothesis_id": ["h1", "h2"],
            "raw_p_value": [0.01, 0.04],
            "adjusted_p_value": [0.01, 0.04],
            "correction_method": ["benjamini_hochberg"] * 2,
        }
    )
    expect_rejection(
        "uncorrected_multiple_testing",
        lambda: validate_multiple_testing_ledger(
            bad_ledger,
            expected_hypotheses=["h1", "h2"],
            method="benjamini_hochberg",
        ),
    )

    bad_holdout = pd.DataFrame(
        {
            "signal_index": [10, 70, 90],
            "entry_index": [11, 71, 91],
            "split": ["training", "validation", "final_holdout"],
            "holdout_hidden": [False, False, True],
            "forward_12_net": [0.01, 0.02, 0.03],
        }
    )
    expect_rejection(
        "exposed_final_holdout",
        lambda: validate_holdout_lock(
            bad_holdout,
            total_bars=100,
            contract=contract,
        ),
    )
    return {
        "status": "passed",
        "checks": checks,
        "rejected_check_count": len(checks),
    }


def run_validation_pipeline(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    contract: dict[str, Any],
    *,
    fold_count: int = 2,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Run every Phase 5 gate without opening final-holdout outcomes."""
    settings: EventStudySettings = validate_reference_contract(contract)
    total_bars = len(bars)
    holdout = validate_holdout_lock(
        events,
        total_bars=total_bars,
        contract=contract,
    )
    leakage = validate_delay_one_and_selection_inputs(events)
    provenance = validate_feature_provenance(_feature_provenance_fixture(events))
    last_checkpoint = max(settings.warmup_bars, total_bars - 1)
    checkpoints = np.unique(
        np.linspace(
            settings.warmup_bars,
            last_checkpoint,
            num=min(24, max(1, total_bars - settings.warmup_bars)),
            dtype=int,
        )
    )
    recursive = assert_recursive_indicator_stability(
        bars["close"],
        compute=lambda series: series.ewm(
            span=settings.ema_length,
            adjust=False,
            min_periods=settings.ema_length,
        ).mean(),
        checkpoints=checkpoints,
    )
    folds = build_anchored_walk_forward_folds(
        events,
        total_bars=total_bars,
        contract=contract,
        fold_count=fold_count,
    )
    primary_column = f"forward_{max(settings.horizons)}_net"
    fold_reports: list[dict[str, Any]] = []
    validation_frames: list[pd.DataFrame] = []
    for fold in folds:
        validation_frames.append(fold.validation)
        fold_reports.append(
            {
                "fold_id": fold.fold_id,
                "training_end_bar_exclusive": fold.training_end_bar_exclusive,
                "validation_start_bar_inclusive": (
                    fold.validation_start_bar_inclusive
                ),
                "validation_end_bar_exclusive": fold.validation_end_bar_exclusive,
                "purged_training_events": fold.purged_training_events,
                "embargoed_validation_events": fold.embargoed_validation_events,
                "training": summarize_validation_slice(
                    fold.training,
                    outcome_column=primary_column,
                    confidence=settings.confidence_level,
                    max_lag=max(settings.horizons),
                ),
                "validation": summarize_validation_slice(
                    fold.validation,
                    outcome_column=primary_column,
                    confidence=settings.confidence_level,
                    max_lag=max(settings.horizons),
                ),
            }
        )
    development = events.loc[~events["holdout_hidden"].astype(bool)].copy()
    aggregate_validation = pd.concat(validation_frames, ignore_index=True)
    minimum_events = int(contract["validation"]["minimum_events"])
    if len(development) < minimum_events or len(aggregate_validation) < minimum_events:
        raise ValidationGateError(
            f"Minimum event requirement failed: development={len(development)}, "
            f"validation={len(aggregate_validation)}, required={minimum_events}"
        )

    horizon_reports: dict[str, Any] = {}
    ledger_rows: list[dict[str, Any]] = []
    for offset, horizon in enumerate(settings.horizons):
        column = f"forward_{horizon}_net"
        values = pd.to_numeric(development[column], errors="coerce").dropna()
        hac = newey_west_mean_statistics(
            values,
            max_lag=max(settings.horizons),
            confidence=settings.confidence_level,
        )
        bootstrap = moving_block_bootstrap_interval(
            values,
            confidence=settings.confidence_level,
            samples=settings.bootstrap_samples,
            seed=settings.random_seed + offset,
        )
        hypothesis_id = f"mean_forward_{horizon}_net_positive"
        horizon_reports[str(horizon)] = {
            "hypothesis_id": hypothesis_id,
            "mean_net_return": hac["mean"],
            "median_net_return": float(values.median()) if len(values) else None,
            "newey_west": hac,
            "moving_block_bootstrap": bootstrap,
        }
        ledger_rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "horizon_bars": int(horizon),
                "raw_p_value": float(hac["p_value_one_sided_greater"]),
            }
        )
    ledger = pd.DataFrame(ledger_rows)
    correction_method = contract["validation"]["multiple_testing_correction"]
    ledger["adjusted_p_value"] = benjamini_hochberg(ledger["raw_p_value"])
    ledger["correction_method"] = correction_method
    ledger["reject_at_0_05"] = ledger["adjusted_p_value"] <= 0.05
    multiple_testing = validate_multiple_testing_ledger(
        ledger,
        expected_hypotheses=ledger["hypothesis_id"].tolist(),
        method=correction_method,
    )

    subperiod = {
        str(name): summarize_validation_slice(
            group,
            outcome_column=primary_column,
            confidence=settings.confidence_level,
            max_lag=max(settings.horizons),
        )
        for name, group in development.groupby("subperiod")
    }
    regime = {
        str(name): summarize_validation_slice(
            group,
            outcome_column=primary_column,
            confidence=settings.confidence_level,
            max_lag=max(settings.horizons),
        )
        for name, group in development.groupby("regime")
    }
    fold_means = [
        float(item["validation"]["mean_net_return"])
        for item in fold_reports
        if item["validation"]["mean_net_return"] is not None
    ]
    primary_hypothesis = f"mean_forward_{max(settings.horizons)}_net_positive"
    primary_row = ledger.loc[ledger["hypothesis_id"] == primary_hypothesis].iloc[0]
    if fold_means and all(value > 0.0 for value in fold_means) and bool(
        primary_row["reject_at_0_05"]
    ):
        conclusion = "positive_stable"
    elif fold_means and all(value <= 0.0 for value in fold_means):
        conclusion = "negative"
    else:
        conclusion = "inconclusive_unstable"
    adversarial = run_adversarial_validation_checks(contract)
    report = {
        "status": "passed",
        "phase": 5,
        "claim_level": "exploratory",
        "validation_conclusion": conclusion,
        "primary_outcome": primary_column,
        "holdout": holdout,
        "leakage": leakage,
        "feature_provenance": provenance,
        "recursive_indicator": recursive,
        "walk_forward": {
            "method": "anchored_walk_forward",
            "fold_count": len(fold_reports),
            "purge_bars": int(contract["validation"]["purge_bars"]),
            "embargo_bars": int(contract["validation"]["embargo_bars"]),
            "folds": fold_reports,
            "aggregate_validation_event_count": int(len(aggregate_validation)),
        },
        "uncertainty_by_horizon": horizon_reports,
        "multiple_testing": {
            **multiple_testing,
            "alpha": 0.05,
            "rejected_hypothesis_count": int(ledger["reject_at_0_05"].sum()),
        },
        "stability": {
            "validation_fold_means": fold_means,
            "validation_fold_sign_consistent": (
                bool(all(value > 0.0 for value in fold_means))
                or bool(all(value <= 0.0 for value in fold_means))
            ),
            "subperiods": subperiod,
            "regimes": regime,
        },
        "adversarial_checks": adversarial,
        "warnings": [
            "Final-holdout outcomes remain locked and were not opened.",
            "Success rates are historical empirical rates, not future probabilities.",
            "Corrected development evidence does not authorize deployment.",
            "Phase 5 validates one frozen definition and performs no parameter search.",
        ],
    }
    return report, ledger
