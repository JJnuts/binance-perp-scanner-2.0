"""Frozen Phase 3 EMA-bounce event study with explicit timing and ambiguity."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable

import numpy as np
import pandas as pd


REFERENCE_EXPERIMENT_ID = "btcusdt-5m-ema9-bounce-v1"
REFERENCE_ENGINE_VERSION = "0.28.5"


class ContractCapabilityError(ValueError):
    """Raised when a contract asks Phase 3 to perform an unsupported study."""


@dataclass(frozen=True)
class EventStudySettings:
    ema_length: int
    precondition_bars: int
    touch_tolerance: float
    max_penetration: float
    min_rejection: float
    cooldown_bars: int
    horizons: tuple[int, ...]
    target: float
    stop: float
    max_holding_bars: int
    fee_rate: float
    slippage_rate: float
    warmup_bars: int
    confidence_level: float
    bootstrap_samples: int
    random_seed: int
    training_fraction: float
    validation_fraction: float
    holdout_fraction: float
    holdout_locked: bool
    funding_enabled: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DetailReplayResolver:
    """Resolve ambiguous 5m barriers from prepared, checksummed 1m windows."""

    def __init__(self, index_path: Path | str, *, repo_root: Path | str | None = None) -> None:
        self.index_path = Path(index_path)
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else None
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.windows = {
            int(item["bar_open_time_ms"]): item
            for item in payload["windows"]
        }
        self._cache: dict[int, pd.DataFrame] = {}

    def __call__(self, bar_open_ms: int, target: float, stop: float) -> str:
        item = self.windows.get(int(bar_open_ms))
        if item is None:
            return "unresolved"
        if bar_open_ms not in self._cache:
            relative = item.get("normalized_repo_relative")
            path = (
                self.repo_root / relative
                if relative is not None and self.repo_root is not None
                else Path(item["normalized_file"])
            )
            if _sha256_file(path) != item["normalized_sha256"]:
                raise ValueError(f"Detail window checksum mismatch: {path}")
            frame = pd.read_csv(path)
            for column in ("high", "low"):
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            frame = frame.sort_values("open_time_ms")
            if len(frame) != 5:
                raise ValueError(f"Expected five 1m bars for detail window {bar_open_ms}")
            self._cache[bar_open_ms] = frame
        for row in self._cache[bar_open_ms].itertuples(index=False):
            target_hit = float(row.high) >= target
            stop_hit = float(row.low) <= stop
            if target_hit and stop_hit:
                return "unresolved"
            if target_hit:
                return "target"
            if stop_hit:
                return "stop"
        return "unresolved"


def load_contract(contract_path: Path | str, schema_path: Path | str | None = None) -> dict[str, Any]:
    path = Path(contract_path)
    contract = json.loads(path.read_text(encoding="utf-8"))
    if schema_path is not None:
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError as exc:
            raise ContractCapabilityError("jsonschema is required for contract validation") from exc
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(contract)
    validate_reference_contract(contract)
    return contract


def validate_reference_contract(contract: dict[str, Any]) -> EventStudySettings:
    required = {
        "experiment.id": contract["experiment"]["id"],
        "research.type": contract["research"]["type"],
        "research.engine": contract["research"]["engine"],
        "market.exchange": contract["market"]["exchange"],
        "market.venue": contract["market"]["venue"],
        "market.symbols": contract["market"]["symbols"],
        "market.direction": contract["market"]["direction"],
        "data.timeframe": contract["data"]["timeframe"],
        "data.timezone": contract["data"]["timezone"],
        "data.bar_policy": contract["data"]["bar_policy"],
        "signal.indicator.kind": contract["signal"]["indicator"]["kind"],
        "signal.indicator.length": contract["signal"]["indicator"]["length"],
        "signal.indicator.price_source": contract["signal"]["indicator"]["price_source"],
        "signal.event.type": contract["signal"]["event"]["type"],
        "signal.event.approach": contract["signal"]["event"]["approach"],
        "signal.event.probe_field": contract["signal"]["event"]["probe_field"],
        "signal.event.confirmation": contract["signal"]["event"]["confirmation"],
        "signal.context_filters": contract["signal"]["context_filters"],
        "entry.timing": contract["entry"]["timing"],
        "entry.price": contract["entry"]["price"],
        "entry.overlapping_positions": contract["entry"]["overlapping_positions"],
        "outcome.mode": contract["outcome"]["mode"],
        "barrier.same_bar_resolution": contract["outcome"]["barrier"]["same_bar_resolution"],
        "barrier.fallback_resolution": contract["outcome"]["barrier"]["fallback_resolution"],
        "reporting.probability_label": contract["reporting"]["probability_label"],
    }
    expected = {
        "experiment.id": REFERENCE_EXPERIMENT_ID,
        "research.type": "event_study",
        "research.engine": "vectorbt",
        "market.exchange": "binance",
        "market.venue": "usd_m_futures",
        "market.symbols": ["BTCUSDT"],
        "market.direction": "long",
        "data.timeframe": "5m",
        "data.timezone": "UTC",
        "data.bar_policy": "closed_only",
        "signal.indicator.kind": "ema",
        "signal.indicator.length": 9,
        "signal.indicator.price_source": "close",
        "signal.event.type": "moving_average_bounce",
        "signal.event.approach": "from_above",
        "signal.event.probe_field": "low",
        "signal.event.confirmation": "close_above_average",
        "signal.context_filters": [],
        "entry.timing": "next_bar_open",
        "entry.price": "open",
        "entry.overlapping_positions": "skip",
        "outcome.mode": "both",
        "barrier.same_bar_resolution": "lower_timeframe_replay",
        "barrier.fallback_resolution": "unresolved",
        "reporting.probability_label": "historical_empirical_rate",
    }
    unsupported = {
        key: {"expected": expected[key], "actual": value}
        for key, value in required.items()
        if value != expected[key]
    }
    if unsupported:
        raise ContractCapabilityError(f"Phase 3 supports only the frozen reference contract: {unsupported}")
    start = pd.Timestamp(contract["data"]["start"])
    end = pd.Timestamp(contract["data"]["end"])
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ContractCapabilityError("Contract data boundaries must be ordered timezone-aware values")
    splits = contract["validation"]["splits"]
    if not math.isclose(sum(float(value) for value in splits.values()), 1.0, abs_tol=1e-12):
        raise ContractCapabilityError("Chronological split fractions must total 1.0")
    if int(contract["data"]["warmup_bars"]) < int(contract["signal"]["indicator"]["length"]):
        raise ContractCapabilityError("Warmup must cover the indicator length")
    event = contract["signal"]["event"]
    barrier = contract["outcome"]["barrier"]
    execution = contract["execution"]
    return EventStudySettings(
        ema_length=int(contract["signal"]["indicator"]["length"]),
        precondition_bars=int(event["precondition_bars"]),
        touch_tolerance=float(event["touch_tolerance_bps"]) / 10_000.0,
        max_penetration=float(event["max_penetration_bps"]) / 10_000.0,
        min_rejection=float(event["min_rejection_bps"]) / 10_000.0,
        cooldown_bars=int(contract["entry"]["cooldown_bars"]),
        horizons=tuple(int(value) for value in contract["outcome"]["forward_horizons_bars"]),
        target=float(barrier["target_pct"]) / 100.0,
        stop=float(barrier["stop_pct"]) / 100.0,
        max_holding_bars=int(barrier["max_holding_bars"]),
        fee_rate=float(execution["fee_bps_per_side"]) / 10_000.0,
        slippage_rate=float(execution["slippage_bps_per_side"]) / 10_000.0,
        warmup_bars=int(contract["data"]["warmup_bars"]),
        confidence_level=float(contract["validation"]["confidence_level"]),
        bootstrap_samples=int(contract["validation"]["bootstrap_samples"]),
        random_seed=int(contract["validation"]["random_seed"]),
        training_fraction=float(splits["training"]),
        validation_fraction=float(splits["validation"]),
        holdout_fraction=float(splits["final_holdout"]),
        holdout_locked=bool(contract["validation"]["final_holdout_locked"]),
        funding_enabled=bool(execution["funding"]["include"]),
    )


def load_normalized_bars(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "open_time_utc",
        "open_time_ms",
        "open",
        "high",
        "low",
        "close",
        "close_time_ms",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Normalized bars are missing columns: {sorted(missing)}")
    frame["open_time_utc"] = pd.to_datetime(frame["open_time_utc"], utc=True)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["open_time_ms"] = frame["open_time_ms"].astype(np.int64)
    frame["close_time_ms"] = frame["close_time_ms"].astype(np.int64)
    if not frame["open_time_utc"].is_monotonic_increasing:
        raise ValueError("Normalized bars must be ordered")
    if frame["open_time_utc"].duplicated().any():
        raise ValueError("Normalized bars must be unique")
    return frame.set_index("open_time_utc", drop=False)


def ema_series(close: pd.Series, length: int, *, backend: str) -> pd.Series:
    if backend == "pandas":
        return close.ewm(span=length, adjust=False, min_periods=length).mean()
    if backend != "vectorbt":
        raise ValueError(f"Unknown EMA backend: {backend}")
    try:
        import vectorbt as vbt
    except ImportError as exc:
        raise RuntimeError("The VectorBT backend must run in the pinned Phase 1 environment") from exc
    if version("vectorbt") != REFERENCE_ENGINE_VERSION:
        raise RuntimeError(f"Unexpected VectorBT version: {version('vectorbt')}")
    return vbt.MA.run(close, window=length, ewm=True, adjust=False).ma


def candidate_mask(frame: pd.DataFrame, ema: pd.Series, settings: EventStudySettings) -> pd.Series:
    precondition = pd.Series(True, index=frame.index)
    for shift in range(1, settings.precondition_bars + 1):
        precondition &= frame["close"].shift(shift) > ema.shift(shift)
    lower = ema * (1.0 - settings.max_penetration)
    upper = ema * (1.0 + settings.touch_tolerance)
    rejection = ema * (1.0 + settings.min_rejection)
    mask = (
        precondition
        & (frame["low"] >= lower)
        & (frame["low"] <= upper)
        & (frame["close"] > rejection)
    ).fillna(False)
    mask.iloc[: settings.warmup_bars] = False
    return mask


def find_ambiguous_windows(
    frame: pd.DataFrame,
    contract: dict[str, Any],
    *,
    backend: str,
) -> list[int]:
    """Return every first-hit 5m bar that needs lower-timeframe replay."""
    settings = validate_reference_contract(contract)
    ema = ema_series(frame["close"], settings.ema_length, backend=backend)
    candidates = np.flatnonzero(candidate_mask(frame, ema, settings).to_numpy())
    windows: set[int] = set()
    for signal_index in candidates:
        entry_index = int(signal_index) + 1
        final_index = entry_index + settings.max_holding_bars - 1
        if final_index >= len(frame):
            continue
        entry = float(frame["open"].iloc[entry_index])
        target = entry * (1.0 + settings.target)
        stop = entry * (1.0 - settings.stop)
        for bar_index in range(entry_index, final_index + 1):
            target_hit = float(frame["high"].iloc[bar_index]) >= target
            stop_hit = float(frame["low"].iloc[bar_index]) <= stop
            if target_hit and stop_hit:
                windows.add(int(frame["open_time_ms"].iloc[bar_index]))
                break
            if target_hit or stop_hit:
                break
    return sorted(windows)


def _daily_regimes(frame: pd.DataFrame) -> pd.Series:
    daily_close = frame["close"].resample("1D").last().dropna()
    ema50 = daily_close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema200 = daily_close.ewm(span=200, adjust=False, min_periods=200).mean()
    regime = pd.Series("Range / Transition", index=daily_close.index, dtype=object)
    regime[(daily_close > ema50) & (ema50 > ema200)] = "Bull Trend"
    regime[(daily_close < ema50) & (ema50 < ema200)] = "Bear Trend"
    regime[(ema200.isna())] = "Insufficient Daily Warmup"
    return regime.shift(1)


def _split_labels(length: int, settings: EventStudySettings) -> np.ndarray:
    train_end = int(length * settings.training_fraction)
    validation_end = int(length * (settings.training_fraction + settings.validation_fraction))
    labels = np.full(length, "final_holdout", dtype=object)
    labels[:train_end] = "training"
    labels[train_end:validation_end] = "validation"
    return labels


def _funding_adjustment(
    funding: pd.DataFrame | None,
    entry_ms: int,
    exit_ms: int,
    *,
    enabled: bool,
) -> tuple[float, int]:
    if not enabled:
        return 0.0, 0
    if funding is None:
        raise ValueError("The frozen contract requires historical funding data")
    selected = funding[
        (funding["funding_time_ms"] > entry_ms)
        & (funding["funding_time_ms"] <= exit_ms)
    ]
    return -float(selected["funding_rate"].sum()), int(len(selected))


def _net_return(entry: float, exit_price: float, cost_rate: float, funding_return: float) -> float:
    return (exit_price * (1.0 - cost_rate)) / (entry * (1.0 + cost_rate)) - 1.0 + funding_return


def _resolve_barrier(
    frame: pd.DataFrame,
    entry_index: int,
    settings: EventStudySettings,
    *,
    funding: pd.DataFrame | None,
    detail_resolver: Callable[[int, float, float], str] | None,
) -> dict[str, Any]:
    entry_price = float(frame["open"].iloc[entry_index])
    target_price = entry_price * (1.0 + settings.target)
    stop_price = entry_price * (1.0 - settings.stop)
    final_index = min(entry_index + settings.max_holding_bars - 1, len(frame) - 1)
    ambiguous_bars = 0
    for bar_index in range(entry_index, final_index + 1):
        high = float(frame["high"].iloc[bar_index])
        low = float(frame["low"].iloc[bar_index])
        target_hit = high >= target_price
        stop_hit = low <= stop_price
        if not target_hit and not stop_hit:
            continue
        status: str
        resolution = "5m_ohlc"
        if target_hit and stop_hit:
            ambiguous_bars += 1
            status = (
                detail_resolver(int(frame["open_time_ms"].iloc[bar_index]), target_price, stop_price)
                if detail_resolver
                else "unresolved"
            )
            resolution = "1m_replay" if status in {"target", "stop"} else "unresolved"
            if status not in {"target", "stop"}:
                exit_ms = int(frame["close_time_ms"].iloc[final_index])
                funding_return, funding_count = _funding_adjustment(
                    funding,
                    int(frame["open_time_ms"].iloc[entry_index]),
                    exit_ms,
                    enabled=settings.funding_enabled,
                )
                return {
                    "barrier_status": "unresolved",
                    "barrier_resolution": resolution,
                    "barrier_exit_index": final_index,
                    "barrier_exit_time_ms": exit_ms,
                    "barrier_exit_price": None,
                    "barrier_gross_return": None,
                    "barrier_net_return": None,
                    "funding_return": funding_return,
                    "funding_payments": funding_count,
                    "ambiguous_bar_count": ambiguous_bars,
                }
        else:
            status = "target" if target_hit else "stop"
        exit_price = target_price if status == "target" else stop_price
        exit_ms = int(frame["close_time_ms"].iloc[bar_index])
        funding_return, funding_count = _funding_adjustment(
            funding,
            int(frame["open_time_ms"].iloc[entry_index]),
            exit_ms,
            enabled=settings.funding_enabled,
        )
        gross = exit_price / entry_price - 1.0
        cost = settings.fee_rate + settings.slippage_rate
        return {
            "barrier_status": status,
            "barrier_resolution": resolution,
            "barrier_exit_index": bar_index,
            "barrier_exit_time_ms": exit_ms,
            "barrier_exit_price": exit_price,
            "barrier_gross_return": gross,
            "barrier_net_return": _net_return(entry_price, exit_price, cost, funding_return),
            "funding_return": funding_return,
            "funding_payments": funding_count,
            "ambiguous_bar_count": ambiguous_bars,
        }
    exit_price = float(frame["close"].iloc[final_index])
    exit_ms = int(frame["close_time_ms"].iloc[final_index])
    funding_return, funding_count = _funding_adjustment(
        funding,
        int(frame["open_time_ms"].iloc[entry_index]),
        exit_ms,
        enabled=settings.funding_enabled,
    )
    cost = settings.fee_rate + settings.slippage_rate
    return {
        "barrier_status": "neither",
        "barrier_resolution": "max_holding_close",
        "barrier_exit_index": final_index,
        "barrier_exit_time_ms": exit_ms,
        "barrier_exit_price": exit_price,
        "barrier_gross_return": exit_price / entry_price - 1.0,
        "barrier_net_return": _net_return(entry_price, exit_price, cost, funding_return),
        "funding_return": funding_return,
        "funding_payments": funding_count,
        "ambiguous_bar_count": ambiguous_bars,
    }


def run_event_study(
    frame: pd.DataFrame,
    contract: dict[str, Any],
    *,
    backend: str,
    funding: pd.DataFrame | None,
    detail_resolver: Callable[[int, float, float], str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = validate_reference_contract(contract)
    ema = ema_series(frame["close"], settings.ema_length, backend=backend)
    candidates = np.flatnonzero(candidate_mask(frame, ema, settings).to_numpy())
    regimes = _daily_regimes(frame)
    split_labels = _split_labels(len(frame), settings)
    rows: list[dict[str, Any]] = []
    overlap_skipped = 0
    cooldown_skipped = 0
    incomplete_skipped = 0
    last_signal_index = -10**12
    active_until = -1
    max_horizon = max(max(settings.horizons), settings.max_holding_bars)
    for signal_index in candidates:
        entry_index = int(signal_index) + 1
        if entry_index + max_horizon - 1 >= len(frame):
            incomplete_skipped += 1
            continue
        if entry_index <= active_until:
            overlap_skipped += 1
            continue
        if signal_index <= last_signal_index + settings.cooldown_bars:
            cooldown_skipped += 1
            continue
        split = str(split_labels[signal_index])
        signal_time = frame.index[signal_index]
        base = {
            "signal_index": int(signal_index),
            "signal_time_utc": signal_time.isoformat(),
            "signal_time_ms": int(frame["open_time_ms"].iloc[signal_index]),
            "entry_index": entry_index,
            "entry_time_utc": frame.index[entry_index].isoformat(),
            "entry_time_ms": int(frame["open_time_ms"].iloc[entry_index]),
            "entry_price": float(frame["open"].iloc[entry_index]),
            "ema": float(ema.iloc[signal_index]),
            "split": split,
            "holdout_hidden": bool(settings.holdout_locked and split == "final_holdout"),
            "subperiod": f"{signal_time.year}-H{1 if signal_time.month <= 6 else 2}",
            "regime": str(regimes.get(signal_time.floor("1D"), "Unknown")),
        }
        if base["holdout_hidden"]:
            rows.append(base)
            last_signal_index = int(signal_index)
            active_until = entry_index + settings.max_holding_bars - 1
            continue
        barrier = _resolve_barrier(
            frame,
            entry_index,
            settings,
            funding=funding,
            detail_resolver=detail_resolver,
        )
        base.update(barrier)
        cost = settings.fee_rate + settings.slippage_rate
        for horizon in settings.horizons:
            exit_index = entry_index + horizon - 1
            exit_price = float(frame["close"].iloc[exit_index])
            exit_ms = int(frame["close_time_ms"].iloc[exit_index])
            funding_return, funding_count = _funding_adjustment(
                funding,
                int(base["entry_time_ms"]),
                exit_ms,
                enabled=settings.funding_enabled,
            )
            base[f"forward_{horizon}_gross"] = exit_price / float(base["entry_price"]) - 1.0
            base[f"forward_{horizon}_net"] = _net_return(
                float(base["entry_price"]),
                exit_price,
                cost,
                funding_return,
            )
            base[f"forward_{horizon}_funding_payments"] = funding_count
        rows.append(base)
        last_signal_index = int(signal_index)
        active_until = int(barrier["barrier_exit_index"])

    events = pd.DataFrame(rows)
    summary = summarize_events(
        events,
        settings,
        candidate_count=len(candidates),
        overlap_skipped=overlap_skipped,
        cooldown_skipped=cooldown_skipped,
        incomplete_skipped=incomplete_skipped,
        backend=backend,
    )
    return events, summary


def _wilson_interval(successes: int, total: int, confidence: float) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denominator
    return center - margin, center + margin


def _bootstrap_interval(
    values: np.ndarray,
    *,
    confidence: float,
    samples: int,
    seed: int,
) -> dict[str, float | None]:
    clean = values[np.isfinite(values)]
    if clean.size == 0:
        return {"mean_low": None, "mean_high": None, "median_low": None, "median_high": None}
    rng = np.random.default_rng(seed)
    means = np.empty(samples)
    medians = np.empty(samples)
    batch = 200
    offset = 0
    while offset < samples:
        size = min(batch, samples - offset)
        sample = rng.choice(clean, size=(size, clean.size), replace=True)
        means[offset : offset + size] = sample.mean(axis=1)
        medians[offset : offset + size] = np.median(sample, axis=1)
        offset += size
    alpha = (1.0 - confidence) / 2.0
    return {
        "mean_low": float(np.quantile(means, alpha)),
        "mean_high": float(np.quantile(means, 1.0 - alpha)),
        "median_low": float(np.quantile(medians, alpha)),
        "median_high": float(np.quantile(medians, 1.0 - alpha)),
    }


def _group_summary(group: pd.DataFrame) -> dict[str, Any]:
    visible = group[~group["holdout_hidden"]].copy()
    if visible.empty:
        return {"event_count": 0, "resolved_event_count": 0}
    resolved = visible[visible["barrier_status"] != "unresolved"]
    successes = int((resolved["barrier_status"] == "target").sum())
    total = len(resolved)
    low, high = _wilson_interval(successes, total, 0.95)
    return {
        "event_count": int(len(visible)),
        "resolved_event_count": int(total),
        "target_count": successes,
        "historical_empirical_success_rate": successes / total if total else None,
        "success_rate_ci_95": [low, high],
    }


def summarize_events(
    events: pd.DataFrame,
    settings: EventStudySettings,
    *,
    candidate_count: int,
    overlap_skipped: int,
    cooldown_skipped: int,
    incomplete_skipped: int,
    backend: str,
) -> dict[str, Any]:
    if events.empty:
        visible = events
        hidden_count = 0
    else:
        visible = events[~events["holdout_hidden"]].copy()
        hidden_count = int(events["holdout_hidden"].sum())
    resolved = (
        visible[visible["barrier_status"] != "unresolved"]
        if not visible.empty
        else visible
    )
    target_count = int((resolved["barrier_status"] == "target").sum()) if not resolved.empty else 0
    resolved_count = int(len(resolved))
    low, high = _wilson_interval(target_count, resolved_count, settings.confidence_level)
    forward: dict[str, Any] = {}
    for offset, horizon in enumerate(settings.horizons):
        column = f"forward_{horizon}_net"
        values = visible[column].to_numpy(dtype=float) if column in visible else np.array([])
        clean = values[np.isfinite(values)]
        forward[str(horizon)] = {
            "sample_size": int(clean.size),
            "mean_net_return": float(clean.mean()) if clean.size else None,
            "median_net_return": float(np.median(clean)) if clean.size else None,
            "confidence_interval": _bootstrap_interval(
                clean,
                confidence=settings.confidence_level,
                samples=settings.bootstrap_samples,
                seed=settings.random_seed + offset,
            ),
        }
    regime_breakdown = (
        {str(name): _group_summary(group) for name, group in visible.groupby("regime")}
        if not visible.empty
        else {}
    )
    subperiod_breakdown = (
        {str(name): _group_summary(group) for name, group in visible.groupby("subperiod")}
        if not visible.empty
        else {}
    )
    barrier_counts = (
        {str(key): int(value) for key, value in visible["barrier_status"].value_counts().items()}
        if not visible.empty
        else {}
    )
    ambiguous_count = (
        int((visible["ambiguous_bar_count"] > 0).sum())
        if "ambiguous_bar_count" in visible
        else 0
    )
    return {
        "status": "passed",
        "engine": backend,
        "engine_version": REFERENCE_ENGINE_VERSION if backend == "vectorbt" else version("pandas"),
        "candidate_signal_count": int(candidate_count),
        "accepted_event_count": int(len(events)),
        "development_event_count": int(len(visible)),
        "final_holdout_event_count_hidden": hidden_count,
        "overlap_skipped_count": int(overlap_skipped),
        "cooldown_skipped_count": int(cooldown_skipped),
        "incomplete_outcome_skipped_count": int(incomplete_skipped),
        "ambiguous_event_count": ambiguous_count,
        "barrier_counts": barrier_counts,
        "resolved_event_count": resolved_count,
        "target_count": target_count,
        "historical_empirical_success_rate": target_count / resolved_count if resolved_count else None,
        "success_rate_confidence_interval": [low, high],
        "forward_returns": forward,
        "regime_breakdown": regime_breakdown,
        "subperiod_breakdown": subperiod_breakdown,
        "holdout_policy": (
            "final holdout outcomes hidden"
            if settings.holdout_locked
            else "final holdout visible"
        ),
    }


def run_manifest(
    *,
    contract_path: Path,
    data_manifest_path: Path,
    funding_manifest_path: Path,
    backend: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract_file": str(contract_path),
        "contract_sha256": _sha256_file(contract_path),
        "data_manifest_file": str(data_manifest_path),
        "data_manifest_sha256": _sha256_file(data_manifest_path),
        "funding_manifest_file": str(funding_manifest_path),
        "funding_manifest_sha256": _sha256_file(funding_manifest_path),
        "engine": backend,
        "engine_version": summary["engine_version"],
        "random_seed": 144,
        "numeric_precision": "IEEE-754 float64",
        "timezone": "UTC",
        "holdout_policy": summary["holdout_policy"],
    }
