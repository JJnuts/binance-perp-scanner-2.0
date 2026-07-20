import copy
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from perpscanner.strategy_lab.validation import (
    ValidationGateError,
    assert_recursive_indicator_stability,
    benjamini_hochberg,
    build_anchored_walk_forward_folds,
    moving_block_bootstrap_interval,
    run_adversarial_validation_checks,
    run_validation_pipeline,
    validate_delay_one_and_selection_inputs,
    validate_feature_provenance,
    validate_holdout_lock,
    validate_multiple_testing_ledger,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "strategy_lab"
    / "examples"
    / "btcusdt_ema9_bounce.event-study.json"
)
PHASE3_EVENTS = (
    REPO_ROOT
    / "tools"
    / "strategy_lab_phase3"
    / "artifacts"
    / "pandas"
    / "events.csv"
)


def validation_contract() -> dict:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["validation"]["bootstrap_samples"] = 100
    contract["validation"]["minimum_events"] = 20
    return contract


def synthetic_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    total_bars = 1000
    bars = pd.DataFrame(
        {
            "close": 100.0 + np.arange(total_bars, dtype=float) * 0.01,
        }
    )
    signal_indices = np.arange(10, 990, 5, dtype=int)
    rows = []
    for ordinal, signal_index in enumerate(signal_indices):
        split = (
            "training"
            if signal_index < 600
            else "validation"
            if signal_index < 800
            else "final_holdout"
        )
        hidden = split == "final_holdout"
        value = float(np.sin(ordinal / 7.0) * 0.002 - 0.0002)
        row = {
            "signal_index": int(signal_index),
            "signal_time_ms": int(signal_index * 300_000),
            "entry_index": int(signal_index + 1),
            "entry_time_ms": int((signal_index + 1) * 300_000),
            "ema": 100.0 + signal_index * 0.01,
            "split": split,
            "holdout_hidden": hidden,
            "subperiod": "A" if signal_index < 500 else "B",
            "regime": "Bull" if ordinal % 2 else "Bear",
            "barrier_status": np.nan if hidden else ("target" if value > 0 else "stop"),
        }
        for horizon in (1, 3, 6, 12):
            row[f"forward_{horizon}_net"] = np.nan if hidden else value / horizon
        rows.append(row)
    return pd.DataFrame(rows), bars, validation_contract()


