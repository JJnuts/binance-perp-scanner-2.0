import json
import unittest

import pandas as pd

from perpscanner import calibration_htf, calibration_ltf


BASELINE = {
    "alpha": 0.30,
    "vol_adjusted": 0.20,
    "relative_strength": 0.20,
    "trend": 0.15,
    "oi": 0.10,
    "funding_trend_quality": 0.05,
    "premium_level_candidate": 0.0,
    "premium_roc_candidate": 0.0,
}
BOUNDS = {
    "alpha": [0.15, 0.40],
    "vol_adjusted": [0.10, 0.30],
    "relative_strength": [0.10, 0.30],
    "trend": [0.05, 0.25],
    "oi": [0.0, 0.15],
    "funding_trend_quality": [0.05, 0.25],
    "premium_level_candidate": [0.0, 0.15],
    "premium_roc_candidate": [0.0, 0.10],
}


class HTFCalibrationPrimitiveTests(unittest.TestCase):
    @staticmethod
    def _frame(scans=1, symbols=12) -> pd.DataFrame:
        rows = []
        for scan in range(scans):
            timestamp = pd.Timestamp("2026-01-01T00:00:00") + pd.Timedelta(
                days=2 * scan
            )
            for index in range(symbols):
                component = float(index * 100.0 / (symbols - 1))
                overextension = float(index % 3)
                momentum = component
                setup = max(momentum - 0.40 * overextension, 0.0)
                rows.append(
                    {
                        "scan_ts": timestamp,
                        "symbol": f"S{index:02d}",
                        "htf_alpha_score": component,
                        "vol_adjusted_score": component,
                        "htf_relative_strength_score": component,
                        "htf_trend_score": component,
                        "oi_score": component,
                        "funding_trend_quality_score": component,
                        "premium_bp": float(index),
                        "premium_roc_bp_h": float(index),
                        "overextension_score": overextension,
                        "htf_momentum_score": momentum,
                        "htf_setup_score": setup,
                    }
                )
        return pd.DataFrame(rows)

    def test_candidate_generation_is_locked_deterministic_and_unique(self):
        first = calibration_htf.generate_htf_candidates(BASELINE, BOUNDS)
        second = calibration_htf.generate_htf_candidates(BASELINE, BOUNDS)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 69)
        self.assertEqual(first[0].candidate_id, "baseline")
        self.assertEqual(len({item.candidate_id for item in first}), len(first))
        for item in first:
            calibration_ltf.validate_weight_vector(
                item.weights,
                BOUNDS,
                baseline=BASELINE,
                maximum_l1_distance=0.30,
            )

    def test_candidate_generation_rejects_wrong_factor_order(self):
        wrong = dict(BASELINE)
        wrong["relative_strength"] = wrong.pop("relative_strength")
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.generate_htf_candidates(wrong, BOUNDS)

    def test_baseline_momentum_and_setup_parity(self):
        parity = calibration_htf.verify_htf_baseline_score_parity(
            self._frame(), BASELINE
        )
        self.assertLessEqual(parity.momentum_max_abs_error, 1e-9)
        self.assertLessEqual(parity.setup_max_abs_error, 1e-9)

    def test_parity_rejects_changed_setup_formula(self):
        frame = self._frame()
        frame.loc[frame.index[-1], "htf_setup_score"] += 0.01
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.verify_htf_baseline_score_parity(frame, BASELINE)

    def test_premium_candidate_transform_is_ascending_within_scan(self):
        frame = self._frame(scans=2)
        weights = dict(BASELINE)
        weights["alpha"] -= 0.10
        weights["premium_level_candidate"] = 0.10
        scored = calibration_htf.score_htf_candidate(frame, weights)
        for _, group in scored.groupby("scan_ts"):
            ordered = group.sort_values("premium_bp")
            self.assertGreater(
                ordered["candidate_score"].iloc[-1],
                ordered["candidate_score"].iloc[0],
            )

    def test_scoring_rejects_non_finite_active_factor(self):
        frame = self._frame()
        frame.loc[0, "htf_alpha_score"] = float("inf")
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.score_htf_candidate(frame, BASELINE)

    def test_htf_objective_uses_24h_primary_and_4h_secondary(self):
        frame = self._frame(scans=2)
        candidate = calibration_htf.generate_htf_candidates(BASELINE, BOUNDS)[0]
        forward = {}
        for horizon, descending in ((1.0, False), (4.0, True), (24.0, False)):
            labels = frame[["scan_ts", "symbol"]].copy()
            values = []
            for _, group in labels.groupby("scan_ts", sort=False):
                sequence = list(range(len(group)))
                values.extend(reversed(sequence) if descending else sequence)
            labels["fwd_ret"] = [float(value) for value in values]
            forward[horizon] = labels
        result = calibration_htf.evaluate_htf_candidate_metrics(
            frame, forward, candidate
        )
        self.assertAlmostEqual(result.mean_ic_by_horizon[1.0], 1.0)
        self.assertAlmostEqual(result.mean_ic_by_horizon[4.0], -1.0)
        self.assertAlmostEqual(result.mean_ic_by_horizon[24.0], 1.0)
        self.assertAlmostEqual(result.objective_ic, 0.40)
        self.assertEqual(set(result.epoch_objective), {1, 2})

    def test_htf_evaluation_rejects_missing_safety_horizon(self):
        frame = self._frame()
        labels = frame[["scan_ts", "symbol"]].copy()
        labels["fwd_ret"] = range(len(labels))
        candidate = calibration_htf.generate_htf_candidates(BASELINE, BOUNDS)[0]
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.evaluate_htf_candidate_metrics(
                frame,
                {4.0: labels, 24.0: labels},
                candidate,
            )


