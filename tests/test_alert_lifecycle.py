import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from perpscanner.alert_lifecycle import (
    ActiveSetup,
    AlertLifecycleState,
    LifecycleError,
    acknowledge_delivery,
    advance_lifecycle,
)
from perpscanner.alert_signals import AlertSignal


class AlertLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

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

    def _open(self, signal=None, at=None):
        at = at or self.t0
        signal = signal or self._signal(at)
        return advance_lifecycle(AlertLifecycleState(), (signal,), at)

    def _open_and_ack(self, signal=None, at=None, delivered_at=None):
        at = at or self.t0
        opened = self._open(signal=signal or self._signal(at), at=at)
        delivered_at = delivered_at or at
        acknowledged = acknowledge_delivery(
            opened.state,
            opened.notifications[0].notification_id,
            delivered_at,
        )
        return opened, acknowledged

    def test_fresh_signal_opens_new_setup_and_initial_notification(self):
        result = self._open()

        self.assertEqual(len(result.state.setups), 1)
        setup = result.state.setups[0]
        self.assertEqual(setup.key, ("SOLUSDT", "LONG"))
        self.assertEqual(setup.status, "NEW")
        self.assertEqual(setup.strength, 84)
        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].kind, "INITIAL")
        self.assertEqual(result.notifications[0].notification_id, setup.pending_notification_id)

    def test_continuation_only_signal_cannot_open_new_setup(self):
        signal = self._signal(initial=False, continuing=True)

        result = advance_lifecycle(AlertLifecycleState(), (signal,), self.t0)

        self.assertEqual(result.state.setups, ())
        self.assertEqual(result.notifications, ())

    def test_repeated_scan_does_not_duplicate_pending_initial(self):
        opened = self._open()
        later = self.t0 + timedelta(minutes=1)

        result = advance_lifecycle(
            opened.state,
            (self._signal(later, strength=86, score=85.7),),
            later,
        )

        self.assertEqual(result.notifications, ())
        self.assertEqual(result.state.setups[0].pending_notification_id, opened.notifications[0].notification_id)
        self.assertEqual(result.state.setups[0].strength, 86)

    def test_acknowledgement_starts_reminder_clock(self):
        opened, state = self._open_and_ack(delivered_at=self.t0 + timedelta(minutes=2))

        setup = state.setups[0]
        self.assertEqual(setup.status, "ACTIVE")
        self.assertIsNone(setup.pending_notification_id)
        self.assertEqual(setup.last_successful_delivery_at_utc, self.t0 + timedelta(minutes=2))
        self.assertNotEqual(opened.state, state)

    def test_reminder_waits_three_hours_from_successful_delivery(self):
        _, state = self._open_and_ack(delivered_at=self.t0 + timedelta(minutes=2))
        before_due = self.t0 + timedelta(hours=3, minutes=1, seconds=59)
        at_due = self.t0 + timedelta(hours=3, minutes=2)

        before = advance_lifecycle(state, (self._signal(before_due),), before_due)
        due = advance_lifecycle(before.state, (self._signal(at_due, strength=81, score=80.6),), at_due)

        self.assertEqual(before.notifications, ())
        self.assertEqual(len(due.notifications), 1)
        self.assertEqual(due.notifications[0].kind, "REMINDER")
        self.assertEqual(due.notifications[0].strength, 81)
        self.assertEqual(due.state.setups[0].status, "REMINDER_DUE")

    def test_pending_reminder_is_not_duplicated_by_later_scans(self):
        _, state = self._open_and_ack()
        due_at = self.t0 + timedelta(hours=3)
        due = advance_lifecycle(state, (self._signal(due_at),), due_at)
        later = due_at + timedelta(hours=3)

        repeated = advance_lifecycle(due.state, (self._signal(later),), later)

        self.assertEqual(repeated.notifications, ())
        self.assertEqual(repeated.state.setups[0].pending_notification_id, due.notifications[0].notification_id)

    def test_acknowledged_reminder_restarts_clock_from_actual_delivery(self):
        _, state = self._open_and_ack()
        due_at = self.t0 + timedelta(hours=3)
        due = advance_lifecycle(state, (self._signal(due_at),), due_at)
        delivered_at = due_at + timedelta(minutes=10)
        state = acknowledge_delivery(due.state, due.notifications[0].notification_id, delivered_at)

        too_early_at = delivered_at + timedelta(hours=2, minutes=59)
        too_early = advance_lifecycle(state, (self._signal(too_early_at),), too_early_at)
        next_due_at = delivered_at + timedelta(hours=3)
        next_due = advance_lifecycle(too_early.state, (self._signal(next_due_at),), next_due_at)

        self.assertEqual(too_early.notifications, ())
        self.assertEqual(len(next_due.notifications), 1)

    def test_long_offline_gap_creates_only_one_current_reminder(self):
        _, state = self._open_and_ack()
        return_at = self.t0 + timedelta(hours=10)

        result = advance_lifecycle(state, (self._signal(return_at),), return_at)

        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].kind, "REMINDER")

    def test_one_failed_qualification_enters_probation_and_suppresses_reminder(self):
        _, state = self._open_and_ack()
        due_at = self.t0 + timedelta(hours=3)

        result = advance_lifecycle(state, (), due_at)

        self.assertEqual(result.notifications, ())
        self.assertEqual(result.state.setups[0].status, "PROBATION")
        self.assertEqual(result.state.setups[0].consecutive_failures, 1)

    def test_queued_reminder_is_cancelled_on_first_completed_qualification_failure(self):
        _, state = self._open_and_ack()
        due_at = self.t0 + timedelta(hours=3)
        due = advance_lifecycle(state, (self._signal(due_at),), due_at)
        failed_at = due_at + timedelta(minutes=1)

        failed = advance_lifecycle(due.state, (), failed_at)

        self.assertEqual(failed.state.setups[0].status, "PROBATION")
        self.assertIsNone(failed.state.setups[0].pending_notification_id)
        self.assertEqual(failed.cancelled_notification_ids, (due.notifications[0].notification_id,))

        recovered_at = failed_at + timedelta(minutes=1)
        recovered = advance_lifecycle(
            failed.state,
            (self._signal(recovered_at, initial=False, continuing=True),),
            recovered_at,
        )
        self.assertEqual(len(recovered.notifications), 1)
        self.assertEqual(recovered.notifications[0].kind, "REMINDER")

    def test_two_consecutive_failed_qualifications_clear_setup(self):
        opened = self._open()
        first_at = self.t0 + timedelta(minutes=1)
        second_at = self.t0 + timedelta(minutes=2)
        first = advance_lifecycle(opened.state, (), first_at)

        second = advance_lifecycle(first.state, (), second_at)

        self.assertEqual(second.state.setups, ())
        self.assertEqual(second.cleared_keys, (("SOLUSDT", "LONG"),))
        self.assertEqual(second.cancelled_notification_ids, (opened.notifications[0].notification_id,))

    def test_revalidation_resets_probation_failure_count(self):
        _, state = self._open_and_ack()
        first_at = self.t0 + timedelta(minutes=1)
        probation = advance_lifecycle(state, (), first_at)
        recovered_at = self.t0 + timedelta(minutes=2)

        recovered = advance_lifecycle(
            probation.state,
            (self._signal(recovered_at, initial=False, continuing=True),),
            recovered_at,
        )

        self.assertEqual(recovered.state.setups[0].status, "ACTIVE")
        self.assertEqual(recovered.state.setups[0].consecutive_failures, 0)

    def test_cleared_setup_can_reenter_on_later_fresh_signal(self):
        opened, state = self._open_and_ack()
        first = advance_lifecycle(state, (), self.t0 + timedelta(minutes=1))
        cleared = advance_lifecycle(first.state, (), self.t0 + timedelta(minutes=2))
        reentry_at = self.t0 + timedelta(minutes=3)

        reentry = advance_lifecycle(cleared.state, (self._signal(reentry_at),), reentry_at)

        self.assertEqual(len(reentry.notifications), 1)
        self.assertEqual(reentry.notifications[0].kind, "INITIAL")
        self.assertNotEqual(reentry.notifications[0].notification_id, opened.notifications[0].notification_id)

    def test_qualified_direction_flip_is_immediate_and_cancels_pending_old_message(self):
        opened = self._open()
        flip_at = self.t0 + timedelta(minutes=1)
        short = self._signal(flip_at, direction="SHORT", strength=88, score=87.8)

        result = advance_lifecycle(opened.state, (short,), flip_at)

        self.assertEqual(result.cleared_keys, (("SOLUSDT", "LONG"),))
        self.assertEqual(result.cancelled_notification_ids, (opened.notifications[0].notification_id,))
        self.assertEqual(result.state.setups[0].key, ("SOLUSDT", "SHORT"))
        self.assertEqual(result.notifications[0].kind, "INITIAL")

    def test_opposite_continuation_only_signal_does_not_open_direction_flip(self):
        _, state = self._open_and_ack()
        at = self.t0 + timedelta(minutes=1)
        opposite = self._signal(at, direction="SHORT", initial=False, continuing=True)

        result = advance_lifecycle(state, (opposite,), at)

        self.assertEqual(result.notifications, ())
        self.assertEqual(result.state.setups[0].key, ("SOLUSDT", "LONG"))
        self.assertEqual(result.state.setups[0].status, "PROBATION")

    def test_failed_scan_is_unknown_and_leaves_state_untouched(self):
        opened = self._open()
        failed_at = self.t0 + timedelta(hours=6)

        result = advance_lifecycle(opened.state, (), failed_at, scan_succeeded=False)

        self.assertEqual(result.state, opened.state)
        self.assertEqual(result.notifications, ())
        self.assertEqual(result.cleared_keys, ())

    def test_invalid_or_out_of_order_inputs_are_rejected(self):
        opened = self._open()
        earlier = self.t0 - timedelta(seconds=1)

        with self.assertRaisesRegex(LifecycleError, "precede"):
            advance_lifecycle(opened.state, (), earlier)
        with self.assertRaisesRegex(LifecycleError, "failed scan"):
            advance_lifecycle(opened.state, (self._signal(),), self.t0, scan_succeeded=False)
        with self.assertRaisesRegex(LifecycleError, "multiple current directions"):
            advance_lifecycle(
                AlertLifecycleState(),
                (self._signal(), self._signal(direction="SHORT")),
                self.t0,
            )

    def test_scan_cannot_precede_a_later_delivery_acknowledgement(self):
        _, state = self._open_and_ack(delivered_at=self.t0 + timedelta(minutes=2))

        with self.assertRaisesRegex(LifecycleError, "prior lifecycle activity"):
            advance_lifecycle(
                state,
                (self._signal(self.t0 + timedelta(minutes=1)),),
                self.t0 + timedelta(minutes=1),
            )
        with self.assertRaisesRegex(LifecycleError, "does not match scan time"):
            advance_lifecycle(
                AlertLifecycleState(),
                (self._signal(self.t0 + timedelta(minutes=1)),),
                self.t0,
            )

    def test_invalid_settings_are_rejected(self):
        for interval in (timedelta(0), timedelta(hours=-1), "3h"):
            with self.subTest(interval=interval):
                with self.assertRaisesRegex(LifecycleError, "reminder_interval"):
                    advance_lifecycle(AlertLifecycleState(), (), self.t0, reminder_interval=interval)
        for failure_count in (0, -1, 1.5, True):
            with self.subTest(failure_count=failure_count):
                with self.assertRaisesRegex(LifecycleError, "clear_after_failures"):
                    advance_lifecycle(
                        AlertLifecycleState(),
                        (),
                        self.t0,
                        clear_after_failures=failure_count,
                    )

    def test_delivery_acknowledgement_rejects_unknown_or_impossible_delivery(self):
        opened = self._open()
        notification_id = opened.notifications[0].notification_id

        with self.assertRaisesRegex(LifecycleError, "not pending"):
            acknowledge_delivery(opened.state, "unknown", self.t0)
        with self.assertRaisesRegex(LifecycleError, "precede"):
            acknowledge_delivery(opened.state, notification_id, self.t0 - timedelta(seconds=1))

    def test_lifecycle_functions_do_not_mutate_prior_state(self):
        opened = self._open()
        original = opened.state
        later = self.t0 + timedelta(minutes=1)

        result = advance_lifecycle(original, (self._signal(later, strength=90, score=90.0),), later)

        self.assertEqual(original.setups[0].strength, 84)
        self.assertEqual(result.state.setups[0].strength, 90)
        self.assertIsNot(original.setups[0], result.state.setups[0])

    def test_state_validation_rejects_two_active_directions_for_one_symbol(self):
        opened = self._open().state.setups[0]
        invalid = AlertLifecycleState(
            setups=(opened, replace(opened, direction="SHORT")),
        )

        with self.assertRaisesRegex(LifecycleError, "multiple active directions"):
            advance_lifecycle(invalid, (), self.t0)

    def test_state_validation_rejects_invalid_strength_and_score(self):
        opened = self._open().state.setups[0]

        for invalid in (replace(opened, strength=0), replace(opened, score=float("nan"))):
            with self.subTest(invalid=invalid):
                with self.assertRaises(LifecycleError):
                    advance_lifecycle(AlertLifecycleState((invalid,)), (), self.t0)

    def test_minute_scans_over_six_hours_emit_only_two_due_reminders(self):
        opened, state = self._open_and_ack()
        reminder_ids = []
        for minute in range(1, 361):
            at = self.t0 + timedelta(minutes=minute)
            result = advance_lifecycle(state, (self._signal(at),), at)
            state = result.state
            if result.notifications:
                self.assertEqual(len(result.notifications), 1)
                notification = result.notifications[0]
                self.assertEqual(notification.kind, "REMINDER")
                reminder_ids.append(notification.notification_id)
                state = acknowledge_delivery(state, notification.notification_id, at)

        self.assertEqual(len(opened.notifications), 1)
        self.assertEqual(len(reminder_ids), 2)
        self.assertEqual(len(set(reminder_ids)), 2)

    def test_hundreds_of_simultaneous_setups_have_unique_deterministic_intents(self):
        signals = tuple(
            self._signal(
                symbol=f"ALT{index:03d}USDT",
                direction="LONG" if index % 2 == 0 else "SHORT",
                strength=75 + index % 26,
                score=75.0 + index % 26,
            )
            for index in range(250)
        )

        opened = advance_lifecycle(AlertLifecycleState(), signals, self.t0)

        self.assertEqual(len(opened.state.setups), 250)
        self.assertEqual(len(opened.notifications), 250)
        self.assertEqual(len({item.notification_id for item in opened.notifications}), 250)

        state = opened.state
        for notification in opened.notifications:
            state = acknowledge_delivery(state, notification.notification_id, self.t0)
        due_at = self.t0 + timedelta(hours=3)
        due_signals = tuple(replace(signal, observed_at_utc=due_at) for signal in signals)

        due = advance_lifecycle(state, due_signals, due_at)

        self.assertEqual(len(due.notifications), 250)
        self.assertEqual(len({item.notification_id for item in due.notifications}), 250)


if __name__ == "__main__":
    unittest.main()
