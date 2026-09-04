import unittest

import pandas as pd

from perpscanner import calibration_confluence, calibration_ltf


BASELINE = {
    "expansion": 0.30,
    "volume": 0.25,
    "oi": 0.20,
    "taker": 0.15,
    "basis": 0.10,
}
BOUNDS = {
    "expansion": [0.05, 0.40],
    "volume": [0.05, 0.40],
    "oi": [0.05, 0.30],
    "taker": [0.05, 0.30],
    "basis": [0.05, 0.25],
}


class ConfluenceCalibrationPrimitiveTests(unittest.TestCase):
    @staticmethod
    def _frame(scans=1, symbols=6) -> pd.DataFrame:
        rows = []
        for scan in range(scans):
            timestamp = pd.Timestamp("2026-01-01T00:00:00") + pd.Timedelta(
                hours=scan
            )
            for index in range(symbols):
                scaled = index / float(symbols - 1)
                side = "Long" if index % 2 == 0 else "Short"
                sign = 1.0 if side == "Long" else -1.0
                rows.append(
                    {
                        "scan_ts": timestamp,
                        "symbol": f"S{index:02d}",
                        "veto_side": side,
                        "comp_expansion": scaled,
                        "comp_volume": scaled,
                        "comp_oi": scaled,
                        "comp_taker_net": sign * scaled,
                        "comp_basis_net": sign * scaled,
                    }
                )
        return pd.DataFrame(rows)

    def test_candidate_generation_is_locked_deterministic_and_unique(self):
        first = calibration_confluence.generate_confluence_candidates(BASELINE, BOUNDS)
        second = calibration_confluence.generate_confluence_candidates(BASELINE, BOUNDS)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 37)
        self.assertEqual(first[0].candidate_id, "baseline")
        self.assertEqual(len({item.candidate_id for item in first}), len(first))
        for item in first:
            calibration_ltf.validate_weight_vector(
                item.weights,
                BOUNDS,
                baseline=BASELINE,
                maximum_l1_distance=0.20,
            )
            changed = [
                component
                for component in BASELINE
                if abs(item.weights[component] - BASELINE[component]) > 1e-12
            ]
            self.assertIn(len(changed), {0, 2})
            if changed:
                self.assertIn(item.transfer, {0.05, 0.10})

    def test_candidate_generation_rejects_wrong_component_order(self):
        wrong = dict(BASELINE)
        wrong["volume"] = wrong.pop("volume")
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.generate_confluence_candidates(wrong, BOUNDS)

    def test_scoring_orients_taker_and_basis_to_each_veto_side(self):
        frame = pd.DataFrame(
            [
                {
                    "scan_ts": "2026-01-01T00:00:00",
                    "symbol": "LONG",
                    "veto_side": "Long",
                    "comp_expansion": 0.2,
                    "comp_volume": 0.4,
                    "comp_oi": 0.6,
                    "comp_taker_net": 0.8,
                    "comp_basis_net": -1.0,
                },
                {
                    "scan_ts": "2026-01-01T00:00:00",
                    "symbol": "SHORT",
                    "veto_side": "Short",
                    "comp_expansion": 0.2,
                    "comp_volume": 0.4,
                    "comp_oi": 0.6,
                    "comp_taker_net": -0.8,
                    "comp_basis_net": 1.0,
                },
                {
                    "scan_ts": "2026-01-01T00:00:00",
                    "symbol": "INACTIVE",
                    "veto_side": "None",
                    "comp_expansion": 0.0,
                    "comp_volume": 0.0,
                    "comp_oi": 0.0,
                    "comp_taker_net": 0.0,
                    "comp_basis_net": 0.0,
                },
            ]
        )
        scored = calibration_confluence.score_confluence_candidate(frame, BASELINE)
        self.assertEqual(list(scored["symbol"]), ["LONG", "SHORT"])
        expected = 0.30 * 0.2 + 0.25 * 0.4 + 0.20 * 0.6 + 0.15 * 0.8 - 0.10
        self.assertAlmostEqual(scored.loc[scored["symbol"] == "LONG", "candidate_score"].iloc[0], expected)
        self.assertAlmostEqual(scored.loc[scored["symbol"] == "SHORT", "candidate_score"].iloc[0], expected)

    def test_scoring_fails_closed_on_bad_side_range_or_value(self):
        bad_side = self._frame()
        bad_side.loc[0, "veto_side"] = "Buy"
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.score_confluence_candidate(bad_side, BASELINE)

        bad_range = self._frame()
        bad_range.loc[0, "comp_volume"] = 1.01
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.score_confluence_candidate(bad_range, BASELINE)

        non_finite = self._frame()
        non_finite.loc[0, "comp_oi"] = float("inf")
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.score_confluence_candidate(non_finite, BASELINE)

    def test_directional_rank_ic_flips_short_returns(self):
        scored = calibration_confluence.score_confluence_candidate(
            self._frame(), BASELINE
        )
        forward = scored[["scan_ts", "symbol"]].copy()
        signed_outcomes = scored["candidate_score"].to_numpy()
        side_sign = scored["veto_side"].map({"Long": 1.0, "Short": -1.0}).to_numpy()
        forward["fwd_ret"] = signed_outcomes * side_sign
        report = calibration_confluence.directional_cross_section_rank_ic(
            scored, forward
        )
        self.assertEqual(len(report), 1)
        self.assertAlmostEqual(report.iloc[0]["rank_ic"], 1.0)

    def test_objective_uses_1h_and_4h_while_24h_is_safety_only(self):
        frame = self._frame(scans=2)
        candidate = calibration_confluence.generate_confluence_candidates(
            BASELINE, BOUNDS
        )[0]
        scored = calibration_confluence.score_confluence_candidate(frame, BASELINE)
        forward = {}
        for horizon, multiplier in ((1.0, 1.0), (4.0, -1.0), (24.0, 1.0)):
            labels = scored[["scan_ts", "symbol"]].copy()
            side_sign = scored["veto_side"].map({"Long": 1.0, "Short": -1.0})
            labels["fwd_ret"] = scored["candidate_score"] * side_sign * multiplier
            forward[horizon] = labels
        result = calibration_confluence.evaluate_confluence_candidate_metrics(
            frame,
            forward,
            candidate,
            minimum_cross_sections=2,
        )
        self.assertAlmostEqual(result.mean_ic_by_horizon[1.0], 1.0)
        self.assertAlmostEqual(result.mean_ic_by_horizon[4.0], -1.0)
        self.assertAlmostEqual(result.mean_ic_by_horizon[24.0], 1.0)
        self.assertAlmostEqual(result.objective_ic, 0.20)
        self.assertEqual(set(result.component_rank_ic_by_horizon), {1.0, 4.0, 24.0})

    def test_evaluation_rejects_missing_safety_horizon(self):
        frame = self._frame()
        scored = calibration_confluence.score_confluence_candidate(frame, BASELINE)
        labels = scored[["scan_ts", "symbol"]].copy()
        labels["fwd_ret"] = range(len(labels))
        candidate = calibration_confluence.generate_confluence_candidates(
            BASELINE, BOUNDS
        )[0]
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.evaluate_confluence_candidate_metrics(
                frame,
                {1.0: labels, 4.0: labels},
                candidate,
                minimum_cross_sections=1,
            )

    def test_component_support_gate_fails_closed(self):
        frame = self._frame(scans=2)
        scored = calibration_confluence.score_confluence_candidate(frame, BASELINE)
        labels = scored[["scan_ts", "symbol"]].copy()
        labels["fwd_ret"] = range(len(labels))
        candidate = calibration_confluence.generate_confluence_candidates(
            BASELINE, BOUNDS
        )[0]
        with self.assertRaisesRegex(
            calibration_ltf.CalibrationGuardError, "requires 3"
        ):
            calibration_confluence.evaluate_confluence_candidate_metrics(
                frame,
                {1.0: labels, 4.0: labels, 24.0: labels},
                candidate,
                minimum_cross_sections=3,
            )


