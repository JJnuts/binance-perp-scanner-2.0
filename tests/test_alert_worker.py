import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from perpscanner.alert_store import AlertStore
from perpscanner.alert_worker import (
    AlertWorker,
    MarketScan,
    SingleInstanceLock,
    WorkerAlreadyRunningError,
    WorkerConfig,
    WorkerConfigurationError,
    _report_json,
)
from perpscanner.config import RESEARCH_DB_PATH
from perpscanner.discord_transport import DiscordDeliveryResult


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class FakeDiscordClient:
    def __init__(self, results=None, *, raises=False):
        self.results = list(results or [DiscordDeliveryResult(delivered=True)])
        self.raises = raises
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        if self.raises:
            raise RuntimeError("secret transport detail")
        return self.results.pop(0)


class AlwaysSuccessfulDiscordClient:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return DiscordDeliveryResult(delivered=True)


class FakeStopEvent:
    def __init__(self, stop_after_waits):
        self.stop_after_waits = stop_after_waits
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.waits.append(delay)
        if len(self.waits) >= self.stop_after_waits:
            self.stopped = True
        return self.stopped


class AlertWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "alerts.sqlite"
        self.t0 = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        self.clock = MutableClock(self.t0)

    @staticmethod
    def _ltf_row(symbol="SOLUSDT", direction="Long", score=80.0, *, fresh=True):
        return {
            "symbol": symbol,
            "tf_alignment_pass": True,
            "fresh_setup_pass": fresh,
            "ltf_direction": direction,
            "ltf_ignition_score": score,
        }

    @staticmethod
    def _htf_row(symbol="SOLUSDT", direction="Long", score=90.0):
        return {
            "symbol": symbol,
            "htf_expansion_direction": f"{direction} expansion",
            "htf_expansion_score": score,
            "htf_momentum_score": score,
            "htf_setup_score": score,
            "htf_atr_percentile": 50.0,
            "htf_atr_roc": 0.1,
            "htf_breakout_distance_atr": 0.5,
            "daily_structure_score": 90.0,
            "daily_long_confirmed": direction == "Long",
            "daily_short_confirmed": direction == "Short",
            "daily_volume_ratio": 2.0,
            "daily_volume_persistence_days": 2,
            "daily_oi_persistence_days": 2,
            "daily_swing_high": 100.0,
            "daily_swing_low": 90.0,
            "btc_daily_regime": "Bull trend",
            "btc_daily_regime_score": 80.0,
            "rs_24h": 3.0,
            "rs_72h": 5.0,
        }

    def _scan(self, *, fresh=True, empty=False):
        if empty:
            return MarketScan(pd.DataFrame(), pd.DataFrame(), 100)
        return MarketScan(
            htf=pd.DataFrame([self._htf_row()]),
            ltf=pd.DataFrame([self._ltf_row(fresh=fresh)]),
            universe_size=100,
        )

    def _config(self, **overrides):
        values = {
            "database_path": self.db_path,
            "research_logging_enabled": False,
        }
        values.update(overrides)
        return WorkerConfig(**values)

    def _worker(self, *, config=None, scan=None, client=None, research_logger=None, monotonic=None):
        return AlertWorker(
            config or self._config(),
            delivery_client=client,
            scanner=lambda unused: scan if scan is not None else self._scan(),
            research_logger=research_logger or (lambda htf, ltf: {}),
            clock=self.clock,
            monotonic=monotonic or (lambda: 0.0),
        )

    def test_safe_defaults_leave_delivery_disabled(self):
        config = WorkerConfig(database_path=self.db_path)

        self.assertFalse(config.delivery_enabled)
        self.assertEqual(config.scan_interval_seconds, 60.0)
        self.assertEqual(config.reminder_interval_seconds, 10_800.0)
        self.assertEqual(config.entry_threshold, 75.0)
        self.assertEqual(config.continuation_threshold, 75.0)
        self.assertNotIn("webhook", repr(config).lower())

    def test_environment_configuration_is_strict_and_explicit(self):
        config = WorkerConfig.from_environment(
            {
                "PERPSCANNER_ALERT_DB_PATH": str(self.db_path),
                "PERPSCANNER_ALERT_SCAN_INTERVAL_SECONDS": "90",
                "PERPSCANNER_ALERT_DELIVERY_ENABLED": "yes",
                "PERPSCANNER_ALERT_RESEARCH_LOGGING_ENABLED": "off",
                "PERPSCANNER_ALERT_ENTRY_THRESHOLD": "80.5",
            }
        )

        self.assertEqual(config.database_path, self.db_path)
        self.assertEqual(config.scan_interval_seconds, 90.0)
        self.assertTrue(config.delivery_enabled)
        self.assertFalse(config.research_logging_enabled)
        self.assertEqual(config.entry_threshold, 80.5)

        with self.assertRaisesRegex(WorkerConfigurationError, "true or false"):
            WorkerConfig.from_environment(
                {
                    "PERPSCANNER_ALERT_DB_PATH": str(self.db_path),
                    "PERPSCANNER_ALERT_DELIVERY_ENABLED": "perhaps",
                }
            )

    def test_unsafe_or_excessive_configuration_is_rejected(self):
        with self.assertRaisesRegex(WorkerConfigurationError, "separate"):
            WorkerConfig(database_path=RESEARCH_DB_PATH)
        with self.assertRaisesRegex(WorkerConfigurationError, "below 30"):
            self._config(scan_interval_seconds=29.9)
        with self.assertRaisesRegex(WorkerConfigurationError, "between 0 and 100"):
            self._config(entry_threshold=101)
        with self.assertRaisesRegex(WorkerConfigurationError, "positive integer"):
            self._config(clear_after_failures=True)
        with self.assertRaisesRegex(WorkerConfigurationError, "1 through 100"):
            self._config(delivery_batch_limit=101)

    def test_delivery_enabled_requires_an_explicit_client(self):
        with self.assertRaisesRegex(WorkerConfigurationError, "Discord is not configured"):
            self._worker(config=self._config(delivery_enabled=True))

    def test_delivery_disabled_persists_only_one_initial_notification(self):
        worker = self._worker()

        first = worker.run_once()
        self.clock.value += timedelta(minutes=1)
        second = worker.run_once()

        self.assertTrue(first.scan_succeeded)
        self.assertEqual(first.notifications_created, 1)
        self.assertEqual(first.delivery_attempts, 0)
        self.assertEqual(first.active_setups, 1)
        self.assertEqual(first.pending_outbox, 1)
        self.assertEqual(second.notifications_created, 0)
        self.assertEqual(second.pending_outbox, 1)
        message = AlertStore(self.db_path).list_outbox()[0]
        self.assertEqual(message.status, "pending")
        self.assertEqual(message.attempt_count, 0)

    def test_market_scan_failure_is_unknown_and_does_not_mutate_state(self):
        worker = self._worker()
        worker.run_once()
        before = AlertStore(self.db_path).load_snapshot()
        before_outbox = AlertStore(self.db_path).list_outbox()

        def broken_scan(unused):
            raise RuntimeError("upstream credentials and internals")

        failed_worker = AlertWorker(
            self._config(),
            scanner=broken_scan,
            research_logger=lambda htf, ltf: self.fail("logger must not run"),
            clock=self.clock,
        )
        report = failed_worker.run_once()

        self.assertFalse(report.scan_succeeded)
        self.assertEqual(report.error_code, "MARKET_SCAN_FAILED")
        self.assertEqual(AlertStore(self.db_path).load_snapshot(), before)
        self.assertEqual(AlertStore(self.db_path).list_outbox(), before_outbox)

    def test_invalid_or_partial_scan_fails_closed(self):
        invalid_type = self._worker(scan="not-a-market-scan").run_once()
        self.assertEqual(invalid_type.error_code, "MARKET_SCAN_INVALID")

        partial = MarketScan(
            htf=pd.DataFrame([self._htf_row()]),
            ltf=pd.DataFrame([{"symbol": "SOLUSDT"}]),
            universe_size=100,
        )
        invalid_schema = self._worker(scan=partial).run_once()
        self.assertEqual(invalid_schema.error_code, "LIFECYCLE_UPDATE_FAILED")
        self.assertEqual(AlertStore(self.db_path).load_snapshot().revision, 0)

    def test_research_logging_is_independent_and_nonfatal(self):
        calls = []

        def logger(htf, ltf):
            calls.append((len(htf), len(ltf)))
            return {"metric": 2, "ltf": 3}

        config = self._config(research_logging_enabled=True)
        report = self._worker(config=config, research_logger=logger).run_once()

        self.assertEqual(calls, [(1, 1)])
        self.assertEqual(report.research_rows_written, 5)

        self.clock.value += timedelta(minutes=1)
        broken = self._worker(
            config=config,
            research_logger=lambda htf, ltf: (_ for _ in ()).throw(RuntimeError("disk detail")),
        ).run_once()
        self.assertTrue(broken.scan_succeeded)
        self.assertEqual(broken.research_rows_written, 0)
        self.assertIsNone(broken.error_code)

    def test_successful_delivery_acknowledges_initial(self):
        client = FakeDiscordClient()
        worker = self._worker(config=self._config(delivery_enabled=True), client=client)

        report = worker.run_once()

        self.assertEqual(report.delivery_attempts, 1)
        self.assertEqual(report.deliveries_succeeded, 1)
        self.assertEqual(report.deliveries_failed, 0)
        self.assertEqual(report.pending_outbox, 0)
        self.assertEqual(len(client.messages), 1)
        self.assertEqual(client.messages[0].status, "inflight")
        message = AlertStore(self.db_path).list_outbox()[0]
        setup = AlertStore(self.db_path).load_snapshot().state.setups[0]
        self.assertEqual(message.status, "sent")
        self.assertEqual(message.sent_at_utc, self.t0)
        self.assertEqual(setup.last_successful_delivery_at_utc, self.t0)
        self.assertIsNone(setup.pending_notification_id)

    def test_failed_delivery_is_retried_only_when_due(self):
        client = FakeDiscordClient(
            [
                DiscordDeliveryResult(
                    delivered=False,
                    error_code="DISCORD_RATE_LIMITED",
                    retry_after=timedelta(minutes=5),
                ),
                DiscordDeliveryResult(delivered=True),
            ]
        )
        worker = self._worker(config=self._config(delivery_enabled=True), client=client)

        first = worker.run_once()
        self.clock.value += timedelta(minutes=1)
        early = worker.run_once()
        self.clock.value += timedelta(minutes=4)
        retry = worker.run_once()

        self.assertEqual(first.deliveries_failed, 1)
        self.assertEqual(early.delivery_attempts, 0)
        self.assertEqual(retry.deliveries_succeeded, 1)
        self.assertEqual(len(client.messages), 2)
        message = AlertStore(self.db_path).list_outbox()[0]
        self.assertEqual(message.status, "sent")
        self.assertEqual(message.attempt_count, 2)

    def test_transport_exception_is_sanitized_and_requeued(self):
        client = FakeDiscordClient(raises=True)
        worker = self._worker(config=self._config(delivery_enabled=True), client=client)

        report = worker.run_once()
        message = AlertStore(self.db_path).list_outbox()[0]

        self.assertTrue(report.scan_succeeded)
        self.assertEqual(report.deliveries_failed, 1)
        self.assertEqual(message.status, "pending")
        self.assertEqual(message.last_error_code, "DISCORD_TRANSPORT_ERROR")
        self.assertNotIn("secret", _report_json(report).lower())

    def test_three_hour_reminder_uses_latest_strength(self):
        client = FakeDiscordClient(
            [DiscordDeliveryResult(delivered=True), DiscordDeliveryResult(delivered=True)]
        )
        scan_holder = {"value": self._scan()}
        worker = AlertWorker(
            self._config(delivery_enabled=True),
            delivery_client=client,
            scanner=lambda unused: scan_holder["value"],
            research_logger=lambda htf, ltf: {},
            clock=self.clock,
        )
        worker.run_once()

        self.clock.value += timedelta(hours=3)
        scan_holder["value"] = MarketScan(
            htf=pd.DataFrame([self._htf_row(score=100.0)]),
            ltf=pd.DataFrame([self._ltf_row(score=100.0, fresh=False)]),
            universe_size=100,
        )
        reminder = worker.run_once()

        self.assertEqual(reminder.notifications_created, 1)
        self.assertEqual(reminder.deliveries_succeeded, 1)
        self.assertEqual(len(client.messages), 2)
        self.assertEqual(client.messages[0].kind, "INITIAL")
        self.assertEqual(client.messages[1].kind, "REMINDER")
        self.assertEqual(client.messages[1].strength, 100)

    def test_restart_retry_reminder_failure_isolation_and_direction_flip_end_to_end(self):
        scan_holder = {"value": self._scan()}
        config = self._config(delivery_enabled=True)
        first_client = FakeDiscordClient(
            [
                DiscordDeliveryResult(
                    delivered=False,
                    error_code="DISCORD_TIMEOUT",
                    retry_after=timedelta(minutes=5),
                )
            ]
        )
        first_worker = AlertWorker(
            config,
            delivery_client=first_client,
            scanner=lambda unused: scan_holder["value"],
            research_logger=lambda htf, ltf: {},
            clock=self.clock,
        )

        failed_initial = first_worker.run_once()
        initial_id = first_client.messages[0].notification_id
        self.assertEqual(failed_initial.deliveries_failed, 1)

        # Recreate every runtime object to model a process/computer restart.
        self.clock.value += timedelta(minutes=5)
        scan_holder["value"] = self._scan(fresh=False)
        restarted_client = AlwaysSuccessfulDiscordClient()
        restarted_worker = AlertWorker(
            config,
            store=AlertStore(self.db_path),
            delivery_client=restarted_client,
            scanner=lambda unused: scan_holder["value"],
            research_logger=lambda htf, ltf: {},
            clock=self.clock,
        )
        recovered = restarted_worker.run_once()

        self.assertEqual(recovered.deliveries_succeeded, 1)
        self.assertEqual(restarted_client.messages[0].notification_id, initial_id)
        self.assertEqual(restarted_client.messages[0].attempt_count, 2)

        # Repeated current scans neither reopen nor duplicate the initial.
        for _ in range(30):
            self.clock.value += timedelta(minutes=1)
            duplicate = restarted_worker.run_once()
            self.assertEqual(duplicate.notifications_created, 0)
            self.assertEqual(duplicate.delivery_attempts, 0)

        # The reminder is measured from successful delivery and uses new data.
        self.clock.value = self.t0 + timedelta(hours=3, minutes=5)
        scan_holder["value"] = MarketScan(
            htf=pd.DataFrame([self._htf_row(score=100.0)]),
            ltf=pd.DataFrame([self._ltf_row(score=100.0, fresh=False)]),
            universe_size=100,
        )
        reminder = restarted_worker.run_once()
        self.assertEqual(reminder.deliveries_succeeded, 1)
        self.assertEqual(restarted_client.messages[-1].kind, "REMINDER")
        self.assertEqual(restarted_client.messages[-1].strength, 100)

        # A failed market read cannot alter the delivered setup or outbox.
        before_failure = AlertStore(self.db_path).load_snapshot()
        outbox_before_failure = AlertStore(self.db_path).list_outbox()

        def failed_scan(unused):
            raise RuntimeError("private upstream failure details")

        failed_worker = AlertWorker(
            config,
            store=AlertStore(self.db_path),
            delivery_client=restarted_client,
            scanner=failed_scan,
            research_logger=lambda htf, ltf: {},
            clock=self.clock,
        )
        unknown = failed_worker.run_once()
        self.assertEqual(unknown.error_code, "MARKET_SCAN_FAILED")
        self.assertEqual(AlertStore(self.db_path).load_snapshot(), before_failure)
        self.assertEqual(AlertStore(self.db_path).list_outbox(), outbox_before_failure)

        # A fully qualified opposite fresh setup replaces and alerts immediately.
        self.clock.value += timedelta(minutes=1)
        scan_holder["value"] = MarketScan(
            htf=pd.DataFrame([self._htf_row(direction="Short", score=95.0)]),
            ltf=pd.DataFrame([self._ltf_row(direction="Short", score=95.0)]),
            universe_size=100,
        )
        flipped = restarted_worker.run_once()
        setup = AlertStore(self.db_path).load_snapshot().state.setups[0]
        messages = AlertStore(self.db_path).list_outbox()

        self.assertEqual(flipped.notifications_created, 1)
        self.assertEqual(flipped.deliveries_succeeded, 1)
        self.assertEqual(setup.key, ("SOLUSDT", "SHORT"))
        self.assertEqual([message.status for message in messages], ["sent", "sent", "sent"])
        self.assertEqual(len({message.notification_id for message in messages}), 3)
        self.assertEqual(len(restarted_client.messages), 3)

    def test_large_signal_burst_is_drained_in_bounded_batches_without_duplicates(self):
        symbols = [f"T{index:03d}USDT" for index in range(225)]
        scan = MarketScan(
            htf=pd.DataFrame([self._htf_row(symbol=symbol) for symbol in symbols]),
            ltf=pd.DataFrame([self._ltf_row(symbol=symbol) for symbol in symbols]),
            universe_size=300,
        )
        client = AlwaysSuccessfulDiscordClient()
        worker = self._worker(
            config=self._config(delivery_enabled=True, delivery_batch_limit=100),
            scan=scan,
            client=client,
        )

        first = worker.run_once()
        self.clock.value += timedelta(minutes=1)
        second = worker.run_once()
        self.clock.value += timedelta(minutes=1)
        third = worker.run_once()
        self.clock.value += timedelta(minutes=1)
        fourth = worker.run_once()

        self.assertEqual(
            [first.delivery_attempts, second.delivery_attempts, third.delivery_attempts, fourth.delivery_attempts],
            [100, 100, 25, 0],
        )
        self.assertEqual(first.notifications_created, 225)
        self.assertEqual(second.notifications_created, 0)
        self.assertEqual(third.pending_outbox, 0)
        self.assertEqual(len(client.messages), 225)
        self.assertEqual(len({message.notification_id for message in client.messages}), 225)
        self.assertEqual(len(AlertStore(self.db_path).load_snapshot().state.setups), 225)
        self.assertTrue(
            all(message.status == "sent" for message in AlertStore(self.db_path).list_outbox())
        )

    def test_single_instance_lock_rejects_overlap_and_releases_cleanly(self):
        lock_path = Path(self.temp_dir.name) / "worker.lock"

        with SingleInstanceLock(lock_path):
            with self.assertRaises(WorkerAlreadyRunningError):
                with SingleInstanceLock(lock_path):
                    pass
        with SingleInstanceLock(lock_path):
            self.assertTrue(lock_path.exists())

    def test_run_forever_uses_fixed_cadence_and_stops_gracefully(self):
        monotonic_values = iter((0.0, 5.0, 60.0, 65.0))
        event = FakeStopEvent(stop_after_waits=2)
        reports = []
        worker = self._worker(
            scan=self._scan(empty=True),
            monotonic=lambda: next(monotonic_values),
        )

        worker.run_forever(event, on_report=reports.append)

        self.assertEqual(len(reports), 2)
        self.assertEqual(event.waits, [55.0, 55.0])
        self.assertTrue(all(report.scan_succeeded for report in reports))

    def test_report_json_contains_only_bounded_operational_fields(self):
        report = self._worker().run_once()

        payload = json.loads(_report_json(report))

        self.assertEqual(
            set(payload),
            {
                "scan_at_utc",
                "scan_succeeded",
                "universe_size",
                "signals_evaluated",
                "notifications_created",
                "delivery_attempts",
                "deliveries_succeeded",
                "deliveries_failed",
                "active_setups",
                "pending_outbox",
                "research_rows_written",
                "error_code",
            },
        )
        self.assertNotIn("url", _report_json(report).lower())
        self.assertNotIn("webhook", _report_json(report).lower())


if __name__ == "__main__":
    unittest.main()
