"""Browser-independent market scan and Discord alert worker."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Callable, Mapping

import pandas as pd

from .alert_lifecycle import advance_lifecycle
from .alert_signals import select_alert_signals
from .alert_store import AlertStore
from .config import (
    BTC_OPTIONS_BLOCK_DB_PATH,
    REPO_ROOT,
    RESEARCH_CALIBRATION_DB_PATH,
    RESEARCH_DB_PATH,
    WS_LTF_ENABLED,
)
from .data_binance import get_usdt_perpetuals
from .discord_transport import DiscordDeliveryResult, DiscordWebhookClient
from .research import log_scan_snapshot
from .scoring import build_ltf_regime_metrics, build_metrics


ENV_PREFIX = "PERPSCANNER_ALERT_"


class WorkerConfigurationError(ValueError):
    """Worker configuration is missing, unsafe, or outside supported bounds."""


class WorkerAlreadyRunningError(RuntimeError):
    """Another worker owns the configured alert database lock."""


def _env_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerConfigurationError(f"{name} must be a finite number") from exc
    if not math.isfinite(value):
        raise WorkerConfigurationError(f"{name} must be a finite number")
    return value


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise WorkerConfigurationError(f"{name} must be an integer") from exc


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise WorkerConfigurationError(f"{name} must be true or false")


@dataclass(frozen=True)
class WorkerConfig:
    database_path: Path = REPO_ROOT / "data" / "alerts.sqlite"
    scan_interval_seconds: float = 60.0
    entry_threshold: float = 75.0
    continuation_threshold: float = 75.0
    reminder_interval_seconds: float = 3 * 60 * 60.0
    clear_after_failures: int = 2
    min_quote_volume: float = 10_000_000.0
    min_trades: float = 15_000.0
    min_oi_value: float = 5_000_000.0
    use_ws_feed: bool = WS_LTF_ENABLED
    delivery_enabled: bool = False
    delivery_batch_limit: int = 20
    claim_lease_seconds: float = 120.0
    research_logging_enabled: bool = True

    def __post_init__(self):
        path = Path(self.database_path)
        object.__setattr__(self, "database_path", path)
        protected = {
            Path(RESEARCH_DB_PATH).resolve(),
            Path(RESEARCH_CALIBRATION_DB_PATH).resolve(),
            Path(BTC_OPTIONS_BLOCK_DB_PATH).resolve(),
        }
        if path.resolve() in protected:
            raise WorkerConfigurationError("alert database must be separate from protected scanner databases")
        for name, value in (
            ("scan_interval_seconds", self.scan_interval_seconds),
            ("reminder_interval_seconds", self.reminder_interval_seconds),
            ("claim_lease_seconds", self.claim_lease_seconds),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise WorkerConfigurationError(f"{name} must be a positive finite number")
            if float(value) <= 0.0:
                raise WorkerConfigurationError(f"{name} must be a positive finite number")
        if self.scan_interval_seconds < 30.0:
            raise WorkerConfigurationError("scan_interval_seconds cannot be below 30 seconds")
        for name, value in (
            ("entry_threshold", self.entry_threshold),
            ("continuation_threshold", self.continuation_threshold),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise WorkerConfigurationError(f"{name} must be between 0 and 100")
            if not 0.0 <= float(value) <= 100.0:
                raise WorkerConfigurationError(f"{name} must be between 0 and 100")
        for name, value in (
            ("min_quote_volume", self.min_quote_volume),
            ("min_trades", self.min_trades),
            ("min_oi_value", self.min_oi_value),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise WorkerConfigurationError(f"{name} must be a non-negative finite number")
            if float(value) < 0.0:
                raise WorkerConfigurationError(f"{name} must be a non-negative finite number")
        if (
            isinstance(self.clear_after_failures, bool)
            or not isinstance(self.clear_after_failures, int)
            or self.clear_after_failures < 1
        ):
            raise WorkerConfigurationError("clear_after_failures must be a positive integer")
        if (
            isinstance(self.delivery_batch_limit, bool)
            or not isinstance(self.delivery_batch_limit, int)
            or not 1 <= self.delivery_batch_limit <= 100
        ):
            raise WorkerConfigurationError("delivery_batch_limit must be an integer from 1 through 100")
        for name, value in (
            ("use_ws_feed", self.use_ws_feed),
            ("delivery_enabled", self.delivery_enabled),
            ("research_logging_enabled", self.research_logging_enabled),
        ):
            if not isinstance(value, bool):
                raise WorkerConfigurationError(f"{name} must be boolean")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "WorkerConfig":
        source = os.environ if environ is None else environ
        return cls(
            database_path=Path(source.get(f"{ENV_PREFIX}DB_PATH", REPO_ROOT / "data" / "alerts.sqlite")),
            scan_interval_seconds=_env_float(source, f"{ENV_PREFIX}SCAN_INTERVAL_SECONDS", 60.0),
            entry_threshold=_env_float(source, f"{ENV_PREFIX}ENTRY_THRESHOLD", 75.0),
            continuation_threshold=_env_float(source, f"{ENV_PREFIX}CONTINUATION_THRESHOLD", 75.0),
            reminder_interval_seconds=_env_float(
                source,
                f"{ENV_PREFIX}REMINDER_INTERVAL_SECONDS",
                3 * 60 * 60.0,
            ),
            clear_after_failures=_env_int(source, f"{ENV_PREFIX}CLEAR_AFTER_FAILURES", 2),
            min_quote_volume=_env_float(source, f"{ENV_PREFIX}MIN_QUOTE_VOLUME", 10_000_000.0),
            min_trades=_env_float(source, f"{ENV_PREFIX}MIN_TRADES", 15_000.0),
            min_oi_value=_env_float(source, f"{ENV_PREFIX}MIN_OI_VALUE", 5_000_000.0),
            use_ws_feed=_env_bool(source, f"{ENV_PREFIX}USE_WS", WS_LTF_ENABLED),
            delivery_enabled=_env_bool(source, f"{ENV_PREFIX}DELIVERY_ENABLED", False),
            delivery_batch_limit=_env_int(source, f"{ENV_PREFIX}DELIVERY_BATCH_LIMIT", 20),
            claim_lease_seconds=_env_float(source, f"{ENV_PREFIX}CLAIM_LEASE_SECONDS", 120.0),
            research_logging_enabled=_env_bool(source, f"{ENV_PREFIX}RESEARCH_LOGGING_ENABLED", True),
        )


@dataclass(frozen=True)
class MarketScan:
    htf: pd.DataFrame
    ltf: pd.DataFrame
    universe_size: int


@dataclass(frozen=True)
class CycleReport:
    scan_succeeded: bool
    scan_at_utc: datetime
    universe_size: int = 0
    signals_evaluated: int = 0
    notifications_created: int = 0
    delivery_attempts: int = 0
    deliveries_succeeded: int = 0
    deliveries_failed: int = 0
    active_setups: int = 0
    pending_outbox: int = 0
    research_rows_written: int = 0
    error_code: str | None = None


def scan_market(config: WorkerConfig) -> MarketScan:
    """Run the existing HTF and LTF models without a browser session."""

    symbols = tuple(get_usdt_perpetuals())
    htf = build_metrics(
        symbols,
        config.min_quote_volume,
        config.min_trades,
        config.min_oi_value,
    )
    ltf = build_ltf_regime_metrics(
        symbols,
        config.min_quote_volume,
        config.min_trades,
        config.min_oi_value,
        use_ws=config.use_ws_feed,
    )
    return MarketScan(htf=htf, ltf=ltf, universe_size=len(symbols))


class SingleInstanceLock:
    """Cross-platform advisory lock held for the worker process lifetime."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise WorkerAlreadyRunningError("another alert worker is already running") from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._handle is None:
            return False
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
        return False


