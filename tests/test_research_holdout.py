import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from perpscanner import research


class ResearchHoldoutBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original = (
            research.RESEARCH_DB_PATH,
            research.RESEARCH_CALIBRATION_DB_PATH,
            research.RESEARCH_HOLDOUT_CUTOFF_UTC,
            research.RESEARCH_CALIBRATION_ENFORCE_SHA256,
            research._utc_now_naive,
        )
        root = Path(self._tmpdir.name)
        research.RESEARCH_DB_PATH = root / "live.sqlite"
        research.RESEARCH_CALIBRATION_DB_PATH = root / "frozen.sqlite"
        research.RESEARCH_HOLDOUT_CUTOFF_UTC = "2026-09-03T12:01:29.372069"
        research.RESEARCH_CALIBRATION_ENFORCE_SHA256 = False

    def tearDown(self):
        (
            research.RESEARCH_DB_PATH,
            research.RESEARCH_CALIBRATION_DB_PATH,
            research.RESEARCH_HOLDOUT_CUTOFF_UTC,
            research.RESEARCH_CALIBRATION_ENFORCE_SHA256,
            research._utc_now_naive,
        ) = self._original
        self._tmpdir.cleanup()

    @staticmethod
    def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
        connection = sqlite3.connect(path)
        try:
            pd.DataFrame(rows).to_sql(research.METRIC_TABLE, connection, if_exists="replace", index=False)
            connection.commit()
        finally:
            connection.close()

    def test_development_read_uses_frozen_store_and_inclusive_cutoff(self):
        self._write_rows(
            research.RESEARCH_CALIBRATION_DB_PATH,
            [
                {"scan_ts": "2026-09-03T12:01:29.372068", "symbol": "BEFORE", "price": 1.0},
                {"scan_ts": "2026-09-03T12:01:29.372069", "symbol": "AT_CUTOFF", "price": 2.0},
                {"scan_ts": "2026-09-03T12:01:29.372070", "symbol": "AFTER", "price": 3.0},
            ],
        )
        self._write_rows(
            research.RESEARCH_DB_PATH,
            [{"scan_ts": "2026-09-03T11:00:00", "symbol": "LIVE_ONLY", "price": 9.0}],
        )

        loaded = research._load_table(research.METRIC_TABLE)

        self.assertEqual(loaded["symbol"].tolist(), ["BEFORE", "AT_CUTOFF"])
        self.assertNotIn("AFTER", loaded["symbol"].tolist())
        self.assertNotIn("LIVE_ONLY", loaded["symbol"].tolist())

    def test_calibration_connection_rejects_writes(self):
        self._write_rows(
            research.RESEARCH_CALIBRATION_DB_PATH,
            [{"scan_ts": "2026-09-03T12:00:00", "symbol": "AAA", "price": 1.0}],
        )

        connection = research._connect_calibration()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    f"INSERT INTO {research.METRIC_TABLE} (scan_ts, symbol, price) VALUES (?, ?, ?)",
                    ("2026-09-03T12:00:01", "BBB", 2.0),
                )
        finally:
            connection.close()

    def test_missing_frozen_store_fails_closed_without_live_fallback(self):
        self._write_rows(
            research.RESEARCH_DB_PATH,
            [{"scan_ts": "2026-09-03T11:00:00", "symbol": "LIVE_ONLY", "price": 9.0}],
        )

        loaded = research._load_table(research.METRIC_TABLE)

        self.assertTrue(loaded.empty)

    def test_checksum_mismatch_fails_closed(self):
        self._write_rows(
            research.RESEARCH_CALIBRATION_DB_PATH,
            [{"scan_ts": "2026-09-03T12:00:00", "symbol": "AAA", "price": 1.0}],
        )
        original_hash = research.RESEARCH_CALIBRATION_DB_SHA256
        research.RESEARCH_CALIBRATION_ENFORCE_SHA256 = True
        research.RESEARCH_CALIBRATION_DB_SHA256 = "0" * 64
        research._CALIBRATION_INTEGRITY_CACHE.clear()
        try:
            self.assertTrue(research._load_table(research.METRIC_TABLE).empty)
            with self.assertRaises(RuntimeError):
                research._connect_calibration()
        finally:
            research.RESEARCH_CALIBRATION_DB_SHA256 = original_hash
            research._CALIBRATION_INTEGRITY_CACHE.clear()

    def test_post_cutoff_logging_continues_only_in_live_store(self):
        research._utc_now_naive = lambda: pd.Timestamp("2026-09-03T13:00:00")
        frame = pd.DataFrame({"symbol": ["NEWUSDT"], "price": [10.0], "momentum_score": [55.0]})

        written = research.log_scan_snapshot(frame, None)

        self.assertEqual(written[research.METRIC_TABLE], 1)
        connection = sqlite3.connect(research.RESEARCH_DB_PATH)
        try:
            stored = connection.execute(
                f"SELECT scan_ts, symbol FROM {research.METRIC_TABLE}"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(stored, [("2026-09-03T13:00:00", "NEWUSDT")])
        self.assertFalse(research.RESEARCH_CALIBRATION_DB_PATH.exists())

    def test_machine_readable_policy_matches_runtime_constants(self):
        policy_path = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "calibration"
            / "holdout_policy_20260903T120129Z.json"
        )
        policy = json.loads(policy_path.read_text(encoding="utf-8"))

        self.assertEqual(policy["policy_id"], research.RESEARCH_HOLDOUT_POLICY_ID)
        self.assertEqual(policy["cutoff_utc"], f"{research.RESEARCH_HOLDOUT_CUTOFF_UTC}Z")
        self.assertEqual(
            policy["development_source"]["required_sha256"],
            research.RESEARCH_CALIBRATION_DB_SHA256,
        )
        self.assertTrue(policy["development_source"]["sha256_required_before_open"])
        self.assertEqual(policy["split_rules"]["development"], "scan_ts <= cutoff_utc")
        self.assertEqual(policy["split_rules"]["sealed_holdout"], "scan_ts > cutoff_utc")
        self.assertFalse(policy["ordinary_research_reports"]["post_cutoff_rows_visible"])
        self.assertFalse(policy["ordinary_research_reports"]["fallback_to_live_store"])


if __name__ == "__main__":
    unittest.main()
