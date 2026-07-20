"""Restartable, auditable Binance USDT-M historical kline collection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FORMAT_VERSION = 1
BINANCE_USDM_BASE = "https://fapi.binance.com"
KLINE_ENDPOINT = "/fapi/v1/klines"
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}
RAW_COLUMN_COUNT = 12
NORMALIZED_COLUMNS = (
    "open_time_utc",
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_utc",
    "close_time_ms",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
)


class DataIntegrityError(RuntimeError):
    """Raised when downloaded or cached data fails a mandatory trust gate."""


def _utc_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Strategy Lab timestamps must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _pretty_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


@dataclass(frozen=True)
class DownloadSpec:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    venue: str = "usd_m_futures"
    source: str = "binance_public_api"

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        interval: str,
        start: str | datetime,
        end: str | datetime,
    ) -> "DownloadSpec":
        return cls(
            symbol=symbol.upper(),
            interval=interval,
            start=_utc_datetime(start),
            end=_utc_datetime(end),
        )

    def __post_init__(self) -> None:
        if self.interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported Binance interval: {self.interval}")
        if self.start >= self.end:
            raise ValueError("Download start must be earlier than end")
        if not self.symbol or not self.symbol.isalnum():
            raise ValueError(f"Invalid Binance symbol: {self.symbol!r}")
        interval_ms = INTERVAL_MS[self.interval]
        if _epoch_ms(self.start) % interval_ms or _epoch_ms(self.end) % interval_ms:
            raise ValueError("Download boundaries must align to the requested interval")

    @property
    def start_ms(self) -> int:
        return _epoch_ms(self.start)

    @property
    def end_ms(self) -> int:
        return _epoch_ms(self.end)

    @property
    def interval_ms(self) -> int:
        return INTERVAL_MS[self.interval]

    @property
    def expected_rows(self) -> int:
        return (self.end_ms - self.start_ms) // self.interval_ms

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "venue": self.venue,
            "symbol": self.symbol,
            "interval": self.interval,
            "start": self.start.isoformat().replace("+00:00", "Z"),
            "end": self.end.isoformat().replace("+00:00", "Z"),
            "bar_policy": "closed_only",
            "boundary_policy": "start_inclusive_end_exclusive",
        }

    @property
    def checksum(self) -> str:
        return _sha256_bytes(_canonical_json_bytes(self.as_dict()))

    @property
    def dataset_name(self) -> str:
        start = self.start.strftime("%Y%m%dT%H%M%SZ")
        end = self.end.strftime("%Y%m%dT%H%M%SZ")
        return f"{start}__{end}"


class BinanceKlineClient:
    """Small public-data client with bounded retries and no credentials."""

    def __init__(
        self,
        *,
        base_url: str = BINANCE_USDM_BASE,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or self._make_session()

    @staticmethod
    def _make_session() -> requests.Session:
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            backoff_factor=0.8,
            status_forcelist=(418, 429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": "binance-perp-scanner-strategy-lab/0.1"})
        return session

    def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[list[Any]]:
        response = self.session.get(
            f"{self.base_url}{KLINE_ENDPOINT}",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms - 1,
                "limit": limit,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise DataIntegrityError(f"Unexpected Binance response type: {type(payload).__name__}")
        return payload


class TrustedKlineStore:
    """Immutable raw pages plus deterministic normalized and manifest layers."""

    def __init__(self, root: Path | str, spec: DownloadSpec, *, page_limit: int = 1500) -> None:
        if not 1 <= page_limit <= 1500:
            raise ValueError("Binance kline page_limit must be between 1 and 1500")
        self.root = Path(root)
        self.spec = spec
        self.page_limit = page_limit
        self.dataset_dir = (
            self.root
            / "binance_usdm"
            / spec.symbol
            / spec.interval
            / spec.dataset_name
        )
        self.raw_dir = self.dataset_dir / "raw" / "pages"
        self.normalized_path = self.dataset_dir / "normalized" / "bars.csv"
        self.quality_path = self.dataset_dir / "quality.json"
        self.manifest_path = self.dataset_dir / "manifest.json"
        self.state_path = self.dataset_dir / "download-state.json"

    def download(
        self,
        client: BinanceKlineClient,
        *,
        now: datetime | None = None,
        max_pages: int | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Download or resume, returning state when paused and manifest when complete."""
        state = self._load_or_create_state()
        if state["download_complete"] and self.manifest_path.exists():
            return self.verify_manifest()
        pages_this_run = 0
        while int(state["next_start_ms"]) < self.spec.end_ms:
            if max_pages is not None and pages_this_run >= max_pages:
                return state
            cursor = int(state["next_start_ms"])
            rows = client.fetch_klines(
                symbol=self.spec.symbol,
                interval=self.spec.interval,
                start_ms=cursor,
                end_ms=self.spec.end_ms,
                limit=self.page_limit,
            )
            if not rows:
                raise DataIntegrityError(
                    f"Binance returned no data at {_iso_from_ms(cursor)} before requested end"
                )
            page = self._validate_page(rows, cursor)
            page_index = len(state["pages"])
            first_ms = int(page[0][0])
            last_ms = int(page[-1][0])
            filename = f"{page_index:06d}_{first_ms}_{last_ms}.json"
            payload = _canonical_json_bytes(page)
            page_path = self.raw_dir / filename
            if page_path.exists():
                if _sha256_file(page_path) != _sha256_bytes(payload):
                    raise DataIntegrityError(f"Immutable raw page differs: {page_path}")
            else:
                _atomic_write(page_path, payload)
            page_record = {
                "index": page_index,
                "file": str(page_path.relative_to(self.dataset_dir)).replace("\\", "/"),
                "first_open_time_ms": first_ms,
                "last_open_time_ms": last_ms,
                "row_count": len(page),
                "sha256": _sha256_bytes(payload),
            }
            state["pages"].append(page_record)
            state["next_start_ms"] = last_ms + self.spec.interval_ms
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(self.state_path, _pretty_json_bytes(state))
            pages_this_run += 1
            if progress:
                progress(dict(page_record))

        state["download_complete"] = True
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(self.state_path, _pretty_json_bytes(state))
        return self._finalize(state, now=now)

    def verify_manifest(self, manifest_path: Path | str | None = None) -> dict[str, Any]:
        path = Path(manifest_path) if manifest_path else self.manifest_path
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["format_version"] != FORMAT_VERSION:
            raise DataIntegrityError("Unsupported Strategy Lab data manifest version")
        if manifest["spec_checksum"] != self.spec.checksum:
            raise DataIntegrityError("Manifest request does not match this data store")
        state = self._load_state()
        raw_rows, raw_digest = self._read_verified_pages(state["pages"])
        normalized, quality = self._normalize(raw_rows, int(manifest["closed_bar_cutoff_ms"]))
        normalized_payload = self._csv_bytes(normalized)
        if _sha256_bytes(normalized_payload) != manifest["files"]["normalized"]["sha256"]:
            raise DataIntegrityError("Normalized reconstruction checksum differs from manifest")
        if raw_digest != manifest["files"]["raw"]["aggregate_sha256"]:
            raise DataIntegrityError("Raw aggregate checksum differs from manifest")
        if quality != manifest["quality"]:
            raise DataIntegrityError("Reconstructed quality report differs from manifest")
        if _sha256_file(self.normalized_path) != manifest["files"]["normalized"]["sha256"]:
            raise DataIntegrityError("Stored normalized file checksum differs from manifest")
        if _sha256_file(self.quality_path) != manifest["files"]["quality"]["sha256"]:
            raise DataIntegrityError("Stored quality file checksum differs from manifest")
        return manifest

    def reconstruct(self, output: Path | str, manifest_path: Path | str | None = None) -> Path:
        manifest = self.verify_manifest(manifest_path)
        state = self._load_state()
        raw_rows, _ = self._read_verified_pages(state["pages"])
        normalized, _ = self._normalize(raw_rows, int(manifest["closed_bar_cutoff_ms"]))
        destination = Path(output)
        _atomic_write(destination, self._csv_bytes(normalized))
        return destination

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = self._load_state()
            if state["spec_checksum"] != self.spec.checksum:
                raise DataIntegrityError("Existing checkpoint belongs to a different request")
            if int(state["page_limit"]) != self.page_limit:
                raise DataIntegrityError("Existing checkpoint uses a different page limit")
            return state
        state = {
            "format_version": FORMAT_VERSION,
            "spec": self.spec.as_dict(),
            "spec_checksum": self.spec.checksum,
            "endpoint": f"{BINANCE_USDM_BASE}{KLINE_ENDPOINT}",
            "page_limit": self.page_limit,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "next_start_ms": self.spec.start_ms,
            "download_complete": False,
            "pages": [],
        }
        _atomic_write(self.state_path, _pretty_json_bytes(state))
        return state

    def _load_state(self) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("format_version") != FORMAT_VERSION:
            raise DataIntegrityError("Unsupported download checkpoint version")
        return state

    def _validate_page(self, rows: list[list[Any]], cursor: int) -> list[list[Any]]:
        page: list[list[Any]] = []
        previous: int | None = None
        for row in rows:
            if not isinstance(row, list) or len(row) != RAW_COLUMN_COUNT:
                raise DataIntegrityError("Binance kline row does not contain 12 fields")
            open_ms = int(row[0])
            if open_ms < cursor or open_ms >= self.spec.end_ms:
                continue
            if open_ms % self.spec.interval_ms:
                raise DataIntegrityError(f"Misaligned kline timestamp: {open_ms}")
            if previous is not None and open_ms < previous:
                raise DataIntegrityError("Binance page timestamps are not ordered")
            previous = open_ms
            page.append(row)
        if not page:
            raise DataIntegrityError("Binance page contained no rows inside the requested range")
        return page

    def _read_verified_pages(self, records: Iterable[dict[str, Any]]) -> tuple[list[list[Any]], str]:
        rows: list[list[Any]] = []
        aggregate = hashlib.sha256()
        for record in records:
            path = self.dataset_dir / record["file"]
            payload = path.read_bytes()
            checksum = _sha256_bytes(payload)
            if checksum != record["sha256"]:
                raise DataIntegrityError(f"Raw page checksum mismatch: {path}")
            aggregate.update(payload)
            page = json.loads(payload)
            if len(page) != int(record["row_count"]):
                raise DataIntegrityError(f"Raw page row count mismatch: {path}")
            rows.extend(page)
        return rows, aggregate.hexdigest()

    def _normalize(
        self,
        raw_rows: list[list[Any]],
        closed_cutoff_ms: int,
    ) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
        by_open: dict[int, list[Any]] = {}
        duplicate_count = 0
        conflicting_duplicates: list[int] = []
        outside_range_count = 0
        unclosed_count = 0
        for row in raw_rows:
            open_ms = int(row[0])
            if open_ms < self.spec.start_ms or open_ms >= self.spec.end_ms:
                outside_range_count += 1
                continue
            if int(row[6]) >= closed_cutoff_ms:
                unclosed_count += 1
                continue
            existing = by_open.get(open_ms)
            if existing is not None:
                duplicate_count += 1
                if existing != row:
                    conflicting_duplicates.append(open_ms)
                continue
            by_open[open_ms] = row

        actual = sorted(by_open)
        expected = set(range(self.spec.start_ms, self.spec.end_ms, self.spec.interval_ms))
        actual_set = set(actual)
        gaps = sorted(expected - actual_set)
        unexpected = sorted(actual_set - expected)
        normalized = []
        for open_ms in actual:
            row = by_open[open_ms]
            close_ms = int(row[6])
            normalized.append(
                (
                    _iso_from_ms(open_ms),
                    open_ms,
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]),
                    _iso_from_ms(close_ms),
                    close_ms,
                    str(row[7]),
                    int(row[8]),
                    str(row[9]),
                    str(row[10]),
                )
            )

        quality = {
            "status": (
                "passed"
                if not gaps
                and not unexpected
                and not duplicate_count
                and not conflicting_duplicates
                else "failed"
            ),
            "timezone": "UTC",
            "timestamps_ordered": actual == sorted(actual),
            "timestamps_unique": duplicate_count == 0,
            "timestamps_aligned": all(ts % self.spec.interval_ms == 0 for ts in actual),
            "requested_start_ms": self.spec.start_ms,
            "requested_end_ms": self.spec.end_ms,
            "expected_row_count": self.spec.expected_rows,
            "raw_row_count": len(raw_rows),
            "normalized_row_count": len(normalized),
            "duplicate_count": duplicate_count,
            "conflicting_duplicate_count": len(conflicting_duplicates),
            "conflicting_duplicate_examples_ms": conflicting_duplicates[:20],
            "gap_count": len(gaps),
            "gap_examples_ms": gaps[:20],
            "unexpected_timestamp_count": len(unexpected),
            "unexpected_timestamp_examples_ms": unexpected[:20],
            "unclosed_rows_removed": unclosed_count,
            "outside_range_rows_removed": outside_range_count,
            "actual_first_open_ms": actual[0] if actual else None,
            "actual_last_open_ms": actual[-1] if actual else None,
        }
        return normalized, quality

    @staticmethod
    def _csv_bytes(rows: list[tuple[Any, ...]]) -> bytes:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(NORMALIZED_COLUMNS)
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8")

    def _finalize(self, state: dict[str, Any], *, now: datetime | None) -> dict[str, Any]:
        closed_cutoff = min(
            self.spec.end_ms,
            _epoch_ms((now or datetime.now(timezone.utc)).astimezone(timezone.utc)),
        )
        raw_rows, raw_digest = self._read_verified_pages(state["pages"])
        normalized, quality = self._normalize(raw_rows, closed_cutoff)
        normalized_payload = self._csv_bytes(normalized)
        quality_payload = _pretty_json_bytes(quality)
        _atomic_write(self.normalized_path, normalized_payload)
        _atomic_write(self.quality_path, quality_payload)
        manifest = {
            "format_version": FORMAT_VERSION,
            "status": quality["status"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "spec": self.spec.as_dict(),
            "spec_checksum": self.spec.checksum,
            "closed_bar_cutoff_ms": closed_cutoff,
            "closed_bar_cutoff_utc": _iso_from_ms(closed_cutoff),
            "request": {
                "method": "GET",
                "endpoint": f"{BINANCE_USDM_BASE}{KLINE_ENDPOINT}",
                "parameters": {
                    "symbol": self.spec.symbol,
                    "interval": self.spec.interval,
                    "startTime": self.spec.start_ms,
                    "endTime_exclusive": self.spec.end_ms,
                    "page_limit": self.page_limit,
                },
            },
            "transformations": [
                "preserve each successful API page as canonical immutable JSON",
                "restrict rows to start-inclusive/end-exclusive request boundaries",
                "exclude bars whose close time is not earlier than the closed-bar cutoff",
                "retain the first identical duplicate and report all duplicate counts",
                "sort by open timestamp and emit explicit UTC ISO-8601 timestamps",
            ],
            "files": {
                "raw": {
                    "directory": "raw/pages",
                    "page_count": len(state["pages"]),
                    "aggregate_sha256": raw_digest,
                    "pages": state["pages"],
                },
                "normalized": {
                    "file": str(self.normalized_path.relative_to(self.dataset_dir)).replace("\\", "/"),
                    "format": "csv",
                    "sha256": _sha256_bytes(normalized_payload),
                    "row_count": len(normalized),
                },
                "quality": {
                    "file": str(self.quality_path.relative_to(self.dataset_dir)).replace("\\", "/"),
                    "sha256": _sha256_bytes(quality_payload),
                },
                "checkpoint": {
                    "file": str(self.state_path.relative_to(self.dataset_dir)).replace("\\", "/"),
                    "sha256": _sha256_file(self.state_path),
                },
            },
            "quality": quality,
            "reconstruction": {
                "module": "perpscanner.strategy_lab.binance_data",
                "class": "TrustedKlineStore",
                "rule": "verify raw page checksums, apply listed transformations, write canonical CSV",
            },
        }
        _atomic_write(self.manifest_path, _pretty_json_bytes(manifest))
        if quality["status"] != "passed":
            raise DataIntegrityError(
                "Data quality gate failed; inspect quality.json and manifest.json"
            )
        return manifest