class StrategyLabValidationTests(unittest.TestCase):
    def test_real_phase3_holdout_is_locked(self):
        events = pd.read_csv(PHASE3_EVENTS)
        report = validate_holdout_lock(
            events,
            total_bars=210_240,
            contract=json.loads(CONTRACT_PATH.read_text(encoding="utf-8")),
        )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["hidden_holdout_event_count"], 1604)
        self.assertFalse(report["holdout_opened"])

    def test_exposed_holdout_outcome_is_rejected(self):
        events, bars, contract = synthetic_inputs()
        holdout_index = events.index[events["holdout_hidden"]][0]
        events.loc[holdout_index, "forward_12_net"] = 0.50
        with self.assertRaises(ValidationGateError):
            validate_holdout_lock(
                events,
                total_bars=len(bars),
                contract=contract,
            )

    def test_mislabeled_chronological_split_is_rejected(self):
        events, bars, contract = synthetic_inputs()
        events.loc[events.index[0], "split"] = "validation"
        with self.assertRaises(ValidationGateError):
            validate_holdout_lock(
                events,
                total_bars=len(bars),
                contract=contract,
            )

    def test_delay_one_passes_and_outcome_selection_fails(self):
        events, _, _ = synthetic_inputs()
        report = validate_delay_one_and_selection_inputs(events)
        self.assertEqual(report["entry_delay_bars"], 1)
        with self.assertRaises(ValidationGateError):
            validate_delay_one_and_selection_inputs(
                events,
                selection_columns=["forward_12_net"],
            )

    def test_future_feature_provenance_is_rejected(self):
        provenance = pd.DataFrame(
            [
                {
                    "feature": "ema",
                    "source_index": 10,
                    "available_index": 10,
                    "decision_index": 11,
                    "used_for_decision": True,
                }
            ]
        )
        self.assertEqual(
            validate_feature_provenance(provenance)["status"],
            "passed",
        )
        provenance.loc[0, "available_index"] = 12
        with self.assertRaises(ValidationGateError):
            validate_feature_provenance(provenance)

    def test_recursive_ema_passes_and_future_shift_fails(self):
        values = pd.Series(np.linspace(100.0, 120.0, 80))
        good = assert_recursive_indicator_stability(
            values,
            compute=lambda series: series.ewm(
                span=9,
                adjust=False,
                min_periods=9,
            ).mean(),
            checkpoints=[10, 25, 50, 79],
        )
        self.assertEqual(good["maximum_absolute_difference"], 0.0)
        with self.assertRaises(ValidationGateError):
            assert_recursive_indicator_stability(
                values,
                compute=lambda series: series.shift(-1),
                checkpoints=[10, 25, 50],
            )

    def test_benjamini_hochberg_and_ledger(self):
        raw = np.array([0.01, 0.04, 0.03, 0.20])
        adjusted = benjamini_hochberg(raw)
        np.testing.assert_allclose(adjusted, [0.04, 0.05333333333333334, 0.05333333333333334, 0.20])
        ledger = pd.DataFrame(
            {
                "hypothesis_id": ["a", "b", "c", "d"],
                "raw_p_value": raw,
                "adjusted_p_value": adjusted,
                "correction_method": ["benjamini_hochberg"] * 4,
            }
        )
        report = validate_multiple_testing_ledger(
            ledger,
            expected_hypotheses=["a", "b", "c", "d"],
            method="benjamini_hochberg",
        )
        self.assertTrue(report["all_hypotheses_logged"])
        ledger.loc[0, "adjusted_p_value"] = ledger.loc[0, "raw_p_value"]
        with self.assertRaises(ValidationGateError):
            validate_multiple_testing_ledger(
                ledger,
                expected_hypotheses=["a", "b", "c", "d"],
                method="benjamini_hochberg",
            )

    def test_moving_block_bootstrap_is_deterministic(self):
        values = np.sin(np.arange(120) / 8.0)
        first = moving_block_bootstrap_interval(
            values,
            confidence=0.95,
            samples=200,
            seed=144,
        )
        second = moving_block_bootstrap_interval(
            values,
            confidence=0.95,
            samples=200,
            seed=144,
        )
        self.assertEqual(first, second)
        self.assertGreater(first["block_length"], 1)

    def test_anchored_folds_respect_purge_embargo_and_holdout(self):
        events, bars, contract = synthetic_inputs()
        folds = build_anchored_walk_forward_folds(
            events,
            total_bars=len(bars),
            contract=contract,
            fold_count=2,
        )
        self.assertEqual(len(folds), 2)
        self.assertEqual(folds[0].validation_start_bar_inclusive, 612)
        self.assertEqual(folds[1].validation_end_bar_exclusive, 800)
        for fold in folds:
            self.assertFalse(fold.training["holdout_hidden"].any())
            self.assertFalse(fold.validation["holdout_hidden"].any())
            self.assertLess(
                int(fold.training["outcome_end_index"].max()),
                fold.training_end_bar_exclusive,
            )
            self.assertGreaterEqual(
                int(fold.validation["signal_index"].min()),
                fold.validation_start_bar_inclusive,
            )

    def test_adversarial_suite_rejects_every_bad_input(self):
        report = run_adversarial_validation_checks(validation_contract())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["rejected_check_count"], 5)
        self.assertEqual(
            {item["status"] for item in report["checks"].values()},
            {"rejected"},
        )

    def test_synthetic_pipeline_passes_without_opening_holdout(self):
        events, bars, contract = synthetic_inputs()
        report, ledger = run_validation_pipeline(
            events,
            bars,
            contract,
        )
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["holdout"]["holdout_opened"])
        self.assertEqual(report["walk_forward"]["fold_count"], 2)
        self.assertEqual(len(ledger), 4)
        self.assertEqual(report["claim_level"], "exploratory")


if __name__ == "__main__":
    unittest.main()
