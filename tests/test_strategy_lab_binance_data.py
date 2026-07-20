import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from perpscanner.strategy_lab.binance_data import (
    DataIntegrityError,
    DownloadSpec,
    TrustedKlineStore,
)


INTERVAL_MS = 300_000


def kline(open_ms: int, price: int = 100, *, close_ms: int | None = None) -> list[object]:
    close_time = close_ms if close_ms is not None else open_ms + INTERVAL_MS - 1
    return [
        open_ms,
        str(price),
        str(price + 2),
        str(price - 2),
        str(price + 1),
        "10.5",
        close_time,
        "1055.25",
        42,
        "5.1",
        "512.5",
        "0",
    ]


class GeneratedClient:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows
        self.calls: list[int] = []

    def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[list[object]]:
        del symbol, interval
        self.calls.append(start_ms)
        return [
            row
            for row in self.rows
            if start_ms <= int(row[0]) < end_ms
        ][:limit]


class TrustedBinanceDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.end = self.start + timedelta(minutes=30)
        self.spec = DownloadSpec.create(
            symbol="BTCUSDT",
            interval="5m",
            start=self.start,
            end=self.end,
        )
        self.rows = [
            kline(int((self.start + timedelta(minutes=5 * index)).timestamp() * 1000), 100 + index)
            for index in range(6)
        ]

    def test_interrupted_download_resumes_from_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TrustedKlineStore(tmp, self.spec, page_limit=2)
            first = store.download(GeneratedClient(self.rows), max_pages=1, now=self.end)
            self.assertFalse(first["download_complete"])
            self.assertEqual(len(first["pages"]), 1)

            resumed_client = GeneratedClient(self.rows)
            manifest = store.download(resumed_client, now=self.end)
            self.assertEqual(
                resumed_client.calls[0],
                int((self.start + timedelta(minutes=10)).timestamp() * 1000),
            )
            self.assertEqual(manifest["status"], "passed")
            self.assertEqual(manifest["quality"]["normalized_row_count"], 6)
            self.assertEqual(manifest["files"]["raw"]["page_count"], 3)
            self.assertEqual(store.verify_manifest()["status"], "passed")

    def test_completed_download_rerun_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TrustedKlineStore(tmp, self.spec, page_limit=3)
            store.download(GeneratedClient(self.rows), now=self.end)
            manifest_before = store.manifest_path.read_bytes()
            state_before = store.state_path.read_bytes()
            client = GeneratedClient(self.rows)

            result = store.download(client, now=self.end)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(client.calls, [])
            self.assertEqual(store.manifest_path.read_bytes(), manifest_before)
            self.assertEqual(store.state_path.read_bytes(), state_before)

    def test_gap_and_duplicate_fail_quality_gate_with_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_rows = [
                self.rows[0],
                self.rows[1],
                self.rows[1],
                self.rows[3],
                self.rows[4],
                self.rows[5],
            ]
            store = TrustedKlineStore(tmp, self.spec, page_limit=10)
            with self.assertRaises(DataIntegrityError):
                store.download(GeneratedClient(bad_rows), now=self.end)

            quality = json.loads(store.quality_path.read_text(encoding="utf-8"))
            self.assertEqual(quality["status"], "failed")
            self.assertEqual(quality["duplicate_count"], 1)
            self.assertEqual(quality["gap_count"], 1)
            self.assertEqual(
                quality["gap_examples_ms"][0],
                int((self.start + timedelta(minutes=10)).timestamp() * 1000),
            )

    def test_unclosed_bar_is_excluded_and_coverage_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cutoff = self.end - timedelta(minutes=2)
            rows = list(self.rows)
            rows[-1] = kline(
                int((self.end - timedelta(minutes=5)).timestamp() * 1000),
                105,
                close_ms=int((self.end + timedelta(minutes=1)).timestamp() * 1000),
            )
            store = TrustedKlineStore(tmp, self.spec, page_limit=10)
            with self.assertRaises(DataIntegrityError):
                store.download(GeneratedClient(rows), now=cutoff)
            quality = json.loads(store.quality_path.read_text(encoding="utf-8"))
            self.assertEqual(quality["unclosed_rows_removed"], 1)
            self.assertEqual(quality["gap_count"], 1)

    def test_manifest_reconstructs_exact_csv_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TrustedKlineStore(tmp, self.spec, page_limit=3)
            manifest = store.download(GeneratedClient(self.rows), now=self.end)
            reconstructed = Path(tmp) / "rebuilt.csv"
            store.reconstruct(reconstructed)
            self.assertEqual(reconstructed.read_bytes(), store.normalized_path.read_bytes())
            self.assertEqual(
                manifest["files"]["normalized"]["sha256"],
                hashlib.sha256(reconstructed.read_bytes()).hexdigest(),
            )

            first_page = store.dataset_dir / manifest["files"]["raw"]["pages"][0]["file"]
            first_page.write_bytes(first_page.read_bytes() + b" ")
            with self.assertRaises(DataIntegrityError):
                store.verify_manifest()

    def test_request_boundaries_must_be_aligned_and_utc(self) -> None:
        with self.assertRaises(ValueError):
            DownloadSpec.create(
                symbol="BTCUSDT",
                interval="5m",
                start="2026-01-01T00:01:00Z",
                end="2026-01-01T01:00:00Z",
            )
        with self.assertRaises(ValueError):
            DownloadSpec.create(
                symbol="BTCUSDT",
                interval="5m",
                start="2026-01-01T00:00:00",
                end="2026-01-01T01:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
