"""Compare pinned VectorBT and pandas reference Phase 3 artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ARTIFACTS = Path("/artifacts")
REFERENCE = ARTIFACTS / "pandas"
VECTORBT = ARTIFACTS / "vectorbt"


def main() -> None:
    reference_summary = json.loads((REFERENCE / "summary.json").read_text(encoding="utf-8"))
    vectorbt_summary = json.loads((VECTORBT / "summary.json").read_text(encoding="utf-8"))
    exact_fields = (
        "candidate_signal_count",
        "accepted_event_count",
        "development_event_count",
        "final_holdout_event_count_hidden",
        "overlap_skipped_count",
        "cooldown_skipped_count",
        "incomplete_outcome_skipped_count",
        "ambiguous_event_count",
        "barrier_counts",
        "resolved_event_count",
        "target_count",
    )
    for field in exact_fields:
        if reference_summary[field] != vectorbt_summary[field]:
            raise AssertionError(
                f"Summary mismatch for {field}: "
                f"{reference_summary[field]!r} != {vectorbt_summary[field]!r}"
            )
    reference = pd.read_csv(REFERENCE / "events.csv")
    vectorbt = pd.read_csv(VECTORBT / "events.csv")
    exact_columns = (
        "signal_index",
        "signal_time_ms",
        "entry_index",
        "entry_time_ms",
        "split",
        "holdout_hidden",
        "barrier_status",
        "barrier_resolution",
    )
    if len(reference) != len(vectorbt):
        raise AssertionError(f"Event count mismatch: {len(reference)} != {len(vectorbt)}")
    for column in exact_columns:
        if not reference[column].fillna("<NA>").equals(vectorbt[column].fillna("<NA>")):
            raise AssertionError(f"Event column mismatch: {column}")
    numeric_columns = [
        column
        for column in reference.columns
        if column == "ema"
        or column.startswith("forward_")
        or column in {
            "entry_price",
            "barrier_exit_price",
            "barrier_gross_return",
            "barrier_net_return",
            "funding_return",
        }
    ]
    max_difference = 0.0
    for column in numeric_columns:
        left = pd.to_numeric(reference[column], errors="coerce").to_numpy(dtype=float)
        right = pd.to_numeric(vectorbt[column], errors="coerce").to_numpy(dtype=float)
        difference = np.abs(left - right)
        finite = difference[np.isfinite(difference)]
        if finite.size:
            max_difference = max(max_difference, float(finite.max()))
        np.testing.assert_allclose(left, right, rtol=1e-10, atol=1e-10, equal_nan=True)
    result = {
        "status": "passed",
        "event_count": len(reference),
        "exact_summary_fields": list(exact_fields),
        "exact_event_columns": list(exact_columns),
        "numeric_columns_compared": numeric_columns,
        "maximum_absolute_numeric_difference": max_difference,
    }
    (ARTIFACTS / "parity.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
