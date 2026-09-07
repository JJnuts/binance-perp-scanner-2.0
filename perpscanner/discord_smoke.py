"""One-shot, masked-input Discord delivery proof for operator use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import getpass
import json
from pathlib import Path
import sys
import tempfile
from typing import Callable

from .alert_lifecycle import AlertLifecycleState, advance_lifecycle
from .alert_signals import AlertSignal
from .alert_store import AlertStore
from .discord_transport import DiscordWebhookClient


SMOKE_SYMBOL = "TESTUSDT"
SMOKE_DIRECTION = "LONG"
SMOKE_STRENGTH = 75


class DiscordSmokeError(RuntimeError):
    """The controlled Discord smoke could not complete safely."""


@dataclass(frozen=True)
class DiscordSmokeResult:
    delivered: bool
    error_code: str | None
    status_code: int | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_with_database(
    webhook_url: str,
    database_path: Path,
    *,
    session,
    at: datetime,
) -> DiscordSmokeResult:
    if database_path.exists():
        raise DiscordSmokeError("smoke database must be a new isolated file")

    store = AlertStore(database_path)
    signal = AlertSignal(
        symbol=SMOKE_SYMBOL,
        direction=SMOKE_DIRECTION,
        strength=SMOKE_STRENGTH,
        score=float(SMOKE_STRENGTH),
        observed_at_utc=at,
        initial_eligible=True,
        continuation_eligible=True,
    )
    lifecycle = advance_lifecycle(AlertLifecycleState(), (signal,), at)
    store.apply_lifecycle_result(lifecycle, expected_revision=0)
    claimed = store.claim_due(at, limit=1, lease=timedelta(minutes=2))
    if len(claimed) != 1:
        raise DiscordSmokeError("smoke message could not be claimed safely")

    client = DiscordWebhookClient(webhook_url, session=session)
    delivery = client.send(claimed[0])
    if delivery.delivered:
        store.record_delivery(claimed[0].notification_id, at)
    else:
        retry_delay = delivery.retry_after or timedelta(minutes=1)
        store.record_failure(
            claimed[0].notification_id,
            at,
            at + retry_delay,
            delivery.error_code or "DISCORD_UNKNOWN_ERROR",
        )
    return DiscordSmokeResult(
        delivered=delivery.delivered,
        error_code=delivery.error_code,
        status_code=delivery.status_code,
    )


def run_controlled_discord_smoke(
    webhook_url: str,
    *,
    session=None,
    database_path: str | Path | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> DiscordSmokeResult:
    """Send one TESTUSDT alert through the production transport path.

    The webhook is passed only to the in-memory transport and is never written
    to SQLite or included in the returned result.  With no database path, all
    lifecycle/outbox evidence is held in a temporary directory and removed on
    return.
    """

    at = clock()
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise DiscordSmokeError("smoke clock must return a timezone-aware datetime")
    at = at.astimezone(timezone.utc)
    if database_path is not None:
        return _run_with_database(
            webhook_url,
            Path(database_path),
            session=session,
            at=at,
        )
    with tempfile.TemporaryDirectory(prefix="perpscanner-discord-smoke-") as temporary_directory:
        return _run_with_database(
            webhook_url,
            Path(temporary_directory) / "alerts.sqlite",
            session=session,
            at=at,
        )


def _result_json(result: DiscordSmokeResult) -> str:
    return json.dumps(
        {
            "delivered": result.delivered,
            "error_code": result.error_code,
            "status_code": result.status_code,
            "test_message": f"{SMOKE_SYMBOL} — {SMOKE_DIRECTION} — Strength {SMOKE_STRENGTH}/100",
        },
        sort_keys=True,
    )


def main() -> int:
    if not sys.stdin.isatty():
        print('{"error_code":"INTERACTIVE_TERMINAL_REQUIRED"}', file=sys.stderr)
        return 2

    webhook_url = getpass.getpass("Replacement Discord webhook URL (hidden input): ")
    try:
        result = run_controlled_discord_smoke(webhook_url)
    except Exception:
        print('{"error_code":"DISCORD_SMOKE_FAILED"}', file=sys.stderr)
        return 1
    finally:
        webhook_url = ""

    print(_result_json(result))
    return 0 if result.delivered else 1


if __name__ == "__main__":
    raise SystemExit(main())
