import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from perpscanner import calibration_ltf
from perpscanner.research import _spearman_corr


BASELINE = {
    "alpha": 0.25,
    "relative_strength": 0.15,
    "volume": 0.25,
    "trend": 0.20,
    "oi": 0.10,
    "funding_quality": 0.05,
    "premium_level_candidate": 0.0,
    "premium_roc_candidate": 0.0,
}
BOUNDS = {
    "alpha": [0.10, 0.35],
    "relative_strength": [0.05, 0.25],
    "volume": [0.10, 0.35],
    "trend": [0.10, 0.30],
    "oi": [0.0, 0.15],
    "funding_quality": [0.05, 0.25],
    "premium_level_candidate": [0.0, 0.15],
    "premium_roc_candidate": [0.0, 0.10],
}


def protocol(database: Path, digest: str) -> dict:
    return {
        "state": "locked_before_optimization",
        "optimization_performed": False,
        "protected_inputs": {
            "database": str(database),
            "database_sha256": digest,
            "cutoff_inclusive_utc": "2026-01-04T23:59:59Z",
            "sealed_holdout_accessed": False,
        },
        "partitions": [
            {
                "name": "training",
                "start_inclusive_utc": "2026-01-01T00:00:00Z",
                "end_inclusive_utc": "2026-01-01T23:59:59Z",
            },
            {
                "name": "internal_validation",
                "start_inclusive_utc": "2026-01-03T00:00:00Z",
                "end_inclusive_utc": "2026-01-03T23:59:59Z",
            },
            {
                "name": "locked_confirmation",
                "start_inclusive_utc": "2026-01-04T00:00:00Z",
                "end_inclusive_utc": "2026-01-04T23:59:59Z",
            },
        ],
        "ranking_experiments": {
            "ltf": {
                "experiment_id": "alt-ltf-rank-v1",
                "baseline_weights": BASELINE,
                "bounds": BOUNDS,
            },
            "candidate_generation": {
                "maximum_l1_distance_from_baseline": 0.30,
                "maximum_candidates_per_experiment_including_baseline": 127,
            },
        },
    }


class LTFCalibrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _database(self) -> Path:
        path = self.root / "frozen.sqlite"
        connection = sqlite3.connect(path)
        try:
            pd.DataFrame(
                [
                    {"scan_ts": "2026-01-01T00:00:00", "symbol": "TRAIN", "price": 100.0},
                    {"scan_ts": "2026-01-01T01:00:00", "symbol": "TRAIN", "price": 110.0},
                    {"scan_ts": "2026-01-03T00:00:00", "symbol": "VALID", "price": 200.0},
                ]
            ).to_sql("metric_snapshots", connection, index=False)
            connection.commit()
        finally:
            connection.close()
        return path

    def test_locked_protocol_checksum_and_state_fail_closed(self):
        database = self._database()
        digest = calibration_ltf.sha256_file(database)
        payload = protocol(database, digest)
        path = self.root / "protocol.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        protocol_digest = hashlib.sha256(path.read_bytes()).hexdigest()

        loaded = calibration_ltf.load_locked_protocol(path, protocol_digest)
        self.assertEqual(loaded["state"], "locked_before_optimization")
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.load_locked_protocol(path, "0" * 64)

        payload["optimization_performed"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
        changed_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.load_locked_protocol(path, changed_digest)

    def test_database_is_checksum_verified_and_read_only(self):
        database = self._database()
        digest = calibration_ltf.sha256_file(database)
        with calibration_ltf.connect_verified_database(database, digest) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0], 3)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO metric_snapshots (scan_ts, symbol, price) VALUES (?, ?, ?)",
                    ("2026-01-01T02:00:00", "WRITE", 1.0),
                )
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            with calibration_ltf.connect_verified_database(database, "f" * 64):
                pass

    def test_partition_roles_and_sql_boundaries_are_enforced(self):
        database = self._database()
        digest = calibration_ltf.sha256_file(database)
        locked = protocol(database, digest)
        training = calibration_ltf.resolve_partition(
            locked, "training", purpose="candidate_selection"
        )
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.resolve_partition(
                locked, "internal_validation", purpose="candidate_selection"
            )
        with calibration_ltf.connect_verified_database(database, digest) as connection:
            loaded = calibration_ltf.load_protocol_partition_frame(
                connection,
                locked,
                "training",
                purpose="candidate_selection",
                table="metric_snapshots",
                columns=("scan_ts", "symbol", "price"),
            )
            self.assertEqual(set(loaded["symbol"]), {"TRAIN"})
            with self.assertRaises(calibration_ltf.CalibrationGuardError):
                calibration_ltf.load_protocol_partition_frame(
                    connection,
                    locked,
                    "training",
                    purpose="candidate_selection",
                    table="metric_snapshots; DROP TABLE metric_snapshots",
                    columns=("scan_ts", "symbol"),
                )

    def test_forward_labels_cannot_cross_partition_boundary(self):
        locked = protocol(Path("unused.sqlite"), "0" * 64)
        prices = pd.DataFrame(
            {
                "scan_ts": pd.to_datetime(
                    ["2026-01-01T23:00:00", "2026-01-02T00:00:01"]
                ),
                "symbol": ["AAA", "AAA"],
                "price": [100.0, 110.0],
            }
        )
        result = calibration_ltf.build_protocol_partition_forward_returns(
            prices,
            1.0,
            locked,
            "training",
            purpose="candidate_selection",
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result["fwd_ret"].isna().all())

    def test_forward_labels_reject_non_finite_prices(self):
        locked = protocol(Path("unused.sqlite"), "0" * 64)
        prices = pd.DataFrame(
            {
                "scan_ts": pd.to_datetime(
                    ["2026-01-01T00:00:00", "2026-01-01T01:00:00"]
                ),
                "symbol": ["AAA", "AAA"],
                "price": [100.0, float("inf")],
            }
        )
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.build_protocol_partition_forward_returns(
                prices,
                1.0,
                locked,
                "training",
                purpose="candidate_selection",
            )

    def test_candidate_generation_is_deterministic_bounded_and_single_transfer(self):
        first = calibration_ltf.generate_single_transfer_candidates(BASELINE, BOUNDS)
        second = calibration_ltf.generate_single_transfer_candidates(BASELINE, BOUNDS)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 71)
        self.assertEqual(first[0].candidate_id, "baseline")
        self.assertLessEqual(len(first), 127)
        self.assertEqual(len({candidate.candidate_id for candidate in first}), len(first))
        for candidate in first:
            calibration_ltf.validate_weight_vector(
                candidate.weights,
                BOUNDS,
                baseline=BASELINE,
                maximum_l1_distance=0.30,
            )
            changed = [
                factor
                for factor in BASELINE
                if abs(candidate.weights[factor] - BASELINE[factor]) > 1e-12
            ]
            self.assertIn(len(changed), {0, 2})

    def test_candidate_limit_fails_instead_of_truncating(self):
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.generate_single_transfer_candidates(
                BASELINE, BOUNDS, maximum_candidates=2
            )


