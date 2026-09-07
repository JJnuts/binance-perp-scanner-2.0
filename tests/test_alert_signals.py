import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from perpscanner.alert_signals import AlertSignal, SignalSelectionError, select_alert_signals
from perpscanner.scoring import _build_best_setups


class AlertSignalSelectionTests(unittest.TestCase):
    def _ltf_row(
        self,
        symbol="SOLUSDT",
        direction="Long",
        score=80.0,
        *,
        fresh=True,
        aligned=True,
    ):
        return {
            "symbol": symbol,
            "tf_alignment_pass": aligned,
            "fresh_setup_pass": fresh,
            "ltf_direction": direction,
            "ltf_ignition_score": score,
        }

    def _htf_row(
        self,
        symbol="SOLUSDT",
        direction="Long",
        score=90.0,
        *,
        daily_confirmed=True,
        expansion_direction=None,
    ):
        expansion_direction = expansion_direction or f"{direction} expansion"
        return {
            "symbol": symbol,
            "htf_expansion_direction": expansion_direction,
            "htf_expansion_score": score,
            "htf_momentum_score": score,
            "htf_setup_score": score,
            "htf_atr_percentile": 50.0,
            "htf_atr_roc": 0.1,
            "htf_breakout_distance_atr": 0.5,
            "daily_structure_score": 90.0,
            "daily_long_confirmed": daily_confirmed if direction == "Long" else False,
            "daily_short_confirmed": daily_confirmed if direction == "Short" else False,
            "daily_volume_ratio": 2.0,
            "daily_volume_persistence_days": 2,
            "daily_oi_persistence_days": 2,
            "daily_swing_high": 100.0,
            "daily_swing_low": 90.0,
            "btc_daily_regime": "Bull trend",
            "btc_daily_regime_score": 80.0,
            "rs_24h": 3.0,
            "rs_72h": 5.0,
        }

    def _select(self, ltf_rows, htf_rows, **kwargs):
        return select_alert_signals(
            pd.DataFrame(ltf_rows),
            pd.DataFrame(htf_rows),
            datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc),
            **kwargs,
        )

    def test_fresh_long_best_setup_is_initial_and_continuation_eligible(self):
        signals = self._select([self._ltf_row()], [self._htf_row()])

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertIsInstance(signal, AlertSignal)
        self.assertEqual(signal.key, ("SOLUSDT", "LONG"))
        self.assertAlmostEqual(signal.score, 86.0)
        self.assertEqual(signal.strength, 86)
        self.assertTrue(signal.initial_eligible)
        self.assertTrue(signal.continuation_eligible)

    def test_fresh_short_best_setup_uses_short_direction(self):
        signals = self._select(
            [self._ltf_row("ETHUSDT", "Short", 82.0)],
            [self._htf_row("ETHUSDT", "Short", 88.0)],
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].key, ("ETHUSDT", "SHORT"))
        self.assertEqual(signals[0].strength, 87)

    def test_expired_trigger_can_continue_but_cannot_open_setup(self):
        signals = self._select(
            [self._ltf_row(fresh=False)],
            [self._htf_row()],
        )

        self.assertEqual(len(signals), 1)
        self.assertFalse(signals[0].initial_eligible)
        self.assertTrue(signals[0].continuation_eligible)

    def test_dashboard_best_setups_default_still_requires_freshness(self):
        ltf = pd.DataFrame([self._ltf_row(fresh=False)])
        htf = pd.DataFrame([self._htf_row()])

        self.assertTrue(_build_best_setups(ltf, htf).empty)
        continuing = _build_best_setups(ltf, htf, require_fresh=False)
        self.assertEqual(continuing.iloc[0]["best_setup_state"], "Long best setup")

    def test_dashboard_default_matches_explicit_fresh_mode(self):
        ltf = pd.DataFrame([self._ltf_row()])
        htf = pd.DataFrame([self._htf_row()])

        pd.testing.assert_frame_equal(
            _build_best_setups(ltf, htf),
            _build_best_setups(ltf, htf, require_fresh=True),
        )

    def test_mixed_and_compression_states_are_excluded(self):
        mixed = self._select(
            [self._ltf_row("MIXUSDT", "Long", 100.0)],
            [self._htf_row("MIXUSDT", "Short", 100.0)],
        )
        compression = self._select(
            [self._ltf_row("COMPUSDT", "Long", 100.0)],
            [
                self._htf_row(
                    "COMPUSDT",
                    "Long",
                    100.0,
                    expansion_direction="Compression",
                )
            ],
        )

        self.assertEqual(mixed, ())
        self.assertEqual(compression, ())

    def test_missing_daily_confirmation_is_excluded(self):
        signals = self._select(
            [self._ltf_row(score=100.0)],
            [self._htf_row(score=100.0, daily_confirmed=False)],
        )

        self.assertEqual(signals, ())

    def test_threshold_is_inclusive_and_separate_for_entry_and_continuation(self):
        # 0.55 * 60 + 0.30 * 90 + 0.15 * 100 = exactly 75.
        at_threshold = self._select(
            [self._ltf_row(score=60.0)],
            [self._htf_row(score=90.0)],
        )
        entry_only = self._select(
            [self._ltf_row(score=60.0)],
            [self._htf_row(score=90.0)],
            entry_threshold=75.0,
            continuation_threshold=80.0,
        )

        self.assertEqual(at_threshold[0].strength, 75)
        self.assertTrue(at_threshold[0].initial_eligible)
        self.assertTrue(at_threshold[0].continuation_eligible)
        self.assertTrue(entry_only[0].initial_eligible)
        self.assertFalse(entry_only[0].continuation_eligible)

    def test_below_both_thresholds_is_excluded(self):
        signals = self._select(
            [self._ltf_row(score=50.0)],
            [self._htf_row(score=50.0)],
        )

        self.assertEqual(signals, ())

    def test_results_are_sorted_by_raw_score_then_symbol(self):
        signals = self._select(
            [
                self._ltf_row("LOWUSDT", score=70.0),
                self._ltf_row("ZEDUSDT", score=90.0),
                self._ltf_row("ALPHAUSDT", score=90.0),
            ],
            [
                self._htf_row("LOWUSDT", score=80.0),
                self._htf_row("ZEDUSDT", score=90.0),
                self._htf_row("ALPHAUSDT", score=90.0),
            ],
        )

        self.assertEqual([signal.symbol for signal in signals], ["ALPHAUSDT", "ZEDUSDT", "LOWUSDT"])

    def test_observation_time_is_normalized_to_utc(self):
        observed = datetime(2026, 9, 6, 18, 0, tzinfo=timezone(timedelta(hours=3)))

        signal = select_alert_signals(
            pd.DataFrame([self._ltf_row()]),
            pd.DataFrame([self._htf_row()]),
            observed,
        )[0]

        self.assertEqual(signal.observed_at_utc, datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc))

    def test_naive_observation_time_and_invalid_thresholds_are_rejected(self):
        ltf = pd.DataFrame([self._ltf_row()])
        htf = pd.DataFrame([self._htf_row()])

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            select_alert_signals(ltf, htf, datetime(2026, 9, 6, 15, 0))
        for invalid in (-1, 101, float("nan"), float("inf"), True, "not-a-number"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "entry_threshold"):
                    select_alert_signals(
                        ltf,
                        htf,
                        datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc),
                        entry_threshold=invalid,
                    )

    def test_empty_frames_return_no_signals(self):
        observed = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(select_alert_signals(pd.DataFrame(), pd.DataFrame(), observed), ())

    def test_partial_nonempty_scan_is_rejected_instead_of_treated_as_no_signal(self):
        observed = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)

        with self.assertRaisesRegex(SignalSelectionError, "incomplete alert scan"):
            select_alert_signals(
                pd.DataFrame([{"symbol": "SOLUSDT"}]),
                pd.DataFrame([{"symbol": "SOLUSDT"}]),
                observed,
            )
        with self.assertRaisesRegex(SignalSelectionError, "LTF frame is empty"):
            select_alert_signals(
                pd.DataFrame(),
                pd.DataFrame([self._htf_row()]),
                observed,
            )

    def test_selection_does_not_mutate_scan_frames(self):
        ltf = pd.DataFrame([self._ltf_row()])
        htf = pd.DataFrame([self._htf_row()])
        original_ltf = ltf.copy(deep=True)
        original_htf = htf.copy(deep=True)

        select_alert_signals(
            ltf,
            htf,
            datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc),
        )

        pd.testing.assert_frame_equal(ltf, original_ltf)
        pd.testing.assert_frame_equal(htf, original_htf)


if __name__ == "__main__":
    unittest.main()
