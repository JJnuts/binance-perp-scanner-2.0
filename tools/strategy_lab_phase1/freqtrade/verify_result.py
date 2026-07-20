import hashlib
import json
import math
import os
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile


PHASE1 = Path(os.environ.get("PHASE1_ROOT", "/phase1"))
ARTIFACTS = PHASE1 / "artifacts"
USER_DATA = Path(os.environ.get("FREQTRADE_USER_DATA", "/freqtrade/user_data"))
RESULTS = USER_DATA / "backtest_results"
EXPECTED = json.loads(
    (PHASE1 / "fixture" / "expected.json").read_text(encoding="utf-8")
)
FIXTURE = PHASE1 / "fixture" / "ema3_bounce_5m.csv"
RUN_ID = os.environ.get("PHASE1_RUN_ID", "manual")


def assert_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-8):
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


if version("freqtrade") != "2026.6":
    raise AssertionError(f"Unexpected Freqtrade version: {version('freqtrade')}")

fixture_sha256 = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
if fixture_sha256 != EXPECTED["fixture_sha256"]:
    raise AssertionError(f"Fixture checksum changed: {fixture_sha256}")

latest = json.loads((RESULTS / ".last_result.json").read_text(encoding="utf-8"))
zip_path = RESULTS / latest["latest_backtest"]
with ZipFile(zip_path) as archive:
    result_names = [
        name
        for name in archive.namelist()
        if name.endswith(".json") and not name.endswith("_config.json")
    ]
    if len(result_names) != 1:
        raise AssertionError(f"Expected one result JSON in {zip_path.name}: {result_names}")
    payload = json.loads(archive.read(result_names[0]))

strategy = payload["strategy"]["Phase1FixtureStrategy"]
trades = strategy["trades"]
if len(trades) != 1:
    raise AssertionError(f"Expected one Freqtrade trade, got {len(trades)}")

trade = trades[0]
entry_timestamp = datetime.fromtimestamp(
    trade["open_timestamp"] / 1000,
    tz=timezone.utc,
).isoformat()
exit_timestamp = datetime.fromtimestamp(
    trade["close_timestamp"] / 1000,
    tz=timezone.utc,
).isoformat()
event_timestamp = datetime.fromtimestamp(
    (trade["open_timestamp"] - 5 * 60 * 1000) / 1000,
    tz=timezone.utc,
).isoformat()

entry_price = float(trade["open_rate"])
exit_price = float(trade["close_rate"])
fee = float(EXPECTED["fee_rate_per_side"])
gross_return = exit_price / entry_price - 1.0
canonical_net_return = (exit_price * (1.0 - fee)) / (
    entry_price * (1.0 + fee)
) - 1.0

if event_timestamp != EXPECTED["event_timestamp"]:
    raise AssertionError(f"Unexpected event timestamp: {event_timestamp}")
if entry_timestamp != EXPECTED["entry_timestamp"]:
    raise AssertionError(f"Unexpected entry timestamp: {entry_timestamp}")
if exit_timestamp != EXPECTED["exit_timestamp"]:
    raise AssertionError(f"Unexpected exit timestamp: {exit_timestamp}")
if trade["enter_tag"] != "phase1_ema3_bounce":
    raise AssertionError(f"Unexpected enter tag: {trade['enter_tag']}")
if trade["exit_reason"] != "exit_signal":
    raise AssertionError(f"Unexpected exit reason: {trade['exit_reason']}")
if int(trade["trade_duration"]) != 10:
    raise AssertionError(f"Unexpected trade duration: {trade['trade_duration']}")

assert_close(entry_price, EXPECTED["entry_price"], "entry price")
assert_close(exit_price, EXPECTED["exit_price"], "exit price")
assert_close(gross_return, EXPECTED["canonical_gross_return"], "gross return")
assert_close(canonical_net_return, EXPECTED["canonical_net_return"], "canonical net return")
assert_close(
    float(trade["profit_ratio"]),
    EXPECTED["freqtrade_native_trade_return"],
    "Freqtrade native trade return",
)

result = {
    "engine": "freqtrade",
    "engine_version": version("freqtrade"),
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
    "native_trade_return": float(trade["profit_ratio"]),
}

ARTIFACTS.mkdir(parents=True, exist_ok=True)
(ARTIFACTS / f"freqtrade-{RUN_ID}.json").write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(result, indent=2, sort_keys=True))