class ConfluenceCalibrationOrchestrationTests(unittest.TestCase):
    @staticmethod
    def _training_frame(episodes=30, symbols=6) -> pd.DataFrame:
        rows = []
        start = pd.Timestamp("2026-01-01T00:00:00")
        for episode in range(episodes):
            episode_start = start + pd.Timedelta(hours=72 * episode)
            for hour, return_scale in (
                (0, 0.0),
                (1, 0.001),
                (4, 0.002),
                (24, 0.004),
            ):
                timestamp = episode_start + pd.Timedelta(hours=hour)
                for index in range(symbols):
                    scaled = index / float(symbols - 1)
                    side = "Long" if index % 2 == 0 else "Short"
                    sign = 1.0 if side == "Long" else -1.0
                    rows.append(
                        {
                            "scan_ts": timestamp,
                            "symbol": f"S{index:02d}",
                            "price": 100.0 * (1.0 + sign * scaled * return_scale),
                            "veto_side": side,
                            "comp_expansion": scaled,
                            "comp_volume": scaled,
                            "comp_oi": scaled,
                            "comp_taker_net": sign * scaled,
                            "comp_basis_net": sign * scaled,
                        }
                    )
        return pd.DataFrame(rows)

    @classmethod
    def _protocol(cls, frame: pd.DataFrame) -> dict:
        return {
            "state": "locked_before_optimization",
            "optimization_performed": False,
            "protected_inputs": {
                "database": "unused.sqlite",
                "database_sha256": "0" * 64,
                "cutoff_inclusive_utc": "2026-04-09T23:59:59Z",
                "sealed_holdout_accessed": False,
            },
            "partitions": [
                {
                    "name": "training",
                    "start_inclusive_utc": "2026-01-01T00:00:00Z",
                    "end_inclusive_utc": "2026-04-06T23:59:59Z",
                    "ltf": {
                        "rows": len(frame),
                        "scans": frame["scan_ts"].nunique(),
                        "symbols": frame["symbol"].nunique(),
                        "veto_rows": len(frame),
                    },
                    "confluence_fields_complete": True,
                },
                {
                    "name": "internal_validation",
                    "start_inclusive_utc": "2026-04-08T00:00:00Z",
                    "end_inclusive_utc": "2026-04-08T23:59:59Z",
                },
                {
                    "name": "locked_confirmation",
                    "start_inclusive_utc": "2026-04-09T00:00:00Z",
                    "end_inclusive_utc": "2026-04-09T23:59:59Z",
                },
            ],
            "partition_isolation": {
                "forward_horizons_hours": [1.0, 4.0, 24.0],
                "forward_join_tolerance_fraction": 0.35,
            },
            "ranking_experiments": {
                "ltf": {"experiment_id": "alt-ltf-rank-v1"},
            },
            "confluence_experiment": {
                "experiment_id": "alt-confluence-v1",
                "baseline_weights": BASELINE,
                "bounds": BOUNDS,
                "safety_horizon_hours": 24.0,
                "minimum_cross_sections_per_component_per_horizon_per_partition": 30,
                "training_objective_delta_minimum": 0.01,
                "native_20260903_suggestion_eligible": False,
            },
        }

    def test_training_runner_is_deterministic_and_training_only(self):
        frame = self._training_frame()
        locked = self._protocol(frame)
        first = calibration_confluence.run_confluence_training_stage(locked, frame)
        second = calibration_confluence.run_confluence_training_stage(locked, frame)
        self.assertEqual(first.partition_name, "training")
        self.assertEqual(first.candidate_count, 37)
        self.assertEqual(len(first.decisions), 36)
        self.assertIsNone(first.winner)
        self.assertEqual(
            [item.candidate.candidate_id for item in first.evaluated],
            [item.candidate.candidate_id for item in second.evaluated],
        )
        self.assertEqual(
            [item.objective_ic for item in first.evaluated],
            [item.objective_ic for item in second.evaluated],
        )
        for evaluation in first.evaluated:
            self.assertAlmostEqual(evaluation.objective_ic, 1.0)
            for horizon in (1.0, 4.0, 24.0):
                self.assertEqual(len(evaluation.rank_ic_by_horizon[horizon]), 30)

    def test_training_runner_rejects_escaped_or_partial_partition(self):
        frame = self._training_frame()
        locked = self._protocol(frame)
        escaped = frame.copy()
        escaped.loc[escaped.index[-1], "scan_ts"] = pd.Timestamp(
            "2026-04-08T00:00:00"
        )
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.run_confluence_training_stage(locked, escaped)

        partial_lock = self._protocol(frame)
        partial_lock["partitions"][0]["ltf"]["rows"] += 1
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.run_confluence_training_stage(partial_lock, frame)

    def test_training_runner_rejects_changed_confluence_lock(self):
        frame = self._training_frame()
        changed_gate = self._protocol(frame)
        changed_gate["confluence_experiment"][
            "training_objective_delta_minimum"
        ] = 0.0
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.run_confluence_training_stage(changed_gate, frame)

        changed_weight = self._protocol(frame)
        changed_weight["confluence_experiment"]["baseline_weights"] = {
            **BASELINE,
            "expansion": 0.25,
            "volume": 0.30,
        }
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_confluence.run_confluence_training_stage(changed_weight, frame)

    def test_training_selection_returns_only_one_deterministic_winner(self):
        baseline_candidate = calibration_ltf.WeightCandidate(
            "baseline", dict(BASELINE), None, None, 0.0
        )
        baseline = self._evaluation(baseline_candidate, 0.0, 0.0)
        first_candidate = calibration_ltf.WeightCandidate(
            "first",
            {**BASELINE, "expansion": 0.25, "volume": 0.30},
            "expansion",
            "volume",
            0.05,
        )
        second_candidate = calibration_ltf.WeightCandidate(
            "second",
            {**BASELINE, "expansion": 0.25, "oi": 0.25},
            "expansion",
            "oi",
            0.05,
        )
        first = self._evaluation(first_candidate, 0.02, -0.02)
        second = self._evaluation(second_candidate, 0.02, 0.01)
        below_gate_candidate = calibration_ltf.WeightCandidate(
            "below-gate",
            {**BASELINE, "volume": 0.20, "oi": 0.25},
            "volume",
            "oi",
            0.05,
        )
        below_gate = self._evaluation(below_gate_candidate, 0.009, 0.10)
        decisions, winner = calibration_confluence.select_confluence_training_winner(
            [baseline, first, second, below_gate]
        )
        self.assertEqual(len(decisions), 3)
        self.assertTrue(decisions[0].passed)
        self.assertTrue(decisions[1].passed)
        self.assertFalse(decisions[2].passed)
        self.assertEqual(
            decisions[2].reasons, ("objective_delta_below_minimum",)
        )
        self.assertEqual(winner.candidate.candidate_id, "first")
        self.assertAlmostEqual(decisions[0].safety_horizon_ic_delta, -0.02)

    def test_training_gate_accepts_exact_locked_minimum(self):
        baseline_candidate = calibration_ltf.WeightCandidate(
            "baseline", dict(BASELINE), None, None, 0.0
        )
        challenger_candidate = calibration_ltf.WeightCandidate(
            "at-gate",
            {**BASELINE, "expansion": 0.25, "volume": 0.30},
            "expansion",
            "volume",
            0.05,
        )
        decision = calibration_confluence.confluence_training_gate_decision(
            self._evaluation(baseline_candidate, 0.0, 0.0),
            self._evaluation(challenger_candidate, 0.01, -0.50),
        )
        self.assertTrue(decision.passed)
        self.assertEqual(decision.reasons, ())
        self.assertAlmostEqual(decision.objective_delta, 0.01)

    @staticmethod
    def _evaluation(
        candidate: calibration_ltf.WeightCandidate,
        objective: float,
        safety_ic: float,
    ) -> calibration_confluence.ConfluenceCandidateEvaluation:
        empty = pd.DataFrame()
        return calibration_confluence.ConfluenceCandidateEvaluation(
            candidate=candidate,
            mean_ic_by_horizon={1.0: objective, 4.0: objective, 24.0: safety_ic},
            objective_ic=objective,
            rank_ic_by_horizon={1.0: empty, 4.0: empty, 24.0: empty},
            component_rank_ic_by_horizon={1.0: {}, 4.0: {}, 24.0: {}},
        )


if __name__ == "__main__":
    unittest.main()
