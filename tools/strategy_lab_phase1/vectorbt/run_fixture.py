import hashlib
import json
import math
import os
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt


ROOT = Path(os.environ.get("PHASE1_ROOT", "/workspace"))
FIXTURE = ROOT / "fixture" / "ema3_bounce_5m.csv"
EXPECTED_PATH = ROOT / "fixture" / "expected.json"
ARTIFACTS = ROOT / "artifacts"
RUN_ID = os.environ.get("PHASE1_RUN_ID", "manual")


def assert_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


if vbt.__version__ != "0.28.5":
    raise AssertionError(f"Unexpected VectorBT version: {vbt.__version__}")

fixture_bytes = FIXTURE.read_bytes()
fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
if fixture_sha256 != expected["fixture_sha256"]:
    raise AssertionError(f"Fixture checksum changed: {fixture_sha256}")

frame = pd.read_csv(FIXTURE, parse_dates=["date"])
frame["date"] = pd.to_datetime(frame["date"], utc=True)
frame = frame.set_index("date")

if len(frame) != expected["rows"]:
    raise AssertionError(f"Unexpected fixture rows: {len(frame)}")
if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
    raise AssertionError("Fixture timestamps must be ordered and unique")

ema = vbt.MA.run(
    frame["close"],
    window=expected["ema_length"],
    ewm=True,
    adjust=expected["ema_adjust"],
).ma
reference_ema = frame["close"].ewm(
    span=expected["ema_length"],
    adjust=expected["ema_adjust"],
    min_periods=expected["ema_min_periods"],
).mean()
np.testing.assert_allclose(
    ema.to_numpy(),
    reference_ema.to_numpy(),
    rtol=0.0,
    atol=1e-12,
    equal_nan=True,
)

precondition = pd.Series(True, index=frame.index)
for shift in range(1, expected["precondition_bars"] + 1):
    precondition &= frame["close"].shift(shift) > ema.shift(shift)

bounce = (
    precondition
    & (frame["low"] <= ema)
    & (frame["close"] > ema)
).fillna(False)
entries = bounce.shift(1, fill_value=False)
exits = bounce.shift(3, fill_value=False)

event_times = list(frame.index[bounce])
entry_times = list(frame.index[entries])
exit_times = list(frame.index[exits])
if len(event_times) != 1 or len(entry_times) != 1 or len(exit_times) != 1:
    raise AssertionError(
        f"Expected one event/entry/exit, got {len(event_times)}/"
        f"{len(entry_times)}/{len(exit_times)}"
    )

portfolio = vbt.Portfolio.from_signals(
    frame["open"],
    entries=entries,
    exits=exits,
    init_cash=1000.0,
    size=100.0,
    size_type="value",
    fees=expected["fee_rate_per_side"],
    direction="longonly",
    freq="5min",
)
trades = portfolio.trades.records_readable
orders = portfolio.orders.records_readable
if len(trades) != 1 or len(orders) != 2:
    raise AssertionError(f"Expected one trade and two orders, got {len(trades)} and {len(orders)}")

trade = trades.iloc[0]
entry_price = float(trade["Avg Entry Price"])
exit_price = float(trade["Avg Exit Price"])
fee = float(expected["fee_rate_per_side"])
gross_return = exit_price / entry_price - 1.0
canonical_net_return = (exit_price * (1.0 - fee)) / (
    entry_price * (1.0 + fee)
) - 1.0

event_timestamp = event_times[0].isoformat()
entry_timestamp = entry_times[0].isoformat()
exit_timestamp = exit_times[0].isoformat()

if event_timestamp != expected["event_timestamp"]:
    raise AssertionError(f"Unexpected event timestamp: {event_timestamp}")
if entry_timestamp != expected["entry_timestamp"]:
    raise AssertionError(f"Unexpected entry timestamp: {entry_timestamp}")
if exit_timestamp != expected["exit_timestamp"]:
    raise AssertionError(f"Unexpected exit timestamp: {exit_timestamp}")

assert_close(float(ema.loc[event_times[0]]), expected["event_ema"], "event EMA")
assert_close(entry_price, expected["entry_price"], "entry price")
assert_close(exit_price, expected["exit_price"], "exit price")
assert_close(gross_return, expected["canonical_gross_return"], "gross return")
assert_close(canonical_net_return, expected["canonical_net_return"], "canonical net return")
assert_close(
    float(trade["Return"]),
    expected["vectorbt_native_trade_return"],
    "VectorBT native trade return",
)

result = {
    "engine": "vectorbt",
    "engine_version": vbt.__version__,
    "run_id": RUN_ID,
    "fixture_sha256": fixture_sha256,
    "trade_count": 1,
    "event_timestamp": event_timestamp,
    "entry_timestamp": entry_timestamp,
    "exit_timestamp": exit_timestamp,
    "entry_price": entry_price,
    "exit_price": exit_price,
    "canonical_gross_return": gross_return,
    "canonical_net_return": canonical_net_return,
    "native_trade_return": float(trade["Return"]),
    "numpy_version": version("numpy"),
    "pandas_version": version("pandas"),
}

ARTIFACTS.mkdir(parents=True, exist_ok=True)
(ARTIFACTS / f"vectorbt-{RUN_ID}.json").write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
freeze_path = Path("/opt/strategy-lab/vectorbt-freeze.txt")
if freeze_path.is_file():
    (ARTIFACTS / "vectorbt-freeze.txt").write_text(
        freeze_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
print(json.dumps(result, indent=2, sort_keys=True))