class LTFCalibrationMetricTests(unittest.TestCase):
    @staticmethod
    def _frame(symbols=12) -> pd.DataFrame:
        rows = []
        timestamp = pd.Timestamp("2026-01-01T00:00:00")
        for index in range(symbols):
            component = float(index * 100.0 / (symbols - 1))
            rows.append(
                {
                    "scan_ts": timestamp,
                    "symbol": f"S{index:02d}",
                    "alpha_score": component,
                    "relative_strength_score": component,
                    "volume_score": component,
                    "trend_score": component,
                    "oi_score": component,
                    "funding_quality_score": component,
                    "premium_bp": float(index),
                    "premium_roc_bp_h": float(index),
                    "overextension_score": 0.0,
                    "momentum_score": component,
                }
            )
        return pd.DataFrame(rows)

    def test_baseline_score_parity_and_ascending_premium_transform(self):
        frame = self._frame()
        self.assertLessEqual(
            calibration_ltf.verify_baseline_score_parity(frame, BASELINE), 1e-9
        )
        premium_weights = dict(BASELINE)
        premium_weights["alpha"] -= 0.10
        premium_weights["premium_level_candidate"] = 0.10
        scored = calibration_ltf.score_ltf_candidate(frame, premium_weights)
        self.assertGreater(
            scored.sort_values("premium_bp")["candidate_score"].iloc[-1],
            scored.sort_values("premium_bp")["candidate_score"].iloc[0],
        )

    def test_rank_ic_matches_existing_spearman_semantics(self):
        scored = calibration_ltf.score_ltf_candidate(self._frame(), BASELINE)
        forward = scored[["scan_ts", "symbol"]].copy()
        forward["fwd_ret"] = range(len(forward))
        report = calibration_ltf.cross_section_rank_ic(scored, forward)
        self.assertEqual(len(report), 1)
        expected = _spearman_corr(scored["candidate_score"], forward["fwd_ret"])
        self.assertAlmostEqual(report.iloc[0]["rank_ic"], expected)
        self.assertAlmostEqual(report.iloc[0]["rank_ic"], 1.0)

    def test_exact_dashboard_selection_uses_saved_ltf_sort_and_filters(self):
        frame = self._frame()
        frame.loc[frame.index[-1], "overextension_score"] = 100.0
        scored = calibration_ltf.score_ltf_candidate(frame, BASELINE)
        forward = scored[["scan_ts", "symbol"]].copy()
        forward["fwd_ret"] = pd.Series(range(len(forward)), dtype=float) / 100.0
        report = calibration_ltf.exact_dashboard_excess(scored, forward)
        self.assertEqual(len(report), 1)
        self.assertGreater(report.iloc[0]["selected"], 0)
        self.assertGreater(report.iloc[0]["excess_ret"], 0.0)

    def test_weighted_objective_fails_when_a_horizon_is_missing(self):
        one_hour = pd.DataFrame({"rank_ic": [0.10, 0.20]})
        four_hour = pd.DataFrame({"rank_ic": [0.00, 0.10]})
        objective = calibration_ltf.weighted_ic_objective(
            {1.0: one_hour, 4.0: four_hour}, {1.0: 0.70, 4.0: 0.30}
        )
        self.assertAlmostEqual(objective, 0.12)
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.weighted_ic_objective(
                {1.0: one_hour}, {1.0: 0.70, 4.0: 0.30}
            )


