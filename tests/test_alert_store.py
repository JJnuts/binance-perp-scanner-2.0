import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from perpscanner.alert_lifecycle import (
    AlertLifecycleState,
    advance_lifecycle,
)
from perpscanner.alert_signals import AlertSignal
from perpscanner.alert_store import (
    AlertStore,
    AlertStoreConflict,
    AlertStoreError,
    SCHEMA_VERSION,
)


class AlertStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "alerts.sqlite"
        self.t0 = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

    def _store(self):
        return AlertStore(self.path)

    def _signal(
        self,
        at=None,
        *,
        symbol="SOLUSDT",
        direction="LONG",
        strength=84,
        score=84.2,
        initial=True,
        continuing=True,
    ):
        return AlertSignal(
            symbol=symbol,
            direction=direction,
            strength=strength,
            score=score,
            observed_at_utc=at or self.t0,
            initial_eligible=initial,
            continuation_eligible=continuing,
        )

    def _open_result(self, *, at=None, signal=None):
        at = at or self.t0
        return advance_lifecycle(
            AlertLifecycleState(),
            (signal or self._signal(at),),
            at,
        )

    def _persist_open(self, store=None):
        store = store or self._store()
        result = self._open_result()
        revision = store.apply_lifecycle_result(result, expected_revision=0)
        return store, result, revision

    def _deliver_initial(self, store=None):
        store, opened, _ = self._persist_open(store)
        claimed = store.claim_due(self.t0)
        self.assertEqual(len(claimed), 1)
        revision = store.record_delivery(claimed[0].notification_id, self.t0)
        return store, opened, revision

    def test_new_store_has_version_zero_revision_and_empty_state(self):
        store = self._store()

        snapshot = store.load_snapshot()

        self.assertEqual(snapshot.revision, 0)
        self.assertEqual(snapshot.state, AlertLifecycleState())
        self.assertEqual(store.list_outbox(), ())
        connection = sqlite3.connect(self.path)
        try:
            version = connection.execute(
                "SELECT schema_version FROM alert_store_meta WHERE singleton = 1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, SCHEMA_VERSION)

    def test_store_refuses_to_modify_existing_unrelated_sqlite_database(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE valuable_user_data(value TEXT)")
            connection.execute("INSERT INTO valuable_user_data VALUES ('preserve-me')")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(AlertStoreError, "refusing to modify"):
            AlertStore(self.path)

        connection = sqlite3.connect(self.path)
        try:
            value = connection.execute("SELECT value FROM valuable_user_data").fetchone()[0]
            marker = connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'alert_store_meta'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(value, "preserve-me")
        self.assertIsNone(marker)

    def test_store_rejects_unknown_schema_version(self):
        self._store()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("UPDATE alert_store_meta SET schema_version = 999")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(AlertStoreError, "unsupported"):
            AlertStore(self.path)

    def test_lifecycle_state_and_pending_outbox_survive_reopen(self):
        store, result, revision = self._persist_open()

        reopened = AlertStore(self.path)
        snapshot = reopened.load_snapshot()
        outbox = reopened.list_outbox()

        self.assertEqual(revision, 1)
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(snapshot.state, result.state)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].status, "pending")
        self.assertEqual(outbox[0].notification_id, result.notifications[0].notification_id)
        self.assertEqual(outbox[0].attempt_count, 0)

    def test_stale_revision_rejects_entire_result_without_duplicates(self):
        store = self._store()
        first = self._open_result()
        store.apply_lifecycle_result(first, expected_revision=0)

        with self.assertRaisesRegex(AlertStoreConflict, "revision changed"):
            store.apply_lifecycle_result(first, expected_revision=0)

        self.assertEqual(store.load_snapshot().revision, 1)
        self.assertEqual(len(store.list_outbox()), 1)

    def test_claim_is_leased_and_not_claimed_twice(self):
        store, result, _ = self._persist_open()

        first = store.claim_due(self.t0, lease=timedelta(minutes=2))
        duplicate = store.claim_due(self.t0 + timedelta(minutes=1))

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].status, "inflight")
        self.assertEqual(first[0].attempt_count, 1)
        self.assertEqual(first[0].notification_id, result.notifications[0].notification_id)
        self.assertEqual(duplicate, ())

    def test_expired_claim_is_recovered_after_worker_crash(self):
        store, _, _ = self._persist_open()
        store.claim_due(self.t0, lease=timedelta(minutes=2))

        before_expiry = store.claim_due(self.t0 + timedelta(minutes=1, seconds=59))
        recovered = store.claim_due(self.t0 + timedelta(minutes=2))

        self.assertEqual(before_expiry, ())
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].attempt_count, 2)

    def test_failure_returns_claim_to_queue_until_retry_time(self):
        store, _, _ = self._persist_open()
        message = store.claim_due(self.t0)[0]
        retry_at = self.t0 + timedelta(minutes=5)

        store.record_failure(
            message.notification_id,
            self.t0 + timedelta(seconds=2),
            retry_at,
            "DISCORD_TIMEOUT",
        )

        queued = store.list_outbox(status="pending")[0]
        self.assertEqual(queued.last_error_code, "DISCORD_TIMEOUT")
        self.assertEqual(store.claim_due(retry_at - timedelta(microseconds=1)), ())
        retry = store.claim_due(retry_at)
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0].attempt_count, 2)

    def test_failure_and_delivery_cannot_precede_claim_time(self):
        store, _, _ = self._persist_open()
        claimed_at = self.t0 + timedelta(minutes=1)
        message = store.claim_due(claimed_at)[0]

        with self.assertRaisesRegex(AlertStoreError, "precede claim"):
            store.record_failure(
                message.notification_id,
                self.t0,
                self.t0 + timedelta(minutes=2),
                "DISCORD_TIMEOUT",
            )
        with self.assertRaisesRegex(AlertStoreError, "precede claim"):
            store.record_delivery(message.notification_id, self.t0)

        self.assertEqual(store.list_outbox()[0].status, "inflight")

    def test_failure_rejects_unsanitized_error_details(self):
        store, _, _ = self._persist_open()
        message = store.claim_due(self.t0)[0]

        for unsafe in (
            "request failed with token",
            "https://example.invalid/sensitive-endpoint",
            "x" * 81,
            "",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaisesRegex(AlertStoreError, "sanitized"):
                    store.record_failure(
                        message.notification_id,
                        self.t0,
                        self.t0 + timedelta(minutes=1),
                        unsafe,
                    )

        self.assertEqual(store.list_outbox()[0].status, "inflight")

    def test_successful_delivery_atomically_updates_outbox_and_lifecycle(self):
        store, opened, _ = self._persist_open()
        message = store.claim_due(self.t0)[0]
        delivered_at = self.t0 + timedelta(seconds=3)

        revision = store.record_delivery(message.notification_id, delivered_at)

        snapshot = store.load_snapshot()
        sent = store.list_outbox(status="sent")
        self.assertEqual(revision, 3)
        self.assertEqual(snapshot.revision, 3)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].sent_at_utc, delivered_at)
        self.assertEqual(snapshot.state.setups[0].last_successful_delivery_at_utc, delivered_at)
        self.assertIsNone(snapshot.state.setups[0].pending_notification_id)
        self.assertEqual(opened.state.setups[0].status, "NEW")
        self.assertEqual(snapshot.state.setups[0].status, "ACTIVE")

    def test_delivery_requires_a_current_claim(self):
        store, result, _ = self._persist_open()

        with self.assertRaisesRegex(AlertStoreError, "not currently claimed"):
            store.record_delivery(result.notifications[0].notification_id, self.t0)

        self.assertEqual(store.list_outbox()[0].status, "pending")

    def test_two_failed_qualifications_cancel_pending_initial_atomically(self):
        store, _, _ = self._persist_open()
        first_at = self.t0 + timedelta(minutes=1)
        snapshot = store.load_snapshot()
        first = advance_lifecycle(snapshot.state, (), first_at)
        store.apply_lifecycle_result(first, expected_revision=snapshot.revision)
        second_at = self.t0 + timedelta(minutes=2)
        snapshot = store.load_snapshot()
        second = advance_lifecycle(snapshot.state, (), second_at)

        store.apply_lifecycle_result(second, expected_revision=snapshot.revision)

        self.assertEqual(store.load_snapshot().state.setups, ())
        self.assertEqual(store.list_outbox()[0].status, "cancelled")
        self.assertEqual(store.claim_due(second_at), ())

    def test_probationary_setup_cannot_deliver_pending_initial_until_revalidated(self):
        store, _, _ = self._persist_open()
        first_at = self.t0 + timedelta(minutes=1)
        snapshot = store.load_snapshot()
        probation = advance_lifecycle(snapshot.state, (), first_at)
        store.apply_lifecycle_result(probation, expected_revision=snapshot.revision)

        self.assertEqual(store.claim_due(first_at), ())

        recovered_at = first_at + timedelta(minutes=1)
        snapshot = store.load_snapshot()
        recovered = advance_lifecycle(
            snapshot.state,
            (self._signal(recovered_at, initial=False, continuing=True),),
            recovered_at,
        )
        store.apply_lifecycle_result(recovered, expected_revision=snapshot.revision)

        self.assertEqual(len(store.claim_due(recovered_at)), 1)

    def test_cancelled_reminder_can_be_revalidated_and_requeued_with_same_identity(self):
        store, _, _ = self._deliver_initial()
        due_at = self.t0 + timedelta(hours=3)
        snapshot = store.load_snapshot()
        due = advance_lifecycle(snapshot.state, (self._signal(due_at),), due_at)
        store.apply_lifecycle_result(due, expected_revision=snapshot.revision)
        reminder_id = due.notifications[0].notification_id

        failed_at = due_at + timedelta(minutes=1)
        snapshot = store.load_snapshot()
        failed = advance_lifecycle(snapshot.state, (), failed_at)
        store.apply_lifecycle_result(failed, expected_revision=snapshot.revision)
        self.assertEqual(store.list_outbox(status="cancelled")[0].notification_id, reminder_id)

        recovered_at = failed_at + timedelta(minutes=1)
        snapshot = store.load_snapshot()
        recovered_signal = self._signal(
            recovered_at,
            strength=88,
            score=87.6,
            initial=False,
            continuing=True,
        )
        recovered = advance_lifecycle(snapshot.state, (recovered_signal,), recovered_at)
        self.assertEqual(recovered.notifications[0].notification_id, reminder_id)

        store.apply_lifecycle_result(recovered, expected_revision=snapshot.revision)

        pending = store.list_outbox(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].notification_id, reminder_id)
        self.assertEqual(pending[0].strength, 88)
        self.assertEqual(pending[0].attempt_count, 0)

    def test_apply_rejects_new_notification_missing_from_lifecycle_pending_state(self):
        store = self._store()
        opened = self._open_result()
        invalid = replace(opened, state=AlertLifecycleState())

        with self.assertRaisesRegex(AlertStoreError, "remain pending"):
            store.apply_lifecycle_result(invalid, expected_revision=0)

        self.assertEqual(store.load_snapshot().revision, 0)
        self.assertEqual(store.list_outbox(), ())

    def test_apply_rejects_orphaning_a_deliverable_outbox_message(self):
        store, opened, _ = self._persist_open()
        invalid = replace(opened, state=AlertLifecycleState(), notifications=())

        with self.assertRaisesRegex(AlertStoreError, "must match"):
            store.apply_lifecycle_result(invalid, expected_revision=1)

        self.assertEqual(len(store.load_snapshot().state.setups), 1)
        self.assertEqual(store.list_outbox()[0].status, "pending")

    def test_apply_rejects_unknown_cancellation_and_rolls_back(self):
        store = self._store()
        invalid = replace(
            self._open_result(),
            cancelled_notification_ids=("0" * 64,),
        )

        with self.assertRaisesRegex(AlertStoreError, "not deliverable"):
            store.apply_lifecycle_result(invalid, expected_revision=0)

        self.assertEqual(store.load_snapshot().revision, 0)
        self.assertEqual(store.list_outbox(), ())

    def test_claim_limit_and_order_are_deterministic(self):
        store = self._store()
        signals = tuple(
            self._signal(
                symbol=f"ALT{index:03d}USDT",
                direction="LONG" if index % 2 == 0 else "SHORT",
            )
            for index in range(50)
        )
        opened = advance_lifecycle(AlertLifecycleState(), signals, self.t0)
        store.apply_lifecycle_result(opened, expected_revision=0)

        first = store.claim_due(self.t0, limit=7)
        second = store.claim_due(self.t0, limit=7)

        self.assertEqual(len(first), 7)
        self.assertEqual(len(second), 7)
        self.assertTrue(set(item.notification_id for item in first).isdisjoint(
            item.notification_id for item in second
        ))
        self.assertEqual([item.symbol for item in first], sorted(item.symbol for item in first))

    def test_hundreds_of_setups_persist_and_reload_without_loss(self):
        store = self._store()
        signals = tuple(
            self._signal(
                symbol=f"LOAD{index:03d}USDT",
                direction="LONG" if index % 2 == 0 else "SHORT",
                strength=75 + index % 26,
                score=75.0 + index % 26,
            )
            for index in range(250)
        )
        opened = advance_lifecycle(AlertLifecycleState(), signals, self.t0)

        store.apply_lifecycle_result(opened, expected_revision=0)
        reopened = AlertStore(self.path)

        self.assertEqual(len(reopened.load_snapshot().state.setups), 250)
        self.assertEqual(len(reopened.list_outbox(status="pending")), 250)
        self.assertEqual(len(reopened.claim_due(self.t0, limit=250)), 250)

    def test_concurrent_claimers_cannot_claim_the_same_message(self):
        store, result, _ = self._persist_open()

        def claim_once(_):
            return AlertStore(self.path).claim_due(self.t0, limit=1)

        with ThreadPoolExecutor(max_workers=8) as executor:
            batches = list(executor.map(claim_once, range(16)))

        claimed = [message for batch in batches for message in batch]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].notification_id, result.notifications[0].notification_id)


if __name__ == "__main__":
    unittest.main()
