"""Explicit opt-in smoke test against public Binance market data.

Normal test discovery skips this module's live test.  It never configures a
Discord client and disables research logging, so opting in can only populate
the dedicated alert database supplied for the smoke run.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from perpscanner.alert_store import AlertStore
from perpscanner.alert_worker import AlertWorker, WorkerConfig, scan_market


LIVE_SMOKE_ENV = "PERPSCANNER_RUN_LIVE_BINANCE_SMOKE"
LIVE_SMOKE_DB_ENV = "PERPSCANNER_LIVE_SMOKE_DB_PATH"


def _live_smoke_requested():
    explicitly_named = any(
        argument == __name__ or argument.startswith(f"{__name__}.")
        for argument in sys.argv[1:]
    )
    return os.environ.get(LIVE_SMOKE_ENV) == "1" or explicitly_named


@unittest.skipUnless(_live_smoke_requested(), "live Binance smoke is opt-in")
class LiveBinanceAlertSmokeTests(unittest.TestCase):
    def test_public_market_scan_with_delivery_and_research_writes_disabled(self):
        raw_path = os.environ.get(LIVE_SMOKE_DB_ENV)
        if raw_path:
            database_path = Path(raw_path)
        else:
            temporary_directory = tempfile.TemporaryDirectory()
            self.addCleanup(temporary_directory.cleanup)
            database_path = Path(temporary_directory.name) / "alerts-live-smoke.sqlite"
        self.assertFalse(database_path.exists(), "live smoke database must be new and isolated")

        config = WorkerConfig(
            database_path=database_path,
            use_ws_feed=False,
            delivery_enabled=False,
            research_logging_enabled=False,
        )
        captured = {}

        def capture_scan(worker_config):
            result = scan_market(worker_config)
            captured["scan"] = result
            return result

        worker = AlertWorker(config, scanner=capture_scan)
        report = worker.run_once()
        scan = captured.get("scan")

        self.assertTrue(report.scan_succeeded, report.error_code)
        self.assertIsNone(report.error_code)
        self.assertIsNotNone(scan)
        self.assertGreater(scan.universe_size, 1)
        self.assertFalse(scan.htf.empty)
        self.assertFalse(scan.ltf.empty)
        self.assertIn("symbol", scan.htf.columns)
        self.assertIn("symbol", scan.ltf.columns)
        self.assertGreater(len(set(scan.htf["symbol"]) & set(scan.ltf["symbol"])), 0)
        self.assertEqual(report.delivery_attempts, 0)
        self.assertEqual(report.deliveries_succeeded, 0)
        self.assertEqual(report.research_rows_written, 0)
        self.assertTrue(database_path.is_file())
        self.assertTrue(
            all(message.attempt_count == 0 for message in AlertStore(database_path).list_outbox())
        )

        print(
            "LIVE_BINANCE_ALERT_SMOKE "
            + json.dumps(
                {
                    "universe_size": scan.universe_size,
                    "htf_rows": len(scan.htf),
                    "ltf_rows": len(scan.ltf),
                    "overlap_rows": len(set(scan.htf["symbol"]) & set(scan.ltf["symbol"])),
                    "signals_evaluated": report.signals_evaluated,
                    "notifications_created": report.notifications_created,
                    "delivery_attempts": report.delivery_attempts,
                    "research_rows_written": report.research_rows_written,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
