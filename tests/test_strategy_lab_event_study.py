import copy
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from perpscanner.strategy_lab.event_study import (
    candidate_mask,
    ema_series,
    load_contract,
    run_event_study,
    validate_reference_contract,
)
from perpscanner.strategy_lab.reference_calculator import (
    recursive_ema,
    reference_barrier,
    reference_candidates,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "strategy_lab"
    / "examples"
    / "btcusdt_ema9_bounce.event-study.json"
)
SCHEMA_PATH = REPO_ROOT / "docs" / "strategy_lab" / "research_contract.schema.json"
FIXTURE = REPO_ROOT / "tools" / "strategy_lab_phase3" / "fixture" / "ema9_bounce_5m.csv"
EXPECTED = REPO_ROOT / "tools" / "strategy_lab_phase3" / "fixture" / "expected.json"


def fixture_contract() -> dict:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["data"]["warmup_bars"] = 9
    contract["validation"]["bootstrap_samples"] = 100
    return contract


def fixture_frame() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURE)
    frame["open_time_utc"] = pd.to_datetime(frame.pop("date"), utc=True)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column])
    return frame.set_index("open_time_utc", drop=False)


class StrategyLabEventStudyTests(unittest.TestCase):
    def test_reference_contract_validates_against_schema_and_capability(self) -> None:
        contract = load_contract(CONTRACT_PATH, SCHEMA_PATH)
        settings = validate_reference_contract(contract)
        self.assertEqual(settings.ema_length, 9)
        self.assertEqual(settings.horizons, (1, 3, 6, 12))
        self.assertTrue(settings.holdout_locked)

    def test_hand_fixture_vectorized_and_loop_candidates_agree(self) -> None:
        contract = fixture_contract()
        frame = fixture_frame()
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        settings = validate_reference_contract(contract)
        ema = ema_series(frame["close"], 9, backend="pandas")
        vectorized = np.flatnonzero(candidate_mask(frame, ema, settings).to_numpy()).tolist()
        reference = reference_candidates(frame, contract)
        self.assertEqual(vectorized, expected["candidate_signal_indices"])
        self.assertEqual(reference, vectorized)
        self.assertAlmostEqual(
            float(ema.iloc[15]),
            expected["ema_at_first_signal"],
            places=12,
        )
        self.assertAlmostEqual(
            float(ema.iloc[35]),
            expected["ema_at_second_accepted_signal"],
            places=12,
        )
        loop_ema = recursive_ema(frame["close"].tolist(), 9)
        np.testing.assert_allclose(
            ema.dropna().to_numpy(),
            np.array([value for value in loop_ema if value is not None]),
            rtol=0.0,
            atol=1e-12,
        )

    def test_hand_fixture_outcomes_overlap_and_ambiguity_agree(self) -> None:
        contract = fixture_contract()
        frame = fixture_frame()
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        funding = pd.DataFrame(columns=["funding_time_ms", "funding_rate"])

        def detail_resolver(open_ms: int, target: float, stop: float) -> str:
            del target, stop
            return (
                "target"
                if open_ms == expected["ambiguous_bar_open_time_ms"]
                else "unresolved"
            )

        events, summary = run_event_study(
            frame,
            contract,
            backend="pandas",
            funding=funding,
            detail_resolver=detail_resolver,
        )
        self.assertEqual(
            events["signal_index"].astype(int).tolist(),
            expected["accepted_signal_indices"],
        )
        self.assertEqual(
            events["barrier_status"].tolist(),
            expected["barrier_statuses"],
        )
        self.assertEqual(summary["cooldown_skipped_count"], 1)
        self.assertEqual(summary["overlap_skipped_count"], 0)
        self.assertEqual(summary["ambiguous_event_count"], 1)

        settings = validate_reference_contract(contract)
        statuses = []
        for signal_index in expected["accepted_signal_indices"]:
            status, _ = reference_barrier(
                frame,
                signal_index + 1,
                settings,
                detail_resolver=detail_resolver,
            )
            statuses.append(status)
        self.assertEqual(statuses, expected["barrier_statuses"])

    def test_delay_one_signal_does_not_change_when_future_is_modified(self) -> None:
        contract = fixture_contract()
        frame = fixture_frame()
        settings = validate_reference_contract(contract)
        original = np.flatnonzero(
            candidate_mask(
                frame,
                ema_series(frame["close"], 9, backend="pandas"),
                settings,
            ).to_numpy()
        ).tolist()
        changed = frame.copy()
        changed.loc[changed.index[16]:, "close"] *= 2.0
        changed_candidates = np.flatnonzero(
            candidate_mask(
                changed,
                ema_series(changed["close"], 9, backend="pandas"),
                settings,
            ).to_numpy()
        ).tolist()
        self.assertEqual(original[0], changed_candidates[0])

    def test_unsupported_parameter_search_is_rejected(self) -> None:
        contract = fixture_contract()
        changed = copy.deepcopy(contract)
        changed["signal"]["indicator"]["length"] = 10
        with self.assertRaises(ValueError):
            validate_reference_contract(changed)


if __name__ == "__main__":
    unittest.main()
