"""Verify Freqtrade lifecycle and reconcile the Phase 4 cost ledger."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from zipfile import ZipFile


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.binance_data import _atomic_write, _pretty_json_bytes  # noqa: E402
from perpscanner.strategy_lab.freqtrade_adapter import (  # noqa: E402
    load_strategy_contract,
    reconcile_trade_costs,
)


PHASE4 = Path(os.environ.get("PHASE4_ROOT", "/phase4"))
USER_DATA = Path(os.environ.get("FREQTRADE_USER_DATA", "/freqtrade/user_data"))
ARTIFACTS = Path(os.environ.get("PHASE4_ARTIFACTS", "/phase4/artifacts"))
RESULTS = USER_DATA / "backtest_results"
EXPECTED = json.loads((PHASE4 / "fixture" / "expected.json").read_text(encoding="utf-8"))
CONTRACT = load_strategy_contract(PHASE4 / "compiled" / "confirmed-contract.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


manifest = json.loads(
    (PHASE4 / "compiled" / "compile-manifest.json").read_text(encoding="utf-8")
)
runtime_inputs = {
    "strategy_sha256": sha256_file(
        USER_DATA
        / "strategies"
        / "ApprovedEma9FixedHoldStrategy.py"
    ),
    "config_sha256": sha256_file(USER_DATA / "config.json"),
    "contract_sha256": sha256_file(
        PHASE4 / "compiled" / "confirmed-contract.json"
    ),
    "worker_spec_sha256": sha256_file(PHASE4 / "compiled" / "worker-spec.json"),
}
for field, actual_hash in runtime_inputs.items():
    if actual_hash != manifest[field]:
        raise AssertionError(
            f"Runtime {field} differs from compiled manifest: "
            f"{actual_hash} != {manifest[field]}"
        )


latest = json.loads((RESULTS / ".last_result.json").read_text(encoding="utf-8"))
zip_path = RESULTS / latest["latest_backtest"]
with ZipFile(zip_path) as archive:
    result_names = [
        name
        for name in archive.namelist()
        if name.endswith(".json") and not name.endswith("_config.json")
    ]
    if len(result_names) != 1:
        raise AssertionError(f"Expected one result JSON: {result_names}")
    payload = json.loads(archive.read(result_names[0]))

strategy = payload["strategy"]["ApprovedEma9FixedHoldStrategy"]
trades = strategy["trades"]
if len(trades) != len(EXPECTED["expected_trades"]):
    raise AssertionError(f"Expected two trades, got {len(trades)}")

funding_rates = [
    {
        "funding_time_ms": EXPECTED["funding_timestamp"],
        "funding_rate": EXPECTED["expected_trades"][0]["funding_rate"],
        "mark_price": EXPECTED["expected_trades"][0]["funding_mark_price"],
    }
]
ledger = []
for actual, expected in zip(trades, EXPECTED["expected_trades"]):
    for field in ("open_timestamp", "close_timestamp"):
        if int(actual[field]) != int(expected[field]):
            raise AssertionError(f"{field}: {actual[field]} != {expected[field]}")
    for field in ("open_rate", "close_rate"):
        if abs(float(actual[field]) - float(expected[field])) > 1e-10:
            raise AssertionError(f"{field}: {actual[field]} != {expected[field]}")
    if int(actual["trade_duration"]) != int(expected["trade_duration_minutes"]):
        raise AssertionError(
            f"trade_duration: {actual['trade_duration']} != "
            f"{expected['trade_duration_minutes']}"
        )
    if actual["enter_tag"] != "approved_ema9_bounce":
        raise AssertionError(f"Unexpected entry tag: {actual['enter_tag']}")
    if actual["exit_reason"] != "fixed_12_bar_horizon":
        raise AssertionError(f"Unexpected exit reason: {actual['exit_reason']}")
    normalized = reconcile_trade_costs(actual, CONTRACT, funding_rates)
    if normalized["funding_payment_count"] != expected["funding_payment_count"]:
        raise AssertionError(
            f"Funding count: {normalized['funding_payment_count']} != "
            f"{expected['funding_payment_count']}"
        )
    if abs(float(actual["funding_fees"]) - normalized["funding_profit_abs"]) > 1e-8:
        raise AssertionError(
            f"Funding fees: {actual['funding_fees']} != "
            f"{normalized['funding_profit_abs']}"
        )
    if (
        abs(
            float(actual["profit_ratio"])
            - normalized["fee_and_funding_return"]
        )
        > 1e-8
    ):
        raise AssertionError(
            f"Native return: {actual['profit_ratio']} != "
            f"{normalized['fee_and_funding_return']}"
        )
    if (
        abs(
            float(actual["profit_abs"])
            - normalized["fee_and_funding_profit_abs"]
        )
        > 1e-8
    ):
        raise AssertionError("Native absolute profit does not reconcile")
    normalized["freqtrade_native_profit_ratio"] = float(actual["profit_ratio"])
    normalized["freqtrade_native_profit_abs"] = float(actual["profit_abs"])
    ledger.append(normalized)

result = {
    "status": "passed",
    "engine": "freqtrade",
    "engine_version": "2026.6",
    "trade_count": len(trades),
    "runtime_input_hashes": runtime_inputs,
    "overlap_skipped_signal_indices": EXPECTED["overlap_skipped_signal_indices"],
    "ledger": ledger,
    "native_summary": {
        "profit_total": strategy["profit_total"],
        "profit_total_abs": strategy["profit_total_abs"],
        "stake_currency": strategy["stake_currency"],
    },
}
ARTIFACTS.mkdir(parents=True, exist_ok=True)
_atomic_write(ARTIFACTS / "verified-result.json", _pretty_json_bytes(result))
print(json.dumps(result, indent=2, sort_keys=True))
