"""Pure state transitions for best-setup alert lifecycles.

No function in this module reads a clock, database, network, or environment
variable.  The worker supplies successful scan times and explicitly
acknowledges successful delivery.  This keeps three-hour reminder behaviour
deterministic and fast to stress-test.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import math

from .alert_signals import AlertSignal


DEFAULT_REMINDER_INTERVAL = timedelta(hours=3)
DEFAULT_CLEAR_AFTER_FAILURES = 2
_DIRECTIONS = {"LONG", "SHORT"}
_NOTIFICATION_KINDS = {"INITIAL", "REMINDER"}


class LifecycleError(ValueError):
    """The requested lifecycle transition is invalid or ambiguous."""


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LifecycleError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _notification_id(kind: str, symbol: str, direction: str, due_at_utc: datetime) -> str:
    raw = f"{kind}|{symbol}|{direction}|{due_at_utc.isoformat()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class NotificationIntent:
    notification_id: str
    kind: str
    symbol: str
    direction: str
    strength: int
    score: float
    created_at_utc: datetime

    @property
    def key(self) -> tuple[str, str]:
        return self.symbol, self.direction


@dataclass(frozen=True)
class ActiveSetup:
    symbol: str
    direction: str
    opened_at_utc: datetime
    last_evaluated_at_utc: datetime
    strength: int
    score: float
    consecutive_failures: int = 0
    last_successful_delivery_at_utc: datetime | None = None
    pending_notification_id: str | None = None
    pending_notification_kind: str | None = None
    pending_notification_created_at_utc: datetime | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.symbol, self.direction

    @property
    def status(self) -> str:
        if self.consecutive_failures:
            return "PROBATION"
        if self.pending_notification_kind == "INITIAL" and self.last_successful_delivery_at_utc is None:
            return "NEW"
        if self.pending_notification_kind == "REMINDER":
            return "REMINDER_DUE"
        return "ACTIVE"


@dataclass(frozen=True)
class AlertLifecycleState:
    setups: tuple[ActiveSetup, ...] = ()

    def by_key(self) -> dict[tuple[str, str], ActiveSetup]:
        return {setup.key: setup for setup in self.setups}


@dataclass(frozen=True)
class LifecycleResult:
    state: AlertLifecycleState
    notifications: tuple[NotificationIntent, ...] = ()
    cleared_keys: tuple[tuple[str, str], ...] = ()
    cancelled_notification_ids: tuple[str, ...] = ()


def validate_lifecycle_state(state: AlertLifecycleState) -> AlertLifecycleState:
    """Fail closed when persisted or caller-built lifecycle state is invalid."""

    _validate_state(state)
    return state


def _validated_settings(
    reminder_interval: timedelta,
    clear_after_failures: int,
) -> tuple[timedelta, int]:
    if not isinstance(reminder_interval, timedelta) or reminder_interval <= timedelta(0):
        raise LifecycleError("reminder_interval must be a positive timedelta")
    if isinstance(clear_after_failures, bool) or not isinstance(clear_after_failures, int):
        raise LifecycleError("clear_after_failures must be a positive integer")
    if clear_after_failures < 1:
        raise LifecycleError("clear_after_failures must be a positive integer")
    return reminder_interval, clear_after_failures


def _validate_state(state: AlertLifecycleState) -> dict[str, ActiveSetup]:
    if not isinstance(state, AlertLifecycleState):
        raise LifecycleError("state must be an AlertLifecycleState")

    by_symbol: dict[str, ActiveSetup] = {}
    pending_ids: set[str] = set()
    for setup in state.setups:
        if not setup.symbol or setup.direction not in _DIRECTIONS:
            raise LifecycleError("active setup has an invalid symbol or direction")
        if setup.symbol in by_symbol:
            raise LifecycleError(f"multiple active directions for {setup.symbol}")
        if setup.consecutive_failures < 0:
            raise LifecycleError("consecutive_failures cannot be negative")
        if (
            isinstance(setup.strength, bool)
            or not isinstance(setup.strength, int)
            or not 1 <= setup.strength <= 100
        ):
            raise LifecycleError("active setup strength must be an integer from 1 through 100")
        if not math.isfinite(float(setup.score)) or not 0.0 <= float(setup.score) <= 100.0:
            raise LifecycleError("active setup score must be finite and between 0 and 100")
        opened_at = _utc(setup.opened_at_utc, "opened_at_utc")
        evaluated_at = _utc(setup.last_evaluated_at_utc, "last_evaluated_at_utc")
        if evaluated_at < opened_at:
            raise LifecycleError("last evaluation cannot precede setup opening")
        if setup.pending_notification_kind not in _NOTIFICATION_KINDS | {None}:
            raise LifecycleError("pending notification kind is invalid")
        pending_fields = (
            setup.pending_notification_id,
            setup.pending_notification_kind,
            setup.pending_notification_created_at_utc,
        )
        if any(value is None for value in pending_fields) != all(value is None for value in pending_fields):
            raise LifecycleError("pending notification fields must be set or cleared together")
        if setup.pending_notification_id:
            if setup.pending_notification_id in pending_ids:
                raise LifecycleError("duplicate pending notification id")
            pending_ids.add(setup.pending_notification_id)
            pending_at = _utc(setup.pending_notification_created_at_utc, "pending_notification_created_at_utc")
            if pending_at < opened_at or pending_at > evaluated_at:
                raise LifecycleError("pending notification time must belong to the evaluated setup span")
        if setup.last_successful_delivery_at_utc is not None:
            delivered_at = _utc(setup.last_successful_delivery_at_utc, "last_successful_delivery_at_utc")
            if delivered_at < opened_at:
                raise LifecycleError("last delivery cannot precede setup opening")
        by_symbol[setup.symbol] = setup
    return by_symbol


def _validate_signals(signals: tuple[AlertSignal, ...], scan_at_utc: datetime) -> dict[str, AlertSignal]:
    by_symbol: dict[str, AlertSignal] = {}
    for signal in signals:
        if not isinstance(signal, AlertSignal):
            raise LifecycleError("signals must contain AlertSignal values")
        if not signal.symbol or signal.direction not in _DIRECTIONS:
            raise LifecycleError("signal has an invalid symbol or direction")
        if signal.symbol in by_symbol:
            raise LifecycleError(f"multiple current directions for {signal.symbol}")
        if _utc(signal.observed_at_utc, "signal observed_at_utc") != scan_at_utc:
            raise LifecycleError("signal observation time does not match scan time")
        by_symbol[signal.symbol] = signal
    return by_symbol


def _intent(kind: str, signal: AlertSignal, created_at_utc: datetime, due_at_utc: datetime) -> NotificationIntent:
    notification_id = _notification_id(kind, signal.symbol, signal.direction, due_at_utc)
    return NotificationIntent(
        notification_id=notification_id,
        kind=kind,
        symbol=signal.symbol,
        direction=signal.direction,
        strength=signal.strength,
        score=signal.score,
        created_at_utc=created_at_utc,
    )


def _open_setup(signal: AlertSignal, scan_at_utc: datetime) -> tuple[ActiveSetup, NotificationIntent]:
    notification = _intent("INITIAL", signal, scan_at_utc, scan_at_utc)
    setup = ActiveSetup(
        symbol=signal.symbol,
        direction=signal.direction,
        opened_at_utc=scan_at_utc,
        last_evaluated_at_utc=scan_at_utc,
        strength=signal.strength,
        score=signal.score,
        pending_notification_id=notification.notification_id,
        pending_notification_kind=notification.kind,
        pending_notification_created_at_utc=scan_at_utc,
    )
    return setup, notification


def advance_lifecycle(
    state: AlertLifecycleState,
    signals: tuple[AlertSignal, ...],
    scan_at_utc: datetime,
    *,
    scan_succeeded: bool = True,
    reminder_interval: timedelta = DEFAULT_REMINDER_INTERVAL,
    clear_after_failures: int = DEFAULT_CLEAR_AFTER_FAILURES,
) -> LifecycleResult:
    """Advance active setups from one scanner observation.

    A failed scan is an unknown observation and leaves state untouched.  A
    successful scan may open, revalidate, put on probation, clear, or reverse
    setups.  Generated notification intents stay pending until explicitly
    acknowledged with :func:`acknowledge_delivery`.
    """

    scan_at = _utc(scan_at_utc, "scan_at_utc")
    reminder_interval, clear_after_failures = _validated_settings(
        reminder_interval,
        clear_after_failures,
    )
    active_by_symbol = _validate_state(state)

    if not scan_succeeded:
        if signals:
            raise LifecycleError("a failed scan cannot contain evaluated signals")
        return LifecycleResult(state=state)

    current_by_symbol = _validate_signals(signals, scan_at)
    for setup in active_by_symbol.values():
        temporal_floor = _utc(setup.last_evaluated_at_utc, "last_evaluated_at_utc")
        if setup.last_successful_delivery_at_utc is not None:
            temporal_floor = max(
                temporal_floor,
                _utc(setup.last_successful_delivery_at_utc, "last_successful_delivery_at_utc"),
            )
        if scan_at < temporal_floor:
            raise LifecycleError("scan time cannot precede prior lifecycle activity")

    next_by_symbol: dict[str, ActiveSetup] = {}
    notifications: list[NotificationIntent] = []
    cleared: list[tuple[str, str]] = []
    cancelled: list[str] = []
    previously_active_symbols = set(active_by_symbol)

    for symbol, setup in active_by_symbol.items():
        signal = current_by_symbol.get(symbol)

        if signal is not None and signal.direction != setup.direction and signal.initial_eligible:
            cleared.append(setup.key)
            if setup.pending_notification_id:
                cancelled.append(setup.pending_notification_id)
            replacement, notification = _open_setup(signal, scan_at)
            next_by_symbol[symbol] = replacement
            notifications.append(notification)
            continue

        if signal is not None and signal.direction == setup.direction and signal.continuation_eligible:
            updated = replace(
                setup,
                last_evaluated_at_utc=scan_at,
                strength=signal.strength,
                score=signal.score,
                consecutive_failures=0,
            )
            if updated.pending_notification_id is None and updated.last_successful_delivery_at_utc is not None:
                last_delivery = _utc(updated.last_successful_delivery_at_utc, "last_successful_delivery_at_utc")
                reminder_due_at = last_delivery + reminder_interval
                if scan_at >= reminder_due_at:
                    notification = _intent("REMINDER", signal, scan_at, reminder_due_at)
                    updated = replace(
                        updated,
                        pending_notification_id=notification.notification_id,
                        pending_notification_kind=notification.kind,
                        pending_notification_created_at_utc=scan_at,
                    )
                    notifications.append(notification)
            next_by_symbol[symbol] = updated
            continue

        failed_setup = setup
        if setup.pending_notification_kind == "REMINDER":
            cancelled.append(setup.pending_notification_id)
            failed_setup = replace(
                setup,
                pending_notification_id=None,
                pending_notification_kind=None,
                pending_notification_created_at_utc=None,
            )
        failures = failed_setup.consecutive_failures + 1
        if failures >= clear_after_failures:
            cleared.append(failed_setup.key)
            if failed_setup.pending_notification_id:
                cancelled.append(failed_setup.pending_notification_id)
            continue
        next_by_symbol[symbol] = replace(
            failed_setup,
            last_evaluated_at_utc=scan_at,
            consecutive_failures=failures,
        )

    for symbol, signal in current_by_symbol.items():
        if symbol in next_by_symbol or symbol in previously_active_symbols:
            continue
        if not signal.initial_eligible:
            continue
        setup, notification = _open_setup(signal, scan_at)
        next_by_symbol[symbol] = setup
        notifications.append(notification)

    next_state = AlertLifecycleState(
        setups=tuple(sorted(next_by_symbol.values(), key=lambda item: (item.symbol, item.direction)))
    )
    return LifecycleResult(
        state=next_state,
        notifications=tuple(sorted(notifications, key=lambda item: (item.symbol, item.direction, item.kind))),
        cleared_keys=tuple(sorted(cleared)),
        cancelled_notification_ids=tuple(sorted(cancelled)),
    )


def acknowledge_delivery(
    state: AlertLifecycleState,
    notification_id: str,
    delivered_at_utc: datetime,
) -> AlertLifecycleState:
    """Record one confirmed Discord delivery and start its reminder clock."""

    delivered_at = _utc(delivered_at_utc, "delivered_at_utc")
    _validate_state(state)
    matched = False
    updated: list[ActiveSetup] = []
    for setup in state.setups:
        if setup.pending_notification_id != notification_id:
            updated.append(setup)
            continue
        if matched:
            raise LifecycleError("notification id belongs to multiple active setups")
        created_at = _utc(setup.pending_notification_created_at_utc, "pending_notification_created_at_utc")
        if delivered_at < created_at:
            raise LifecycleError("delivery time cannot precede notification creation")
        matched = True
        updated.append(
            replace(
                setup,
                last_successful_delivery_at_utc=delivered_at,
                pending_notification_id=None,
                pending_notification_kind=None,
                pending_notification_created_at_utc=None,
            )
        )
    if not matched:
        raise LifecycleError("notification id is not pending")
    return AlertLifecycleState(setups=tuple(updated))
