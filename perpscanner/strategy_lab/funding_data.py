"""Trusted historical Binance funding-rate collection for Phase 3."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .binance_data import (
    BINANCE_USDM_BASE,
    DataIntegrityError,
    _atomic_write,
    _canonical_json_bytes,
    _pretty_json_bytes,
    _sha256_bytes,
    _sha256_file,
)


FUNDING_ENDPOINT = "/fapi/v1/fundingRate"
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000
FUNDING_SCHEDULE_TOLERANCE_MS = 60_000


class BinanceFundingClient:
    def __init__(self, session, *, timeout: int = 30) -> None:
        self.session = session
        self.timeout = timeout

    def fetch(
        self,
        *,
        symbol: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{BINANCE_USDM_BASE}{FUNDING_ENDPOINT}",
            params={
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": end_ms - 1,
                "limit": limit,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise DataIntegrityError("Unexpected Binance funding response")
        return payload


class TrustedFundingStore:
    def __init__(
        self,
        root: Path | str,
        *,
        symbol: str,
        start: str,
        end: str,
        page_limit: int = 1000,
    ) -> None:
        self.root = Path(root)
        self.symbol = symbol.upper()
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("Funding boundaries must be ordered timezone-aware values")
        self.start = self.start.tz_convert("UTC")
        self.end = self.end.tz_convert("UTC")
        self.start_ms = int(self.start.timestamp() * 1000)
        self.end_ms = int(self.end.timestamp() * 1000)
        if self.start_ms % FUNDING_INTERVAL_MS or self.end_ms % FUNDING_INTERVAL_MS:
            raise ValueError("Funding boundaries must align to the eight-hour grid")
        if not 1 <= page_limit <= 1000:
            raise ValueError("Funding page limit must be between 1 and 1000")
        self.page_limit = page_limit
        name = f"{self.start.strftime('%Y%m%dT%H%M%SZ')}__{self.end.strftime('%Y%m%dT%H%M%SZ')}"
        self.dataset_dir = self.root / "binance_usdm" / self.symbol / "funding" / name
        self.raw_dir = self.dataset_dir / "raw" / "pages"
        self.normalized_path = self.dataset_dir / "normalized" / "funding.csv"
        self.quality_path = self.dataset_dir / "quality.json"
        self.manifest_path = self.dataset_dir / "manifest.json"
        self.state_path = self.dataset_dir / "download-state.json"
        self.spec = {
            "source": "binance_public_api",
            "venue": "usd_m_futures",
            "symbol": self.symbol,
            "start": self.start.isoformat().replace("+00:00", "Z"),
            "end": self.end.isoformat().replace("+00:00", "Z"),
            "boundary_policy": "start_inclusive_end_exclusive",
        }
        self.spec_checksum = _sha256_bytes(_canonical_json_bytes(self.spec))

    def download(
        self,
        client: BinanceFundingClient,
        *,
        max_pages: int | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        state = self._load_or_create_state()
        if state["download_complete"] and self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if existing.get("status") == "passed":
                return self.verify_manifest()
        pages_this_run = 0
        while int(state["next_start_ms"]) < self.end_ms:
            if max_pages is not None and pages_this_run >= max_pages:
                return state
            cursor = int(state["next_start_ms"])
            rows = client.fetch(
                symbol=self.symbol,
                start_ms=cursor,
                end_ms=self.end_ms,
                limit=self.page_limit,
            )
            rows = [
                row
                for row in rows
                if cursor <= int(row["fundingTime"]) < self.end_ms
            ]
            if not rows:
                break
            times = [int(row["fundingTime"]) for row in rows]
            if times != sorted(times):
                raise DataIntegrityError("Funding page timestamps are not ordered")
            index = len(state["pages"])
            filename = f"{index:06d}_{times[0]}_{times[-1]}.json"
            path = self.raw_dir / filename
            payload = _canonical_json_bytes(rows)
            if path.exists() and _sha256_file(path) != _sha256_bytes(payload):
                raise DataIntegrityError(f"Immutable funding page differs: {path}")
            if not path.exists():
                _atomic_write(path, payload)
            record = {
                "index": index,
                "file": str(path.relative_to(self.dataset_dir)).replace("\\", "/"),
                "first_funding_time_ms": times[0],
                "last_funding_time_ms": times[-1],
                "row_count": len(rows),
                "sha256": _sha256_bytes(payload),
            }
            state["pages"].append(record)
            state["next_start_ms"] = times[-1] + 1
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(self.state_path, _pretty_json_bytes(state))
            pages_this_run += 1
            if progress:
                progress(record)
            if len(rows) < self.page_limit:
                break
        state["download_complete"] = True
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(self.state_path, _pretty_json_bytes(state))
        return self._finalize(state)

    def load_frame(self) -> pd.DataFrame:
        self.verify_manifest()
        frame = pd.read_csv(self.normalized_path)
        frame["funding_time_ms"] = frame["funding_time_ms"].astype("int64")
        frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="raise")
        return frame

    def verify_manifest(self) -> dict[str, Any]:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        rows, aggregate = self._read_pages(state)
        payload, quality = self._normalize(rows)
        if aggregate != manifest["files"]["raw"]["aggregate_sha256"]:
            raise DataIntegrityError("Funding raw aggregate checksum differs")
        if _sha256_bytes(payload) != manifest["files"]["normalized"]["sha256"]:
            raise DataIntegrityError("Funding reconstruction checksum differs")
        if quality != manifest["quality"]:
            raise DataIntegrityError("Funding quality reconstruction differs")
        if _sha256_file(self.normalized_path) != manifest["files"]["normalized"]["sha256"]:
            raise DataIntegrityError("Stored funding CSV checksum differs")
        return manifest

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state["spec_checksum"] != self.spec_checksum:
                raise DataIntegrityError("Funding checkpoint belongs to another request")
            if int(state["page_limit"]) != self.page_limit:
                raise DataIntegrityError("Funding checkpoint uses another page limit")
            return state
        state = {
            "format_version": 1,
            "spec": self.spec,
            "spec_checksum": self.spec_checksum,
            "page_limit": self.page_limit,
            "next_start_ms": self.start_ms,
            "download_complete": False,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "pages": [],
        }
        _atomic_write(self.state_path, _pretty_json_bytes(state))
        return state

    def _read_pages(self, state: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        aggregate = hashlib.sha256()
        rows: list[dict[str, Any]] = []
        for record in state["pages"]:
            path = self.dataset_dir / record["file"]
            payload = path.read_bytes()
            if _sha256_bytes(payload) != record["sha256"]:
                raise DataIntegrityError(f"Funding page checksum mismatch: {path}")
            aggregate.update(payload)
            page = json.loads(payload)
            if len(page) != int(record["row_count"]):
                raise DataIntegrityError(f"Funding page row count mismatch: {path}")
            rows.extend(page)
        return rows, aggregate.hexdigest()

    def _normalize(self, rows: list[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
        by_schedule: dict[int, tuple[int, dict[str, Any]]] = {}
        duplicates = 0
        conflicts = 0
        misaligned = 0
        offsets: list[int] = []
        for row in rows:
            timestamp = int(row["fundingTime"])
            if timestamp < self.start_ms or timestamp >= self.end_ms:
                continue
            scheduled = int(round(timestamp / FUNDING_INTERVAL_MS) * FUNDING_INTERVAL_MS)
            offset = timestamp - scheduled
            offsets.append(offset)
            if abs(offset) > FUNDING_SCHEDULE_TOLERANCE_MS:
                misaligned += 1
            if scheduled in by_schedule:
                duplicates += 1
                if by_schedule[scheduled][1] != row:
                    conflicts += 1
                continue
            by_schedule[scheduled] = (timestamp, row)
        scheduled_times = sorted(by_schedule)
        expected = set(range(self.start_ms, self.end_ms, FUNDING_INTERVAL_MS))
        gaps = sorted(expected - set(scheduled_times))
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            (
                "funding_time_utc",
                "funding_time_ms",
                "scheduled_funding_time_utc",
                "scheduled_funding_time_ms",
                "schedule_offset_ms",
                "funding_rate",
                "mark_price",
            )
        )
        for scheduled in scheduled_times:
            timestamp, row = by_schedule[scheduled]
            writer.writerow(
                (
                    pd.Timestamp(timestamp, unit="ms", tz="UTC").isoformat().replace("+00:00", "Z"),
                    timestamp,
                    pd.Timestamp(scheduled, unit="ms", tz="UTC").isoformat().replace("+00:00", "Z"),
                    scheduled,
                    timestamp - scheduled,
                    str(row["fundingRate"]),
                    str(row.get("markPrice", "")),
                )
            )
        quality = {
            "status": (
                "passed"
                if not duplicates and not conflicts and not gaps and not misaligned
                else "failed"
            ),
            "expected_row_count": len(expected),
            "normalized_row_count": len(scheduled_times),
            "duplicate_count": duplicates,
            "conflicting_duplicate_count": conflicts,
            "gap_count": len(gaps),
            "gap_examples_ms": gaps[:20],
            "timestamps_ordered": scheduled_times == sorted(scheduled_times),
            "timestamps_unique": duplicates == 0,
            "schedule_tolerance_ms": FUNDING_SCHEDULE_TOLERANCE_MS,
            "misaligned_timestamp_count": misaligned,
            "maximum_absolute_schedule_offset_ms": max((abs(value) for value in offsets), default=0),
            "timezone": "UTC",
        }
        return buffer.getvalue().encode("utf-8"), quality

    def _finalize(self, state: dict[str, Any]) -> dict[str, Any]:
        rows, aggregate = self._read_pages(state)
        normalized, quality = self._normalize(rows)
        quality_payload = _pretty_json_bytes(quality)
        _atomic_write(self.normalized_path, normalized)
        _atomic_write(self.quality_path, quality_payload)
        manifest = {
            "format_version": 1,
            "status": quality["status"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "spec": self.spec,
            "spec_checksum": self.spec_checksum,
            "request": {
                "method": "GET",
                "endpoint": f"{BINANCE_USDM_BASE}{FUNDING_ENDPOINT}",
                "parameters": {
                    "symbol": self.symbol,
                    "startTime": self.start_ms,
                    "endTime_exclusive": self.end_ms,
                    "page_limit": self.page_limit,
                },
            },
            "files": {
                "raw": {
                    "page_count": len(state["pages"]),
                    "aggregate_sha256": aggregate,
                    "pages": state["pages"],
                },
                "normalized": {
                    "file": str(self.normalized_path.relative_to(self.dataset_dir)).replace("\\", "/"),
                    "row_count": quality["normalized_row_count"],
                    "sha256": _sha256_bytes(normalized),
                },
                "quality": {
                    "file": str(self.quality_path.relative_to(self.dataset_dir)).replace("\\", "/"),
                    "sha256": _sha256_bytes(quality_payload),
                },
            },
            "quality": quality,
        }
        _atomic_write(self.manifest_path, _pretty_json_bytes(manifest))
        if quality["status"] != "passed":
            raise DataIntegrityError("Historical funding quality gate failed")
        return manifest
