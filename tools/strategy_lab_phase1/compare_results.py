import json
import math
import os
from pathlib import Path


ROOT = Path(os.environ.get("PHASE1_ROOT", "/workspace"))
ARTIFACTS = ROOT / "artifacts"
RUN_IDS = ("run1", "run2")
COMMON_FIELDS = (
    "fixture_sha256",
    "trade_count",
    "event_timestamp",
    "entry_timestamp",
    "exit_timestamp",
    "entry_price",
    "exit_price",
    "canonical_gross_return",
    "canonical_net_return",
)


def load(engine: str, run_id: str) -> dict:
    path = ARTIFACTS / f"{engine}-{run_id}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def assert_equal(left, right, label: str) -> None:
    if isinstance(left, float) or isinstance(right, float):
        if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12):
            raise AssertionError(f"{label}: {left!r} != {right!r}")
    elif left != right:
        raise AssertionError(f"{label}: {left!r} != {right!r}")


results = {
    engine: {run_id: load(engine, run_id) for run_id in RUN_IDS}
    for engine in ("vectorbt", "freqtrade")
}

for engine, engine_runs in results.items():
    for field in engine_runs["run1"]:
        if field == "run_id":
            continue
        assert_equal(
            engine_runs["run1"][field],
            engine_runs["run2"][field],
            f"{engine} repeated run field {field}",
        )

for field in COMMON_FIELDS:
    assert_equal(
        results["vectorbt"]["run1"][field],
        results["freqtrade"]["run1"][field],
        f"cross-engine field {field}",
    )

comparison = {
    "status": "passed",
    "repeated_runs": 2,
    "common_fields_compared": list(COMMON_FIELDS),
    "native_return_note": (
        "VectorBT native trade return divides net PnL by entry value before fees. "
        "Freqtrade profit_ratio divides by entry value including entry fees. "
        "Canonical net return normalizes both engines to the Freqtrade denominator."
    ),
    "vectorbt_native_return": results["vectorbt"]["run1"]["native_trade_return"],
    "freqtrade_native_return": results["freqtrade"]["run1"]["native_trade_return"],
}

with (ARTIFACTS / "reproducibility.json").open("w", encoding="utf-8") as handle:
    json.dump(comparison, handle, indent=2, sort_keys=True)
    handle.write("\n")

print(json.dumps(comparison, indent=2, sort_keys=True))
