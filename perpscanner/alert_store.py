"""Durable SQLite state and transactional outbox for setup alerts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from .alert_lifecycle import (
    ActiveSetup,
    AlertLifecycleState,
    LifecycleResult,
    NotificationIntent,
    acknowledge_delivery,
    validate_lifecycle_state,
)


SCHEMA_VERSION = 1
_OUTBOX_STATUSES = {"pending", "inflight", "sent", "cancelled"}
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_HEX_ID = re.compile(r"^[0-9a-f]{64}$")


class AlertStoreError(RuntimeError):
    """Alert persistence failed closed."""


class AlertStoreConflict(AlertStoreError):
    """A caller tried to apply state derived from an old revision."""


@dataclass(frozen=True)
class StoreSnapshot:
    revision: int
    state: AlertLifecycleState


@dataclass(frozen=True)
class OutboxMessage:
    notification_id: str
    kind: str
    symbol: str
    direction: str
    strength: int
    score: float
    created_at_utc: datetime
    status: str
    attempt_count: int
    next_attempt_at_utc: datetime | None
    lease_until_utc: datetime | None
    last_attempt_at_utc: datetime | None
    sent_at_utc: datetime | None
    last_error_code: str | None


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AlertStoreError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _encode_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value, "timestamp").isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decode_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AlertStoreError("alert database contains an invalid timestamp") from exc
    return _utc(parsed, "stored timestamp")


def _row_to_setup(row: sqlite3.Row) -> ActiveSetup:
    return ActiveSetup(
        symbol=row["symbol"],
        direction=row["direction"],
        opened_at_utc=_decode_ts(row["opened_at_utc"]),
        last_evaluated_at_utc=_decode_ts(row["last_evaluated_at_utc"]),
        strength=int(row["strength"]),
        score=float(row["score"]),
        consecutive_failures=int(row["consecutive_failures"]),
        last_successful_delivery_at_utc=_decode_ts(row["last_successful_delivery_at_utc"]),
        pending_notification_id=row["pending_notification_id"],
        pending_notification_kind=row["pending_notification_kind"],
        pending_notification_created_at_utc=_decode_ts(row["pending_notification_created_at_utc"]),
    )


def _row_to_outbox(row: sqlite3.Row) -> OutboxMessage:
    status = str(row["status"])
    if status not in _OUTBOX_STATUSES:
        raise AlertStoreError("alert database contains an invalid outbox status")
    return OutboxMessage(
        notification_id=row["notification_id"],
        kind=row["kind"],
        symbol=row["symbol"],
        direction=row["direction"],
        strength=int(row["strength"]),
        score=float(row["score"]),
        created_at_utc=_decode_ts(row["created_at_utc"]),
        status=status,
        attempt_count=int(row["attempt_count"]),
        next_attempt_at_utc=_decode_ts(row["next_attempt_at_utc"]),
        lease_until_utc=_decode_ts(row["lease_until_utc"]),
        last_attempt_at_utc=_decode_ts(row["last_attempt_at_utc"]),
        sent_at_utc=_decode_ts(row["sent_at_utc"]),
        last_error_code=row["last_error_code"],
    )


class AlertStore:
    """Own alert lifecycle state and its durable delivery outbox."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialize()

    def _raw_connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _connect(self) -> sqlite3.Connection:
        connection = self._raw_connect()
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection) -> Iterator[None]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed_nonempty = self.path.is_file() and self.path.stat().st_size > 0
        connection = self._raw_connect()
        try:
            if existed_nonempty:
                try:
                    marker = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'alert_store_meta'"
                    ).fetchone()
                except sqlite3.DatabaseError as exc:
                    raise AlertStoreError("existing alert database is unreadable") from exc
                if marker is None:
                    raise AlertStoreError("refusing to modify an existing non-alert SQLite database")

            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            with self._transaction(connection):
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alert_store_meta (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        schema_version INTEGER NOT NULL,
                        revision INTEGER NOT NULL CHECK (revision >= 0)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS active_setups (
                        symbol TEXT PRIMARY KEY,
                        direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                        opened_at_utc TEXT NOT NULL,
                        last_evaluated_at_utc TEXT NOT NULL,
                        strength INTEGER NOT NULL CHECK (strength BETWEEN 1 AND 100),
                        score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                        consecutive_failures INTEGER NOT NULL CHECK (consecutive_failures >= 0),
                        last_successful_delivery_at_utc TEXT,
                        pending_notification_id TEXT,
                        pending_notification_kind TEXT CHECK (
                            pending_notification_kind IS NULL OR
                            pending_notification_kind IN ('INITIAL', 'REMINDER')
                        ),
                        pending_notification_created_at_utc TEXT,
                        CHECK (
                            (pending_notification_id IS NULL AND
                             pending_notification_kind IS NULL AND
                             pending_notification_created_at_utc IS NULL) OR
                            (pending_notification_id IS NOT NULL AND
                             pending_notification_kind IS NOT NULL AND
                             pending_notification_created_at_utc IS NOT NULL)
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alert_outbox (
                        notification_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL CHECK (kind IN ('INITIAL', 'REMINDER')),
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
                        strength INTEGER NOT NULL CHECK (strength BETWEEN 1 AND 100),
                        score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                        created_at_utc TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('pending', 'inflight', 'sent', 'cancelled')
                        ),
                        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                        next_attempt_at_utc TEXT,
                        lease_until_utc TEXT,
                        last_attempt_at_utc TEXT,
                        sent_at_utc TEXT,
                        last_error_code TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_alert_outbox_due
                    ON alert_outbox(status, next_attempt_at_utc, created_at_utc)
                    """
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO alert_store_meta(singleton, schema_version, revision)
                    VALUES (1, ?, 0)
                    """,
                    (SCHEMA_VERSION,),
                )
                row = connection.execute(
                    "SELECT schema_version FROM alert_store_meta WHERE singleton = 1"
                ).fetchone()
                if row is None or int(row["schema_version"]) != SCHEMA_VERSION:
                    raise AlertStoreError("unsupported alert database schema version")
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not initialize alert database") from exc
        finally:
            connection.close()

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT revision FROM alert_store_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise AlertStoreError("alert database metadata is missing")
        return int(row["revision"])

    @staticmethod
    def _increment_revision(connection: sqlite3.Connection) -> int:
        connection.execute(
            "UPDATE alert_store_meta SET revision = revision + 1 WHERE singleton = 1"
        )
        return AlertStore._revision(connection)

    @staticmethod
    def _load_state(connection: sqlite3.Connection) -> AlertLifecycleState:
        rows = connection.execute(
            "SELECT * FROM active_setups ORDER BY symbol, direction"
        ).fetchall()
        state = AlertLifecycleState(setups=tuple(_row_to_setup(row) for row in rows))
        try:
            return validate_lifecycle_state(state)
        except ValueError as exc:
            raise AlertStoreError("alert database contains invalid lifecycle state") from exc

    @staticmethod
    def _replace_state(connection: sqlite3.Connection, state: AlertLifecycleState) -> None:
        validate_lifecycle_state(state)
        connection.execute("DELETE FROM active_setups")
        connection.executemany(
            """
            INSERT INTO active_setups (
                symbol, direction, opened_at_utc, last_evaluated_at_utc,
                strength, score, consecutive_failures,
                last_successful_delivery_at_utc,
                pending_notification_id, pending_notification_kind,
                pending_notification_created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    setup.symbol,
                    setup.direction,
                    _encode_ts(setup.opened_at_utc),
                    _encode_ts(setup.last_evaluated_at_utc),
                    setup.strength,
                    setup.score,
                    setup.consecutive_failures,
                    _encode_ts(setup.last_successful_delivery_at_utc),
                    setup.pending_notification_id,
                    setup.pending_notification_kind,
                    _encode_ts(setup.pending_notification_created_at_utc),
                )
                for setup in state.setups
            ],
        )

    def load_snapshot(self) -> StoreSnapshot:
        connection = self._connect()
        try:
            return StoreSnapshot(
                revision=self._revision(connection),
                state=self._load_state(connection),
            )
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not read alert database") from exc
        finally:
            connection.close()

    @staticmethod
    def _validate_notification(notification: NotificationIntent) -> None:
        if not isinstance(notification, NotificationIntent):
            raise AlertStoreError("lifecycle result contains an invalid notification")
        if not _HEX_ID.fullmatch(notification.notification_id):
            raise AlertStoreError("notification id is invalid")
        if notification.kind not in {"INITIAL", "REMINDER"}:
            raise AlertStoreError("notification kind is invalid")
        if not notification.symbol or notification.direction not in {"LONG", "SHORT"}:
            raise AlertStoreError("notification symbol or direction is invalid")
        if not 1 <= notification.strength <= 100 or not 0.0 <= notification.score <= 100.0:
            raise AlertStoreError("notification score is invalid")
        _utc(notification.created_at_utc, "notification created_at_utc")

    def apply_lifecycle_result(self, result: LifecycleResult, *, expected_revision: int) -> int:
        if not isinstance(result, LifecycleResult):
            raise AlertStoreError("result must be a LifecycleResult")
        try:
            validate_lifecycle_state(result.state)
        except ValueError as exc:
            raise AlertStoreError("lifecycle result contains invalid state") from exc
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise AlertStoreError("expected_revision must be a non-negative integer")

        notifications_by_id: dict[str, NotificationIntent] = {}
        for notification in result.notifications:
            self._validate_notification(notification)
            if notification.notification_id in notifications_by_id:
                raise AlertStoreError("lifecycle result contains a duplicate notification id")
            notifications_by_id[notification.notification_id] = notification
        cancelled_ids = tuple(result.cancelled_notification_ids)
        if any(not _HEX_ID.fullmatch(value or "") for value in cancelled_ids):
            raise AlertStoreError("cancelled notification id is invalid")
        if len(set(cancelled_ids)) != len(cancelled_ids):
            raise AlertStoreError("cancelled notification ids contain duplicates")
        pending_in_state = {
            setup.pending_notification_id
            for setup in result.state.setups
            if setup.pending_notification_id is not None
        }
        if not set(notifications_by_id).issubset(pending_in_state):
            raise AlertStoreError("new notifications must remain pending in lifecycle state")

        connection = self._connect()
        try:
            with self._transaction(connection):
                current_revision = self._revision(connection)
                if current_revision != expected_revision:
                    raise AlertStoreConflict(
                        f"alert store revision changed from {expected_revision} to {current_revision}"
                    )

                for notification in notifications_by_id.values():
                    existing = connection.execute(
                        "SELECT status FROM alert_outbox WHERE notification_id = ?",
                        (notification.notification_id,),
                    ).fetchone()
                    values = (
                        notification.kind,
                        notification.symbol,
                        notification.direction,
                        notification.strength,
                        notification.score,
                        _encode_ts(notification.created_at_utc),
                        _encode_ts(notification.created_at_utc),
                        notification.notification_id,
                    )
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO alert_outbox (
                                kind, symbol, direction, strength, score,
                                created_at_utc, status, attempt_count,
                                next_attempt_at_utc, notification_id
                            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                            """,
                            values,
                        )
                    elif existing["status"] == "cancelled":
                        connection.execute(
                            """
                            UPDATE alert_outbox
                            SET kind = ?, symbol = ?, direction = ?, strength = ?,
                                score = ?, created_at_utc = ?, status = 'pending',
                                attempt_count = 0, next_attempt_at_utc = ?,
                                lease_until_utc = NULL, last_attempt_at_utc = NULL,
                                sent_at_utc = NULL, last_error_code = NULL
                            WHERE notification_id = ?
                            """,
                            values,
                        )
                    else:
                        raise AlertStoreError("notification id already exists in the outbox")

                for notification_id in cancelled_ids:
                    updated = connection.execute(
                        """
                        UPDATE alert_outbox
                        SET status = 'cancelled', lease_until_utc = NULL,
                            next_attempt_at_utc = NULL
                        WHERE notification_id = ? AND status IN ('pending', 'inflight')
                        """,
                        (notification_id,),
                    ).rowcount
                    if updated != 1:
                        raise AlertStoreError("cancelled notification is not deliverable")

                for pending_id in pending_in_state:
                    row = connection.execute(
                        "SELECT status FROM alert_outbox WHERE notification_id = ?",
                        (pending_id,),
                    ).fetchone()
                    if row is None or row["status"] not in {"pending", "inflight"}:
                        raise AlertStoreError("lifecycle pending notification has no deliverable outbox row")

                deliverable_ids = {
                    row["notification_id"]
                    for row in connection.execute(
                        "SELECT notification_id FROM alert_outbox WHERE status IN ('pending', 'inflight')"
                    ).fetchall()
                }
                if deliverable_ids != pending_in_state:
                    raise AlertStoreError("deliverable outbox rows must match lifecycle pending state")

                self._replace_state(connection, result.state)
                return self._increment_revision(connection)
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not apply alert lifecycle result") from exc
        finally:
            connection.close()

    def list_outbox(self, *, status: str | None = None) -> tuple[OutboxMessage, ...]:
        if status is not None and status not in _OUTBOX_STATUSES:
            raise AlertStoreError("outbox status filter is invalid")
        connection = self._connect()
        try:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM alert_outbox ORDER BY created_at_utc, notification_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM alert_outbox WHERE status = ? ORDER BY created_at_utc, notification_id",
                    (status,),
                ).fetchall()
            return tuple(_row_to_outbox(row) for row in rows)
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not read alert outbox") from exc
        finally:
            connection.close()

    def claim_due(
        self,
        now_utc: datetime,
        *,
        limit: int = 20,
        lease: timedelta = timedelta(minutes=2),
    ) -> tuple[OutboxMessage, ...]:
        now = _utc(now_utc, "now_utc")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise AlertStoreError("limit must be a positive integer")
        if not isinstance(lease, timedelta) or lease <= timedelta(0):
            raise AlertStoreError("lease must be a positive timedelta")
        now_text = _encode_ts(now)
        lease_until = _encode_ts(now + lease)

        connection = self._connect()
        try:
            with self._transaction(connection):
                reclaimed = connection.execute(
                    """
                    UPDATE alert_outbox
                    SET status = 'pending', lease_until_utc = NULL,
                        next_attempt_at_utc = ?
                    WHERE status = 'inflight' AND lease_until_utc <= ?
                    """,
                    (now_text, now_text),
                ).rowcount
                due = connection.execute(
                    """
                    SELECT o.notification_id
                    FROM alert_outbox AS o
                    JOIN active_setups AS a
                      ON a.pending_notification_id = o.notification_id
                    WHERE o.status = 'pending'
                      AND o.next_attempt_at_utc <= ?
                      AND a.consecutive_failures = 0
                    ORDER BY o.next_attempt_at_utc, o.created_at_utc,
                             o.symbol, o.direction, o.notification_id
                    LIMIT ?
                    """,
                    (now_text, limit),
                ).fetchall()
                ids = [row["notification_id"] for row in due]
                for notification_id in ids:
                    connection.execute(
                        """
                        UPDATE alert_outbox
                        SET status = 'inflight', attempt_count = attempt_count + 1,
                            lease_until_utc = ?, last_attempt_at_utc = ?
                        WHERE notification_id = ? AND status = 'pending'
                        """,
                        (lease_until, now_text, notification_id),
                    )
                rows = []
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    rows = connection.execute(
                        f"SELECT * FROM alert_outbox WHERE notification_id IN ({placeholders})",
                        ids,
                    ).fetchall()
                if reclaimed or ids:
                    self._increment_revision(connection)
                by_id = {row["notification_id"]: _row_to_outbox(row) for row in rows}
                return tuple(by_id[notification_id] for notification_id in ids)
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not claim alert outbox messages") from exc
        finally:
            connection.close()

    def record_failure(
        self,
        notification_id: str,
        attempted_at_utc: datetime,
        retry_at_utc: datetime,
        error_code: str,
    ) -> int:
        attempted_at = _utc(attempted_at_utc, "attempted_at_utc")
        retry_at = _utc(retry_at_utc, "retry_at_utc")
        if retry_at < attempted_at:
            raise AlertStoreError("retry time cannot precede attempt time")
        if not _HEX_ID.fullmatch(notification_id or ""):
            raise AlertStoreError("notification id is invalid")
        if not isinstance(error_code, str) or not _SAFE_ERROR_CODE.fullmatch(error_code):
            raise AlertStoreError("error_code must be a sanitized machine-readable value")

        connection = self._connect()
        try:
            with self._transaction(connection):
                row = connection.execute(
                    """
                    SELECT status, last_attempt_at_utc FROM alert_outbox
                    WHERE notification_id = ?
                    """,
                    (notification_id,),
                ).fetchone()
                if row is None or row["status"] != "inflight":
                    raise AlertStoreError("notification is not currently claimed")
                last_attempt_at = _decode_ts(row["last_attempt_at_utc"])
                if last_attempt_at is None or attempted_at < last_attempt_at:
                    raise AlertStoreError("failure time cannot precede claim time")
                updated = connection.execute(
                    """
                    UPDATE alert_outbox
                    SET status = 'pending', next_attempt_at_utc = ?,
                        lease_until_utc = NULL, last_attempt_at_utc = ?,
                        last_error_code = ?
                    WHERE notification_id = ? AND status = 'inflight'
                    """,
                    (
                        _encode_ts(retry_at),
                        _encode_ts(attempted_at),
                        error_code,
                        notification_id,
                    ),
                ).rowcount
                if updated != 1:
                    raise AlertStoreError("notification is not currently claimed")
                return self._increment_revision(connection)
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not record alert delivery failure") from exc
        finally:
            connection.close()

    def record_delivery(self, notification_id: str, delivered_at_utc: datetime) -> int:
        delivered_at = _utc(delivered_at_utc, "delivered_at_utc")
        if not _HEX_ID.fullmatch(notification_id or ""):
            raise AlertStoreError("notification id is invalid")

        connection = self._connect()
        try:
            with self._transaction(connection):
                row = connection.execute(
                    "SELECT status, last_attempt_at_utc FROM alert_outbox WHERE notification_id = ?",
                    (notification_id,),
                ).fetchone()
                if row is None or row["status"] != "inflight":
                    raise AlertStoreError("notification is not currently claimed")
                last_attempt_at = _decode_ts(row["last_attempt_at_utc"])
                if last_attempt_at is None or delivered_at < last_attempt_at:
                    raise AlertStoreError("delivery time cannot precede claim time")
                state = self._load_state(connection)
                try:
                    acknowledged = acknowledge_delivery(state, notification_id, delivered_at)
                except ValueError as exc:
                    raise AlertStoreError("delivery does not match active lifecycle state") from exc
                connection.execute(
                    """
                    UPDATE alert_outbox
                    SET status = 'sent', sent_at_utc = ?, lease_until_utc = NULL,
                        next_attempt_at_utc = NULL, last_error_code = NULL
                    WHERE notification_id = ?
                    """,
                    (_encode_ts(delivered_at), notification_id),
                )
                self._replace_state(connection, acknowledged)
                return self._increment_revision(connection)
        except sqlite3.DatabaseError as exc:
            raise AlertStoreError("could not record alert delivery") from exc
        finally:
            connection.close()