class LTFCalibrationOrchestrationTests(unittest.TestCase):
    @staticmethod
    def _training_protocol() -> dict:
        payload = protocol(Path("unused.sqlite"), "0" * 64)
        payload["protected_inputs"]["cutoff_inclusive_utc"] = "2026-01-08T23:59:59Z"
        payload["partitions"] = [
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
        ]
        return payload

    @staticmethod
    def _training_frame() -> pd.DataFrame:
        rows = []
        for epoch_start in (
            pd.Timestamp("2026-01-01T00:00:00"),
            pd.Timestamp("2026-01-04T00:00:00"),
        ):
            for hours, return_scale in ((0, 0.0), (1, 0.001), (4, 0.002), (24, 0.005)):
                timestamp = epoch_start + pd.Timedelta(hours=hours)
                for index in range(12):
                    component = float(index * 100.0 / 11.0)
                    price = 100.0 * (1.0 + index * return_scale)
                    rows.append(
                        {
                            "scan_ts": timestamp,
                            "symbol": f"S{index:02d}",
                            "price": price,
                            "alpha_score": component,
                            "relative_strength_score": component,
                            "volume_score": component,
                            "trend_score": component,
                            "oi_score": component,
                            "funding_quality_score": component,
                            "premium_bp": float(index),
                            "premium_roc_bp_h": float(index),
                            "overextension_score": 0.0,
                            "momentum_score": component,
                        }
                    )
        return pd.DataFrame(rows)

    @staticmethod
    def _candidate(candidate_id: str, source="alpha", destination="funding_quality"):
        weights = dict(BASELINE)
        weights[source] -= 0.05
        weights[destination] += 0.05
        return calibration_ltf.WeightCandidate(
            candidate_id,
            weights,
            source,
            destination,
            0.05,
        )

    @staticmethod
    def _evaluation(candidate, objective, primary_ic, dashboard, epochs):
        empty = pd.DataFrame()
        return calibration_ltf.LTFCandidateEvaluation(
            candidate=candidate,
            mean_ic_by_horizon={1.0: primary_ic, 4.0: objective, 24.0: 0.0},
            dashboard_excess_by_horizon={1.0: dashboard, 4.0: 0.0, 24.0: 0.0},
            objective_ic=objective,
            epoch_objective=epochs,
            rank_ic_by_horizon={1.0: empty, 4.0: empty, 24.0: empty},
            dashboard_by_horizon={1.0: empty, 4.0: empty, 24.0: empty},
        )

    def test_training_runner_uses_only_training_rows_and_baseline_can_run_alone(self):
        locked = self._training_protocol()
        result = calibration_ltf.run_training_stage(
            locked, self._training_frame()
        )
        self.assertEqual(result.partition_name, "training")
        self.assertEqual(result.candidate_count, 71)
        self.assertLessEqual(result.baseline_max_abs_error, 1e-9)
        self.assertIsNone(result.winner)
        self.assertEqual(len(result.decisions), 70)

        report = calibration_ltf.build_training_comparison_report(
            result, bootstrap_draws=100, seed=11
        )
        self.assertEqual(report["stage"], "training")
        self.assertEqual(report["candidate_count"], 71)
        self.assertEqual(report["challenger_count"], 70)
        self.assertIsNone(report["winner_candidate_id"])
        self.assertEqual(
            report["challengers"][0]["paired_active_day_rank_ic"]["1.0"][
                "common_active_days"
            ],
            2,
        )
        json.dumps(report, sort_keys=True)

        escaped = pd.concat(
            [
                self._training_frame(),
                pd.DataFrame(
                    [
                        {
                            **self._training_frame().iloc[0].to_dict(),
                            "scan_ts": pd.Timestamp("2026-01-07T00:00:00"),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.run_training_stage(locked, escaped)

    def test_training_winner_is_gated_then_chosen_by_worst_epoch(self):
        baseline_candidate = calibration_ltf.WeightCandidate(
            "baseline", dict(BASELINE), None, None, 0.0
        )
        baseline = self._evaluation(baseline_candidate, 0.0, 0.0, 0.0, {1: 0.0, 2: 0.0})
        stable = self._evaluation(
            self._candidate("stable"), 0.006, 0.010, 0.0010, {1: 0.004, 2: 0.005}
        )
        fragile = self._evaluation(
            self._candidate("fragile", "volume", "funding_quality"),
            0.010,
            0.012,
            0.0012,
            {1: 0.020, 2: -0.001},
        )
        rejected = self._evaluation(
            self._candidate("rejected", "trend", "funding_quality"),
            0.001,
            0.005,
            0.0001,
            {1: 0.002, 2: 0.001},
        )
        decisions, winner = calibration_ltf.select_training_winner(
            [baseline, stable, fragile, rejected]
        )
        by_id = {decision.candidate_id: decision for decision in decisions}
        self.assertTrue(by_id["stable"].passed)
        self.assertTrue(by_id["fragile"].passed)
        self.assertFalse(by_id["rejected"].passed)
        self.assertEqual(winner.candidate.candidate_id, "stable")

    def test_paired_active_day_report_uses_candidate_minus_baseline(self):
        baseline = pd.DataFrame(
            {
                "scan_ts": pd.to_datetime(
                    ["2026-01-01T00:00:00", "2026-01-01T01:00:00", "2026-01-02T00:00:00"]
                ),
                "rank_ic": [0.0, 0.0, 0.0],
            }
        )
        candidate = baseline.copy()
        candidate["rank_ic"] = [0.10, 0.10, -0.05]
        report = calibration_ltf.paired_active_day_comparison(
            baseline,
            candidate,
            value_column="rank_ic",
            bootstrap_draws=500,
            seed=7,
        )
        self.assertEqual(report["common_active_days"], 2)
        self.assertAlmostEqual(report["mean_daily_delta"], 0.025)
        self.assertAlmostEqual(report["positive_day_fraction"], 0.5)
        self.assertLessEqual(report["ci95_low"], report["mean_daily_delta"])
        self.assertGreaterEqual(report["ci95_high"], report["mean_daily_delta"])

    def test_training_runner_rejects_partial_locked_partition(self):
        locked = self._training_protocol()
        locked["partitions"][0]["metric"] = {
            "rows": len(self._training_frame()) + 1,
            "scans": self._training_frame()["scan_ts"].nunique(),
        }
        with self.assertRaises(calibration_ltf.CalibrationGuardError):
            calibration_ltf.run_training_stage(
                locked,
                self._training_frame(),
            )


if __name__ == "__main__":
    unittest.main()