class AlertWorker:
    def __init__(
        self,
        config: WorkerConfig,
        *,
        store: AlertStore | None = None,
        delivery_client: DiscordWebhookClient | None = None,
        scanner: Callable[[WorkerConfig], MarketScan] = scan_market,
        research_logger: Callable[[pd.DataFrame, pd.DataFrame], object] = log_scan_snapshot,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(config, WorkerConfig):
            raise WorkerConfigurationError("config must be a WorkerConfig")
        if config.delivery_enabled and delivery_client is None:
            raise WorkerConfigurationError("delivery is enabled but Discord is not configured")
        self.config = config
        self.store = store or AlertStore(config.database_path)
        self.delivery_client = delivery_client
        self.scanner = scanner
        self.research_logger = research_logger
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise WorkerConfigurationError("worker clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _status_counts(self) -> tuple[int, int]:
        active = len(self.store.load_snapshot().state.setups)
        pending = len(self.store.list_outbox(status="pending"))
        return active, pending

    def _failed_report(self, at: datetime, code: str, *, universe_size: int = 0) -> CycleReport:
        try:
            active, pending = self._status_counts()
        except Exception:
            active, pending = 0, 0
        return CycleReport(
            scan_succeeded=False,
            scan_at_utc=at,
            universe_size=universe_size,
            active_setups=active,
            pending_outbox=pending,
            error_code=code,
        )

    def run_once(self) -> CycleReport:
        started_at = self._now()
        try:
            scan = self.scanner(self.config)
        except Exception:
            return self._failed_report(started_at, "MARKET_SCAN_FAILED")
        if not isinstance(scan, MarketScan):
            return self._failed_report(started_at, "MARKET_SCAN_INVALID")

        scan_at = self._now()
        try:
            signals = select_alert_signals(
                scan.ltf,
                scan.htf,
                scan_at,
                entry_threshold=self.config.entry_threshold,
                continuation_threshold=self.config.continuation_threshold,
            )
            snapshot = self.store.load_snapshot()
            lifecycle = advance_lifecycle(
                snapshot.state,
                signals,
                scan_at,
                reminder_interval=timedelta(seconds=self.config.reminder_interval_seconds),
                clear_after_failures=self.config.clear_after_failures,
            )
            self.store.apply_lifecycle_result(lifecycle, expected_revision=snapshot.revision)
        except Exception:
            return self._failed_report(scan_at, "LIFECYCLE_UPDATE_FAILED", universe_size=scan.universe_size)

        research_rows = 0
        if self.config.research_logging_enabled:
            try:
                written = self.research_logger(scan.htf, scan.ltf)
                if isinstance(written, dict):
                    research_rows = sum(int(value) for value in written.values())
            except Exception:
                research_rows = 0

        attempts = 0
        delivered = 0
        failed = 0
        delivery_state_error = False
        if self.config.delivery_enabled:
            try:
                claimed = self.store.claim_due(
                    self._now(),
                    limit=self.config.delivery_batch_limit,
                    lease=timedelta(seconds=self.config.claim_lease_seconds),
                )
            except Exception:
                claimed = ()
                delivery_state_error = True
            for message in claimed:
                attempts += 1
                try:
                    result = self.delivery_client.send(message)
                except Exception:
                    result = DiscordDeliveryResult(
                        delivered=False,
                        error_code="DISCORD_TRANSPORT_ERROR",
                        retry_after=timedelta(minutes=1),
                    )
                attempt_at = self._now()
                try:
                    if result.delivered:
                        self.store.record_delivery(message.notification_id, attempt_at)
                        delivered += 1
                    else:
                        retry_delay = result.retry_after or timedelta(minutes=1)
                        self.store.record_failure(
                            message.notification_id,
                            attempt_at,
                            attempt_at + retry_delay,
                            result.error_code or "DISCORD_UNKNOWN_ERROR",
                        )
                        failed += 1
                except Exception:
                    delivery_state_error = True

        try:
            active, pending = self._status_counts()
        except Exception:
            active, pending = 0, 0
            delivery_state_error = True
        return CycleReport(
            scan_succeeded=True,
            scan_at_utc=scan_at,
            universe_size=scan.universe_size,
            signals_evaluated=len(signals),
            notifications_created=len(lifecycle.notifications),
            delivery_attempts=attempts,
            deliveries_succeeded=delivered,
            deliveries_failed=failed,
            active_setups=active,
            pending_outbox=pending,
            research_rows_written=research_rows,
            error_code="DELIVERY_STATE_ERROR" if delivery_state_error else None,
        )

    def run_forever(
        self,
        stop_event,
        *,
        on_report: Callable[[CycleReport], None] | None = None,
    ) -> None:
        while not stop_event.is_set():
            cycle_started = self.monotonic()
            report = self.run_once()
            if on_report is not None:
                on_report(report)
            elapsed = max(0.0, self.monotonic() - cycle_started)
            delay = max(0.0, self.config.scan_interval_seconds - elapsed)
            if stop_event.wait(delay):
                break


def _report_json(report: CycleReport) -> str:
    return json.dumps(
        {
            "scan_at_utc": report.scan_at_utc.isoformat().replace("+00:00", "Z"),
            "scan_succeeded": report.scan_succeeded,
            "universe_size": report.universe_size,
            "signals_evaluated": report.signals_evaluated,
            "notifications_created": report.notifications_created,
            "delivery_attempts": report.delivery_attempts,
            "deliveries_succeeded": report.deliveries_succeeded,
            "deliveries_failed": report.deliveries_failed,
            "active_setups": report.active_setups,
            "pending_outbox": report.pending_outbox,
            "research_rows_written": report.research_rows_written,
            "error_code": report.error_code,
        },
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Binance altcoin setup alert worker")
    parser.add_argument("--once", action="store_true", help="run one scan cycle and exit")
    args = parser.parse_args(argv)

    try:
        config = WorkerConfig.from_environment()
        store = AlertStore(config.database_path)
        client = DiscordWebhookClient.from_environment() if config.delivery_enabled else None
        worker = AlertWorker(config, store=store, delivery_client=client)
    except Exception:
        print('{"error_code":"WORKER_CONFIGURATION_FAILED"}', file=sys.stderr)
        return 2

    lock_path = Path(f"{config.database_path}.lock")
    stop_event = threading.Event()

    def request_stop(signum, frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)
        with SingleInstanceLock(lock_path):
            if args.once:
                report = worker.run_once()
                print(_report_json(report), flush=True)
                return 0 if report.scan_succeeded and report.error_code is None else 1
            worker.run_forever(stop_event, on_report=lambda report: print(_report_json(report), flush=True))
            return 0
    except WorkerAlreadyRunningError:
        print('{"error_code":"WORKER_ALREADY_RUNNING"}', file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