class HTFCalibrationOrchestrationTests(unittest.TestCase):
    @staticmethod
    def _protocol() -> dict:
        return {
            "state": "locked_before_optimization",
            "optimization_performed": False,
            "protected_inputs": {
                "database": "unused.sqlite",
                "database_sha256": "0" * 64,
                "cutoff_inclusive_utc": "2026-01-08T23:59:59Z",
                "sealed_holdout_accessed": False,
            },
            "partitions": [
                {
                    "name": "training",
                    "start_inclusive_utc": "2026-01-01T00:00:00Z",
                    "end_inclusive_utc": "2026-01-05T23:59:59Z",
                },
                {
                    "name": "internal_validation",
                    "start_inclusive_utc": "2026-01-07T00:00:00Z",
                    "end_inclusive_utc": "2026-01-07T23:59:59Z",
                },
                {
                    "name": "locked_confirmation",
                    "start_inclusive_utc": "2026-01-08T00:00:00Z",
                    "end_inclusive_utc": "2026-01-08T23:59:59Z",
                },
            ],
            "ranking_experiments": {
                "ltf": {"experiment_id": "alt-ltf-rank-v1"},
                "htf": {
                    "experiment_id": "alt-htf-rank-v1",
                    "baseline_weights": BASELINE,
                    "bounds": BOUNDS,
                },
                "candidate_generation": {
                    "maximum_l1_distance_from_baseline": 0.30,
                    "maximum_candidates_per_experiment_including_baseline": 127,
                },
            },
        }

    @staticmethod
    def _training_frame() -> pd.DataFrame:
        rows = []
        for epoch_start in (
            pd.Timestamp("2026-01-01T00:00:00"),
            pd.Timestamp("2026-01-04T00:00:00"),
        ):
            for hours, return_scale in (
                (0, 0.0),
                (1, 0.001),
                (4, 0.002),
                (24, 0.005),
            ):
                timestamp = epoch_start + pd.Timedelta(hours=hours)
                for index in range(12):
                    component = float(index * 100.0 / 11.0)
                    price = 100.0 * (1.0 + index * return_scale)
                    rows.append(
                        {
                            "scan_ts": timestamp,
                            "symbol": f"S{index:02d}",
                            "price": price,
                            "htf_alpha_score": component,
                            "vol_adjusted_score": component,
                            "htf_relative_strength_score": component,
                            "htf_trend_score": component,
                            "oi_score": component,
                            "funding_trend_quality_score": component,
                            "premium_bp": float(index),
                            "premium_roc_bp_h": float(index),
                            "overextension_score": 0.0,
                            "htf_momentum_score": component,
                            "htf_setup_score": component,
                        }
                    )
        return pd.DataFrame(rows)

    @staticmethod
    def _candidate(
        candidate_id: str,
        source: str = "alpha",
        destination: str = "funding_trend_quality",
    ) -> calibration_ltf.WeightCandidate:
        weights = dict(BASELINE)
        weights[source] -= 0.05
        weights[destination] += 0.05
        return calibration_ltf.WeightCandidate(
            candidate_id, weights, source, destination, 0.05
        )

    @staticmethod
    def _evaluation(
        candidate: calibration_ltf.WeightCandidate,
        objective: float,
        primary_ic: float,
        dashboard: float,
        epochs: dict[int, float],
    ) -> calibration_htf.HTFCandidateEvaluation:
        empty = pd.DataFrame()
        return calibration_htf.HTFCandidateEvaluation(
            candidate=candidate,
            mean_ic_by_horizon={1.0: 0.0, 4.0: objective, 24.0: primary_ic},
            dashboard_excess_by_horizon={1.0: 0.0, 4.0: 0.0, 24.0: dashboard},
            objective_ic=objective,
            epoch_objective=epochs,
            rank_ic_by_horizon={1.0: empty, 4.0: empty, 24.0: empty},
            dashboard_by_horizon={1.0: empty, 4.0: empty, 24.0: empty},
        )

    def test_training_runner_and_report_are_training_only(self):
        locked = self._protocol()
        frame = self._training_frame()
        locked["partitions"][0]["metric"] = {
            "rows": len(frame),
            "scans": frame["scan_ts"].nunique(),
            "symbols": frame["symbol"].nunique(),
        }
        result = calibration_htf.run_htf_training_stage(locked, frame)
        self.assertEqual(result.partition_name, "training")
        self.assertEqual(result.candidate_count, 69)
        self.assertLessEqual(result.baseline_parity.momentum_max_abs_error, 1e-9)
        self.assertLessEqual(result.baseline_parity.setup_max_abs_error, 1e-9)
        self.assertIsNone(result.winner)
        self.assertEqual(len(result.decisions), 68)

        report = calibration_htf.build_htf_training_comparison_report(
            result, bootstrap_draws=100, seed=11
        )
        self.assertEqual(report["experiment_id"], "alt-htf-rank-v1")
        self.assertEqual(report["candidate_count"], 69)
        self.assertEqual(report["challenger_count"], 68)
        self.assertIsNone(report["winner_candidate_id"])
        self.assertEqual(
            report["challengers"][0]["paired_active_day_rank_ic"]["24.0"][
                "common_active_days"
            ],
            2,
        )
        json.dumps(report, sort_keys=True)

        escaped = pd.concat(
            [
                frame,
                pd.DataFrame(
                    [
                        {
                            **frame.iloc[0].to_dict(),
                            "scan_ts": pd.Timestamp("2026-01-07T00:00:00"),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.run_htf_training_stage(locked, escaped)

    def test_training_winner_uses_24h_gate_and_worst_epoch(self):
        baseline_candidate = calibration_ltf.WeightCandidate(
            "baseline", dict(BASELINE), None, None, 0.0
        )
        baseline = self._evaluation(
            baseline_candidate, 0.0, 0.0, 0.0, {1: 0.0, 2: 0.0}
        )
        stable = self._evaluation(
            self._candidate("stable"), 0.006, 0.010, 0.0010, {1: 0.004, 2: 0.005}
        )
        fragile = self._evaluation(
            self._candidate("fragile", "vol_adjusted", "funding_trend_quality"),
            0.010,
            0.012,
            0.0012,
            {1: 0.020, 2: -0.001},
        )
        rejected = self._evaluation(
            self._candidate("rejected", "trend", "funding_trend_quality"),
            0.001,
            0.005,
            0.0001,
            {1: 0.002, 2: 0.001},
        )
        decisions, winner = calibration_htf.select_htf_training_winner(
            [baseline, stable, fragile, rejected]
        )
        by_id = {decision.candidate_id: decision for decision in decisions}
        self.assertTrue(by_id["stable"].passed)
        self.assertTrue(by_id["fragile"].passed)
        self.assertFalse(by_id["rejected"].passed)
        self.assertEqual(winner.candidate.candidate_id, "stable")

        negative_primary = self._evaluation(
            self._candidate("negative", "oi", "funding_trend_quality"),
            0.010,
            -0.001,
            0.001,
            {1: 0.010, 2: 0.010},
        )
        decision = calibration_htf.htf_training_gate_decision(
            baseline, negative_primary
        )
        self.assertIn("primary_ic_below_minimum", decision.reasons)

    def test_training_runner_rejects_partial_locked_partition(self):
        locked = self._protocol()
        frame = self._training_frame()
        locked["partitions"][0]["metric"] = {
            "rows": len(frame) + 1,
            "scans": frame["scan_ts"].nunique(),
            "symbols": frame["symbol"].nunique(),
        }
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.run_htf_training_stage(locked, frame)

    def test_training_runner_rejects_changed_locked_htf_horizon(self):
        locked = self._protocol()
        locked["ranking_experiments"]["htf"]["primary_horizon_hours"] = 4.0
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_htf.run_htf_training_stage(locked, self._training_frame())


if __name__ == "__main__":
    unittest.main()
