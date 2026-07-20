"""Allowlisted Phase 4 Freqtrade compiler, ledger, and worker state."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .binance_data import _atomic_write, _pretty_json_bytes


APPROVED_EXPERIMENT_ID = "btcusdt-5m-ema9-fixed-hold-v1"
APPROVED_STRATEGY_CLASS = "ApprovedEma9FixedHoldStrategy"
FREQTRADE_IMAGE = "freqtradeorg/freqtrade:2026.6"
JOB_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})$")
APPROVED_WORKER_SERVICE = "freqtrade-backtest"
APPROVED_STRATEGY_SHA256 = (
    "94d4736c90993405ff1d8830fcc32b78cc99867ca603d550cd34151282595afc"
)
APPROVED_CONTRACT_SEMANTIC_SHA256 = (
    "f2fb5653483660187b08987c60a8ca44fb1457268e18243cbdd7395e7c1b65b4"
)
APPROVED_BTC_AMOUNT_STEP = 0.001


class AdapterCapabilityError(ValueError):
    """Raised when a contract is outside the Phase 4 allowlist."""


@dataclass(frozen=True)
class CompiledStrategy:
    output_dir: Path
    contract_path: Path
    config_path: Path
    manifest_path: Path
    worker_spec_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_strategy_contract(
    contract_path: Path | str,
    schema_path: Path | str | None = None,
) -> dict[str, Any]:
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    if schema_path is not None:
        from jsonschema import Draft202012Validator, FormatChecker

        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(contract)
    validate_strategy_contract(contract)
    return contract


def validate_strategy_contract(contract: dict[str, Any]) -> None:
    checks = {
        "experiment.id": (contract["experiment"]["id"], APPROVED_EXPERIMENT_ID),
        "research.type": (contract["research"]["type"], "strategy_backtest"),
        "research.engine": (contract["research"]["engine"], "freqtrade"),
        "market.exchange": (contract["market"]["exchange"], "binance"),
        "market.venue": (contract["market"]["venue"], "usd_m_futures"),
        "market.symbols": (contract["market"]["symbols"], ["BTCUSDT"]),
        "market.direction": (contract["market"]["direction"], "long"),
        "data.timeframe": (contract["data"]["timeframe"], "5m"),
        "data.timezone": (contract["data"]["timezone"], "UTC"),
        "data.bar_policy": (contract["data"]["bar_policy"], "closed_only"),
        "signal.indicator": (
            contract["signal"]["indicator"],
            {"kind": "ema", "length": 9, "price_source": "close"},
        ),
        "signal.context_filters": (contract["signal"]["context_filters"], []),
        "signal.event.type": (
            contract["signal"]["event"]["type"],
            "moving_average_bounce",
        ),
        "signal.event.approach": (
            contract["signal"]["event"]["approach"],
            "from_above",
        ),
        "entry.timing": (contract["entry"]["timing"], "next_bar_open"),
        "entry.price": (contract["entry"]["price"], "open"),
        "entry.overlapping_positions": (
            contract["entry"]["overlapping_positions"],
            "skip",
        ),
        "outcome.mode": (contract["outcome"]["mode"], "forward_return"),
        "outcome.forward_horizons_bars": (
            contract["outcome"]["forward_horizons_bars"],
            [12],
        ),
        "position_sizing.model": (
            contract["execution"]["position_sizing"]["model"],
            "fixed_notional",
        ),
        "funding.include": (contract["execution"]["funding"]["include"], True),
    }
    mismatches = {
        key: {"actual": actual, "expected": expected}
        for key, (actual, expected) in checks.items()
        if actual != expected
    }
    if mismatches:
        raise AdapterCapabilityError(
            f"Phase 4 accepts only the approved fixed strategy: {mismatches}"
        )
    event = contract["signal"]["event"]
    expected_event = {
        "precondition_bars": 3,
        "probe_field": "low",
        "touch_tolerance_bps": 5.0,
        "max_penetration_bps": 15.0,
        "confirmation": "close_above_average",
        "min_rejection_bps": 0.0,
    }
    for key, expected in expected_event.items():
        if event[key] != expected:
            raise AdapterCapabilityError(f"Unsupported signal parameter {key}: {event[key]}")
    if int(contract["entry"]["cooldown_bars"]) != 12:
        raise AdapterCapabilityError("The approved strategy uses a fixed 12-bar cooldown")
    if not math.isclose(
        float(contract["execution"]["position_sizing"]["value"]),
        1000.0,
        abs_tol=1e-12,
    ):
        raise AdapterCapabilityError("The approved strategy uses fixed 1000 USDT notional")
    semantic_payload = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if hashlib.sha256(semantic_payload).hexdigest() != APPROVED_CONTRACT_SEMANTIC_SHA256:
        raise AdapterCapabilityError(
            "The compiler accepts only the exact reviewed Phase 4 contract"
        )


def _safe_config(contract: dict[str, Any]) -> dict[str, Any]:
    stake = float(contract["execution"]["position_sizing"]["value"])
    fee = float(contract["execution"]["fee_bps_per_side"]) / 10_000.0
    return {
        "$schema": "https://schema.freqtrade.io/schema.json",
        "max_open_trades": 1,
        "stake_currency": "USDT",
        "stake_amount": stake,
        "tradable_balance_ratio": 0.99,
        "fiat_display_currency": "USD",
        "dry_run": True,
        "dry_run_wallet": stake * 2.0,
        "cancel_open_orders_on_exit": False,
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "liquidation_buffer": 0.05,
        "fee": fee,
        "unfilledtimeout": {
            "entry": 10,
            "exit": 10,
            "exit_timeout_count": 0,
            "unit": "minutes",
        },
        "entry_pricing": {
            "price_side": "other",
            "use_order_book": True,
            "order_book_top": 1,
            "price_last_balance": 0.0,
            "check_depth_of_market": {
                "enabled": False,
                "bids_to_ask_delta": 1,
            },
        },
        "exit_pricing": {
            "price_side": "other",
            "use_order_book": True,
            "order_book_top": 1,
        },
        "exchange": {
            "name": "binance",
            "key": "",
            "secret": "",
            "ccxt_config": {},
            "ccxt_async_config": {},
            "pair_whitelist": ["BTC/USDT:USDT"],
            "pair_blacklist": [],
        },
        "pairlists": [{"method": "StaticPairList"}],
        "dataformat_ohlcv": "json",
        "dataformat_trades": "jsongz",
        "bot_name": "strategy-lab-phase4",
        "initial_state": "stopped",
        "force_entry_enable": False,
        "internals": {"process_throttle_secs": 5},
    }


def compile_strategy_contract(
    *,
    contract_path: Path | str,
    schema_path: Path | str,
    strategy_path: Path | str,
    output_dir: Path | str,
) -> CompiledStrategy:
    source_contract = Path(contract_path).resolve()
    source_strategy = Path(strategy_path).resolve()
    output = Path(output_dir).resolve()
    if (
        source_strategy.name != f"{APPROVED_STRATEGY_CLASS}.py"
        or _sha256_file(source_strategy) != APPROVED_STRATEGY_SHA256
    ):
        raise AdapterCapabilityError(
            "The compiler accepts only the reviewed static Phase 4 strategy"
        )
    contract = load_strategy_contract(source_contract, schema_path)
    config = _safe_config(contract)
    prohibited = {"telegram", "api_server"}
    if prohibited & set(config):
        raise AssertionError("Live RPC configuration is prohibited")
    if not config["dry_run"] or config["exchange"]["key"] or config["exchange"]["secret"]:
        raise AssertionError("Compiled Freqtrade config crossed the non-live boundary")
    output.mkdir(parents=True, exist_ok=True)
    compiled_contract = output / "confirmed-contract.json"
    config_path = output / "config.json"
    worker_spec_path = output / "worker-spec.json"
    manifest_path = output / "compile-manifest.json"
    _atomic_write(compiled_contract, _pretty_json_bytes(contract))
    _atomic_write(config_path, _pretty_json_bytes(config))
    worker_spec = {
        "version": 1,
        "image": FREQTRADE_IMAGE,
        "strategy_class": APPROVED_STRATEGY_CLASS,
        "allowed_command": "backtesting",
        "timeframe": "5m",
        "hold_bars": 12,
        "fee_rate_per_side": float(contract["execution"]["fee_bps_per_side"]) / 10_000.0,
        "slippage_rate_per_side": float(contract["execution"]["slippage_bps_per_side"]) / 10_000.0,
        "fixed_notional": float(contract["execution"]["position_sizing"]["value"]),
        "funding_source": contract["execution"]["funding"]["source"],
        "network_policy": "public_binance_metadata_only",
        "live_trading": False,
    }
    _atomic_write(worker_spec_path, _pretty_json_bytes(worker_spec))
    manifest = {
        "compiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "compiler": "allowlisted-static-strategy-v1",
        "approved_contract_semantic_sha256": APPROVED_CONTRACT_SEMANTIC_SHA256,
        "contract_sha256": _sha256_file(compiled_contract),
        "schema_sha256": _sha256_file(Path(schema_path)),
        "strategy_file": str(source_strategy),
        "strategy_sha256": _sha256_file(source_strategy),
        "strategy_generated": False,
        "config_sha256": _sha256_file(config_path),
        "worker_spec_sha256": _sha256_file(worker_spec_path),
        "prohibited_commands": ["trade", "webserver", "hyperopt"],
        "live_trading": False,
    }
    _atomic_write(manifest_path, _pretty_json_bytes(manifest))
    return CompiledStrategy(
        output_dir=output,
        contract_path=compiled_contract,
        config_path=config_path,
        manifest_path=manifest_path,
        worker_spec_path=worker_spec_path,
    )


def reconcile_trade_costs(
    trade: dict[str, Any],
    contract: dict[str, Any],
    funding_rates: list[dict[str, Any]],
) -> dict[str, Any]:
    entry = float(trade["open_rate"])
    exit_price = float(trade["close_rate"])
    stake = float(trade["stake_amount"])
    expected_stake = float(contract["execution"]["position_sizing"]["value"])
    sizing_drift = stake - expected_stake
    leverage = float(trade.get("leverage", 1.0))
    amount = float(trade.get("amount", stake * leverage / entry))
    reconstructed_stake = amount * entry / leverage
    if not math.isclose(stake, reconstructed_stake, rel_tol=0.0, abs_tol=1e-8):
        raise AssertionError(
            f"Stake does not reconcile to amount and price: "
            f"{stake} != {reconstructed_stake}"
        )
    sizing_shortfall = expected_stake - stake
    maximum_precision_shortfall = entry * APPROVED_BTC_AMOUNT_STEP / leverage
    if (
        sizing_shortfall < -1e-8
        or sizing_shortfall >= maximum_precision_shortfall + 1e-8
    ):
        raise AssertionError(
            f"Stake differs by more than one BTC amount step: "
            f"{stake} != {expected_stake}"
        )
    fee = float(contract["execution"]["fee_bps_per_side"]) / 10_000.0
    slippage = float(contract["execution"]["slippage_bps_per_side"]) / 10_000.0
    open_ms = int(trade["open_timestamp"])
    close_ms = int(trade["close_timestamp"])
    selected_funding = [
        {
            "rate": float(item["funding_rate"]),
            "mark_price": float(item["mark_price"]),
        }
        for item in funding_rates
        if open_ms < int(item["funding_time_ms"]) <= close_ms
    ]
    if trade.get("is_short"):
        raise AssertionError("The approved Phase 4 contract is long-only")
    funding_profit_abs = -sum(
        amount * item["mark_price"] * item["rate"]
        for item in selected_funding
    )
    funding_return = funding_profit_abs / stake
    funding_return_on_entry_cost = funding_profit_abs / (stake * (1.0 + fee))
    slipped_entry = entry * (1.0 + slippage)
    slipped_funding_return_on_notional = -sum(
        item["mark_price"] / slipped_entry * item["rate"]
        for item in selected_funding
    )
    slipped_funding_return = slipped_funding_return_on_notional / (1.0 + fee)
    gross_return = exit_price / entry - 1.0
    fee_funding_profit_abs = (
        amount
        * (
            exit_price * (1.0 - fee)
            - entry * (1.0 + fee)
        )
        + funding_profit_abs
    )
    fee_funding_return = (
        (exit_price * (1.0 - fee)) / (entry * (1.0 + fee))
        - 1.0
        + funding_return_on_entry_cost
    )
    all_cost_return = (
        (exit_price * (1.0 - slippage) * (1.0 - fee))
        / (entry * (1.0 + slippage) * (1.0 + fee))
        - 1.0
        + slipped_funding_return
    )
    return {
        "open_timestamp": open_ms,
        "close_timestamp": close_ms,
        "entry_price": entry,
        "exit_price": exit_price,
        "stake_amount": stake,
        "requested_stake_amount": expected_stake,
        "position_size_drift": sizing_drift,
        "position_size_drift_bps": sizing_drift / expected_stake * 10_000.0,
        "position_size_shortfall": sizing_shortfall,
        "amount": amount,
        "amount_step": APPROVED_BTC_AMOUNT_STEP,
        "reconstructed_stake_amount": reconstructed_stake,
        "gross_return": gross_return,
        "fee_rate_per_side": fee,
        "slippage_rate_per_side": slippage,
        "funding_payment_count": len(selected_funding),
        "funding_profit_abs": funding_profit_abs,
        "funding_return": funding_return,
        "funding_return_on_entry_cost": funding_return_on_entry_cost,
        "slippage_adjusted_funding_return_on_notional": (
            slipped_funding_return_on_notional
        ),
        "slippage_adjusted_funding_return": slipped_funding_return,
        "fee_and_funding_profit_abs": fee_funding_profit_abs,
        "fee_and_funding_return": fee_funding_return,
        "all_cost_return": all_cost_return,
        "all_cost_profit_abs": stake * (1.0 + fee) * all_cost_return,
    }


class WorkerStateStore:
    """Persisted fail-closed job state with injectable process control."""

    def __init__(
        self,
        root: Path | str,
        *,
        compose_file: Path | str | None = None,
        project_directory: Path | str | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.root = Path(root)
        self.compose_file = Path(compose_file).resolve() if compose_file else None
        self.project_directory = (
            Path(project_directory).resolve() if project_directory else None
        )
        self.process_factory = process_factory
        self.processes: dict[str, Any] = {}

    def _paths(self, job_id: str) -> tuple[Path, Path]:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError(f"Unsafe job id: {job_id!r}")
        directory = self.root / job_id
        return directory, directory / "status.json"

    def _write_status(self, job_id: str, state: str, **extra: Any) -> dict[str, Any]:
        directory, status_path = self._paths(job_id)
        payload = {
            "job_id": job_id,
            "state": state,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        _atomic_write(status_path, _pretty_json_bytes(payload))
        return payload

    def start(
        self,
        job_id: str,
        *,
        worker_spec_path: Path | str,
    ) -> dict[str, Any]:
        spec = json.loads(Path(worker_spec_path).read_text(encoding="utf-8"))
        expected_spec = {
            "version": 1,
            "image": FREQTRADE_IMAGE,
            "strategy_class": APPROVED_STRATEGY_CLASS,
            "allowed_command": "backtesting",
            "timeframe": "5m",
            "hold_bars": 12,
            "fee_rate_per_side": 0.0004,
            "slippage_rate_per_side": 0.00015,
            "fixed_notional": 1000.0,
            "funding_source": "binance_historical_funding",
            "network_policy": "public_binance_metadata_only",
            "live_trading": False,
        }
        if spec != expected_spec:
            raise ValueError("Worker spec is not approved for isolated backtesting")
        if self.compose_file is None or self.project_directory is None:
            raise ValueError("Worker requires a fixed Compose file and project directory")
        if job_id in self.processes:
            raise ValueError(f"Job is already active: {job_id}")
        directory, _ = self._paths(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        self._write_status(job_id, "queued")
        command = [
            "docker",
            "compose",
            "--file",
            str(self.compose_file),
            "run",
            "--rm",
            APPROVED_WORKER_SERVICE,
        ]
        process = self.process_factory(command, cwd=str(self.project_directory))
        self.processes[job_id] = process
        return self._write_status(
            job_id,
            "running",
            pid=getattr(process, "pid", None),
            service=APPROVED_WORKER_SERVICE,
        )

    def poll(self, job_id: str) -> dict[str, Any]:
        process = self.processes.get(job_id)
        if process is None:
            _, status_path = self._paths(job_id)
            return json.loads(status_path.read_text(encoding="utf-8"))
        code = process.poll()
        if code is None:
            return self._write_status(job_id, "running", pid=getattr(process, "pid", None))
        del self.processes[job_id]
        return self._write_status(
            job_id,
            "completed" if code == 0 else "failed",
            exit_code=int(code),
        )

    def cancel(self, job_id: str) -> dict[str, Any]:
        process = self.processes.get(job_id)
        if process is None:
            raise ValueError(f"Job is not active: {job_id}")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        del self.processes[job_id]
        return self._write_status(job_id, "cancelled")
