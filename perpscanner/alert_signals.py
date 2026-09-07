"""Pure selection of Discord-eligible best-setup signals.

This module deliberately performs no network, database, clock, or Discord
I/O.  Callers supply the successful scan frames and its observation time.
Lifecycle, persistence, scheduling, and delivery belong to later layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math

import pandas as pd

from .scoring import _build_best_setups


DEFAULT_ALERT_ENTRY_THRESHOLD = 75.0
DEFAULT_ALERT_CONTINUATION_THRESHOLD = 75.0
_ALERT_STATES = {
    "Long best setup": "LONG",
    "Short best setup": "SHORT",
}
_LTF_REQUIRED_COLUMNS = {
    "symbol",
    "tf_alignment_pass",
    "fresh_setup_pass",
    "ltf_direction",
    "ltf_ignition_score",
}
_HTF_REQUIRED_COLUMNS = {
    "symbol",
    "htf_expansion_direction",
    "htf_expansion_score",
    "htf_momentum_score",
    "htf_setup_score",
    "htf_atr_percentile",
    "htf_atr_roc",
    "htf_breakout_distance_atr",
    "daily_structure_score",
    "daily_long_confirmed",
    "daily_short_confirmed",
    "daily_volume_ratio",
    "daily_volume_persistence_days",
    "daily_oi_persistence_days",
    "daily_swing_high",
    "daily_swing_low",
    "btc_daily_regime",
    "btc_daily_regime_score",
    "rs_24h",
    "rs_72h",
}


class SignalSelectionError(ValueError):
    """A completed scan frame is not safe to evaluate for alerts."""


@dataclass(frozen=True)
class AlertSignal:
    """One current, directionally aligned setup evaluation."""

    symbol: str
    direction: str
    strength: int
    score: float
    observed_at_utc: datetime
    initial_eligible: bool
    continuation_eligible: bool

    @property
    def key(self) -> tuple[str, str]:
        return self.symbol, self.direction


def _validated_threshold(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number from 0 through 100")
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number from 0 through 100") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 100.0:
        raise ValueError(f"{name} must be a finite number from 0 through 100")
    return threshold


def _validated_observed_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at_utc must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _display_strength(score: float) -> int:
    """Round a valid non-negative setup score to a whole-number strength."""

    return int(min(100.0, max(1.0, math.floor(float(score) + 0.5))))


def _validate_scan_schema(ltf_df: pd.DataFrame, htf_df: pd.DataFrame) -> None:
    missing_ltf = sorted(_LTF_REQUIRED_COLUMNS - set(ltf_df.columns))
    missing_htf = sorted(_HTF_REQUIRED_COLUMNS - set(htf_df.columns))
    if missing_ltf or missing_htf:
        details = []
        if missing_ltf:
            details.append(f"LTF missing {', '.join(missing_ltf)}")
        if missing_htf:
            details.append(f"HTF missing {', '.join(missing_htf)}")
        raise SignalSelectionError("incomplete alert scan: " + "; ".join(details))


def _eligible_rows(frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    required = {"symbol", "best_setup_state", "best_setup_score"}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame(columns=["symbol", "best_setup_state", "best_setup_score"])

    rows = frame.loc[frame["best_setup_state"].isin(_ALERT_STATES)].copy()
    numeric_score = pd.to_numeric(rows["best_setup_score"], errors="coerce")
    finite_score = numeric_score.map(lambda value: bool(pd.notna(value) and math.isfinite(float(value))))
    rows = rows.loc[finite_score & numeric_score.ge(threshold)].copy()
    rows.loc[:, "best_setup_score"] = numeric_score.loc[rows.index]
    return rows


def select_alert_signals(
    ltf_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    observed_at_utc: datetime,
    *,
    entry_threshold: float = DEFAULT_ALERT_ENTRY_THRESHOLD,
    continuation_threshold: float = DEFAULT_ALERT_CONTINUATION_THRESHOLD,
) -> tuple[AlertSignal, ...]:
    """Return current initial and/or continuation-eligible setup signals.

    Initial eligibility retains every existing Best Setups entry gate,
    including trigger freshness.  Continuation eligibility uses the same
    formula and directional structure without requiring the original trigger
    to remain fresh.  Only exact Long/Short best-setup states are returned.
    """

    entry_threshold = _validated_threshold(entry_threshold, "entry_threshold")
    continuation_threshold = _validated_threshold(continuation_threshold, "continuation_threshold")
    observed_at = _validated_observed_at(observed_at_utc)

    if ltf_df.empty and htf_df.empty:
        return ()
    if ltf_df.empty or htf_df.empty:
        empty_side = "LTF" if ltf_df.empty else "HTF"
        raise SignalSelectionError(f"incomplete alert scan: {empty_side} frame is empty")
    _validate_scan_schema(ltf_df, htf_df)

    initial = _eligible_rows(
        _build_best_setups(ltf_df, htf_df, require_fresh=True),
        entry_threshold,
    )
    continuing = _eligible_rows(
        _build_best_setups(ltf_df, htf_df, require_fresh=False),
        continuation_threshold,
    )

    initial_keys = {
        (str(row.symbol), _ALERT_STATES[str(row.best_setup_state)])
        for row in initial.itertuples(index=False)
    }
    continuing_by_key = {
        (str(row.symbol), _ALERT_STATES[str(row.best_setup_state)]): row
        for row in continuing.itertuples(index=False)
    }
    initial_by_key = {
        (str(row.symbol), _ALERT_STATES[str(row.best_setup_state)]): row
        for row in initial.itertuples(index=False)
    }

    signals: list[AlertSignal] = []
    for key in sorted(set(initial_by_key) | set(continuing_by_key)):
        row = continuing_by_key.get(key, initial_by_key.get(key))
        score = float(row.best_setup_score)
        signals.append(
            AlertSignal(
                symbol=key[0],
                direction=key[1],
                strength=_display_strength(score),
                score=score,
                observed_at_utc=observed_at,
                initial_eligible=key in initial_keys,
                continuation_eligible=key in continuing_by_key,
            )
        )

    return tuple(sorted(signals, key=lambda item: (-item.score, item.symbol, item.direction)))
