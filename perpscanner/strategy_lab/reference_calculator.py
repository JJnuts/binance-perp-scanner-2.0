"""Independent loop calculator for the frozen EMA-9 Phase 3 fixture."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from .event_study import EventStudySettings, validate_reference_contract


def recursive_ema(close: list[float], length: int) -> list[float | None]:
    alpha = 2.0 / (length + 1.0)
    running: float | None = None
    output: list[float | None] = []
    for index, value in enumerate(close):
        running = value if running is None else alpha * value + (1.0 - alpha) * running
        output.append(running if index >= length - 1 else None)
    return output


def reference_candidates(frame: pd.DataFrame, contract: dict[str, Any]) -> list[int]:
    settings = validate_reference_contract(contract)
    close = [float(value) for value in frame["close"]]
    low = [float(value) for value in frame["low"]]
    ema = recursive_ema(close, settings.ema_length)
    candidates: list[int] = []
    for index in range(settings.warmup_bars, len(frame)):
        average = ema[index]
        if average is None:
            continue
        prior_ok = True
        for shift in range(1, settings.precondition_bars + 1):
            prior_average = ema[index - shift] if index - shift >= 0 else None
            if prior_average is None or not close[index - shift] > prior_average:
                prior_ok = False
                break
        if not prior_ok:
            continue
        if low[index] < average * (1.0 - settings.max_penetration):
            continue
        if low[index] > average * (1.0 + settings.touch_tolerance):
            continue
        if close[index] <= average * (1.0 + settings.min_rejection):
            continue
        candidates.append(index)
    return candidates


def reference_barrier(
    frame: pd.DataFrame,
    entry_index: int,
    settings: EventStudySettings,
    *,
    detail_resolver: Callable[[int, float, float], str] | None = None,
) -> tuple[str, int]:
    entry = float(frame["open"].iloc[entry_index])
    target = entry * (1.0 + settings.target)
    stop = entry * (1.0 - settings.stop)
    last = min(entry_index + settings.max_holding_bars - 1, len(frame) - 1)
    for index in range(entry_index, last + 1):
        target_hit = float(frame["high"].iloc[index]) >= target
        stop_hit = float(frame["low"].iloc[index]) <= stop
        if target_hit and stop_hit:
            result = (
                detail_resolver(int(frame["open_time_ms"].iloc[index]), target, stop)
                if detail_resolver
                else "unresolved"
            )
            return result if result in {"target", "stop"} else "unresolved", index
        if target_hit:
            return "target", index
        if stop_hit:
            return "stop", index
    return "neither", last
