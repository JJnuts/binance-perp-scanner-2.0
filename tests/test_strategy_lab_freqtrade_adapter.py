import copy
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from perpscanner.strategy_lab.freqtrade_adapter import (
    WorkerStateStore,
    compile_strategy_contract,
    load_strategy_contract,
    reconcile_trade_costs,
    validate_strategy_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    REPO_ROOT
    / "docs"
    / "strategy_lab"
    / "examples"
    / "btcusdt_ema9_fixed-hold.strategy-backtest.json"
)
SCHEMA = REPO_ROOT / "docs" / "strategy_lab" / "research_contract.schema.json"
STRATEGY = (
    REPO_ROOT
    / "tools"
    / "strategy_lab_phase4"
    / "freqtrade"
    / "user_data"
    / "strategies"
    / "ApprovedEma9FixedHoldStrategy.py"
)


class FakeProcess:
    next_pid = 5000

    def __init__(self, command, cwd):
        self.command = command
        self.cwd = cwd
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.code = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout):
        del timeout
        return self.code

    def kill(self):
        self.killed = True
        self.code = -9


class FreqtradeAdapterTests(unittest.TestCase):
    def test_contract_compiles_to_static_non_live_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiled = compile_strategy_contract(
                contract_path=CONTRACT,
                schema_path=SCHEMA,
                strategy_path=STRATEGY,
                output_dir=tmp,
            )
            config = json.loads(compiled.config_path.read_text(encoding="utf-8"))
            manifest = json.loads(compiled.manifest_path.read_text(encoding="utf-8"))
            worker = json.loads(compiled.worker_spec_path.read_text(encoding="utf-8"))
            self.assertTrue(config["dry_run"])
            self.assertEqual(config["trading_mode"], "futures")
            self.assertEqual(config["margin_mode"], "isolated")
            self.assertEqual(config["exchange"]["key"], "")
            self.assertTrue(config["entry_pricing"]["use_order_book"])
            self.assertTrue(config["exit_pricing"]["use_order_book"])
            self.assertNotIn("telegram", config)
            self.assertNotIn("api_server", config)
            self.assertFalse(manifest["strategy_generated"])
            self.assertFalse(manifest["live_trading"])
            self.assertEqual(worker["allowed_command"], "backtesting")
            self.assertFalse(worker["live_trading"])

    def test_unapproved_parameter_is_rejected(self):
        contract = load_strategy_contract(CONTRACT, SCHEMA)
        changed = copy.deepcopy(contract)
        changed["signal"]["indicator"]["length"] = 10
        with self.assertRaises(ValueError):
            validate_strategy_contract(changed)

    def test_any_unreviewed_contract_change_is_rejected(self):
        contract = load_strategy_contract(CONTRACT, SCHEMA)
        changed = copy.deepcopy(contract)
        changed["experiment"]["notes"] += " altered"
        with self.assertRaises(ValueError):
            validate_strategy_contract(changed)

    def test_compiler_rejects_an_unreviewed_strategy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            changed_strategy = Path(tmp) / STRATEGY.name
            changed_strategy.write_text(
                STRATEGY.read_text(encoding="utf-8") + "\n# changed\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                compile_strategy_contract(
                    contract_path=CONTRACT,
                    schema_path=SCHEMA,
                    strategy_path=changed_strategy,
                    output_dir=Path(tmp) / "compiled",
                )

    def test_fee_slippage_funding_and_position_size_reconcile(self):
        contract = load_strategy_contract(CONTRACT, SCHEMA)
        trade = {
            "open_rate": 100.0,
            "close_rate": 101.0,
            "stake_amount": 1000.0,
            "amount": 10.0,
            "is_short": False,
            "open_timestamp": 1_000,
            "close_timestamp": 5_000,
        }
        result = reconcile_trade_costs(
            trade,
            contract,
            [
                {
                    "funding_time_ms": 3_000,
                    "funding_rate": 0.0001,
                    "mark_price": 100.5,
                }
            ],
        )
        fee = 0.0004
        slippage = 0.00015
        expected_funding_abs = -(10.0 * 100.5 * 0.0001)
        expected_profit_abs = (
            10.0 * (101.0 * (1 - fee) - 100.0 * (1 + fee))
            + expected_funding_abs
        )
        expected_fee_funding = (
            expected_profit_abs / (1000.0 * (1 + fee))
        )
        expected_slipped_funding = (
            -(100.5 / (100.0 * (1 + slippage))) * 0.0001
            / (1 + fee)
        )
        expected_all = (
            (101.0 * (1 - slippage) * (1 - fee))
            / (100.0 * (1 + slippage) * (1 + fee))
            - 1
            + expected_slipped_funding
        )
        self.assertAlmostEqual(result["fee_and_funding_return"], expected_fee_funding, places=14)
        self.assertAlmostEqual(result["all_cost_return"], expected_all, places=14)
        self.assertAlmostEqual(
            result["fee_and_funding_profit_abs"],
            expected_profit_abs,
            places=12,
        )
        self.assertAlmostEqual(
            result["all_cost_profit_abs"],
            1000.0 * (1 + fee) * expected_all,
            places=10,
        )
        self.assertEqual(result["funding_payment_count"], 1)
        self.assertAlmostEqual(result["funding_profit_abs"], -0.1005, places=12)

    def test_exchange_amount_step_rounding_reconciles(self):
        contract = load_strategy_contract(CONTRACT, SCHEMA)
        result = reconcile_trade_costs(
            {
                "open_rate": 151.58,
                "close_rate": 152.78,
                "stake_amount": 999.97326,
                "amount": 6.597,
                "leverage": 1.0,
                "is_short": False,
                "open_timestamp": 1_000,
                "close_timestamp": 5_000,
            },
            contract,
            [],
        )
        self.assertAlmostEqual(
            result["reconstructed_stake_amount"],
            999.97326,
            places=8,
        )
        self.assertGreater(result["position_size_shortfall"], 0.0)
        self.assertLess(result["position_size_shortfall"], 151.58 * 0.001)

    def test_fixture_generator_writes_exact_futures_inputs(self):
        phase4 = REPO_ROOT / "tools" / "strategy_lab_phase4"
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "PHASE4_ROOT": str(phase4),
                    "FREQTRADE_USER_DATA": tmp,
                },
            ):
                runpy.run_path(str(phase4 / "prepare_fixture.py"))
            futures = (
                Path(tmp)
                / "data"
                / "binance"
                / "futures"
                / "BTC_USDT_USDT-5m-futures.json"
            )
            funding = futures.with_name("BTC_USDT_USDT-1h-funding_rate.json")
            rows = json.loads(futures.read_text(encoding="utf-8"))
            funding_rows = json.loads(funding.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 560)
            nonzero = [row for row in funding_rows if float(row[4]) != 0.0]
            self.assertEqual(
                nonzero,
                [[1767254400000, 0.0001, 0.0001, 0.0001, 0.0001, 0.0]],
            )

    def test_worker_state_and_cancellation_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiled = compile_strategy_contract(
                contract_path=CONTRACT,
                schema_path=SCHEMA,
                strategy_path=STRATEGY,
                output_dir=Path(tmp) / "compiled",
            )
            processes = []

            def factory(command, cwd):
                process = FakeProcess(command, cwd)
                processes.append(process)
                return process

            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")
            worker = WorkerStateStore(
                Path(tmp) / "jobs",
                compose_file=compose_file,
                project_directory=tmp,
                process_factory=factory,
            )
            running = worker.start(
                "phase4-test-job",
                worker_spec_path=compiled.worker_spec_path,
            )
            self.assertEqual(running["state"], "running")
            self.assertEqual(running["service"], "freqtrade-backtest")
            self.assertEqual(
                processes[0].command,
                [
                    "docker",
                    "compose",
                    "--file",
                    str(compose_file.resolve()),
                    "run",
                    "--rm",
                    "freqtrade-backtest",
                ],
            )
            self.assertEqual(worker.poll("phase4-test-job")["state"], "running")
            cancelled = worker.cancel("phase4-test-job")
            self.assertEqual(cancelled["state"], "cancelled")
            self.assertTrue(processes[0].terminated)
            status = json.loads(
                (Path(tmp) / "jobs" / "phase4-test-job" / "status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["state"], "cancelled")

    def test_worker_cannot_start_without_fixed_compose_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiled = compile_strategy_contract(
                contract_path=CONTRACT,
                schema_path=SCHEMA,
                strategy_path=STRATEGY,
                output_dir=Path(tmp) / "compiled",
            )
            worker = WorkerStateStore(Path(tmp) / "jobs")
            with self.assertRaises(ValueError):
                worker.start(
                    "phase4-test-job",
                    worker_spec_path=compiled.worker_spec_path,
                )

    def test_worker_rejects_modified_worker_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiled = compile_strategy_contract(
                contract_path=CONTRACT,
                schema_path=SCHEMA,
                strategy_path=STRATEGY,
                output_dir=Path(tmp) / "compiled",
            )
            spec = json.loads(compiled.worker_spec_path.read_text(encoding="utf-8"))
            spec["fee_rate_per_side"] = 0.01
            changed_spec = Path(tmp) / "changed-worker-spec.json"
            changed_spec.write_text(json.dumps(spec), encoding="utf-8")
            worker = WorkerStateStore(
                Path(tmp) / "jobs",
                compose_file=Path(tmp) / "docker-compose.yml",
                project_directory=tmp,
            )
            with self.assertRaises(ValueError):
                worker.start(
                    "phase4-test-job",
                    worker_spec_path=changed_spec,
                )

    def test_unsafe_job_identifier_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = WorkerStateStore(tmp)
            with self.assertRaises(ValueError):
                worker.poll("../escape")


if __name__ == "__main__":
    unittest.main()
