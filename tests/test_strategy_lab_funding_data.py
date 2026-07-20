import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from perpscanner.strategy_lab.binance_data import DataIntegrityError
from perpscanner.strategy_lab.funding_data import TrustedFundingStore


class FakeFundingClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch(self, *, symbol, start_ms, end_ms, limit):
        del symbol
        self.calls.append(start_ms)
        return [
            row
            for row in self.rows
            if start_ms <= int(row["fundingTime"]) < end_ms
        ][:limit]


class TrustedFundingTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.end = self.start + timedelta(hours=24)
        self.rows = []
        for offset, rate in ((0, "0.0001"), (8, "-0.0002"), (16, "0.0003")):
            timestamp = int((self.start + timedelta(hours=offset)).timestamp() * 1000)
            self.rows.append(
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": timestamp,
                    "fundingRate": rate,
                    "markPrice": "100000.0",
                }
            )

    def test_funding_download_resumes_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TrustedFundingStore(
                tmp,
                symbol="BTCUSDT",
                start=self.start.isoformat(),
                end=self.end.isoformat(),
                page_limit=2,
            )
            paused = store.download(FakeFundingClient(self.rows), max_pages=1)
            self.assertFalse(paused["download_complete"])
            client = FakeFundingClient(self.rows)
            manifest = store.download(client)
            self.assertEqual(manifest["status"], "passed")
            self.assertEqual(manifest["quality"]["normalized_row_count"], 3)
            self.assertEqual(len(store.load_frame()), 3)

    def test_missing_funding_timestamp_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TrustedFundingStore(
                tmp,
                symbol="BTCUSDT",
                start=self.start.isoformat(),
                end=self.end.isoformat(),
                page_limit=10,
            )
            with self.assertRaises(DataIntegrityError):
                store.download(FakeFundingClient([self.rows[0], self.rows[2]]))
            self.assertTrue(store.quality_path.exists())


if __name__ == "__main__":
    unittest.main()
