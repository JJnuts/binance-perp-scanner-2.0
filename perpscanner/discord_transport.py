"""Secret-safe Discord webhook transport for claimed alert messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math
import os
import re
from typing import Mapping
from urllib.parse import urlsplit

import requests

from .alert_store import OutboxMessage


DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
DEFAULT_RETRY_DELAY = timedelta(seconds=30)
MAX_RATE_LIMIT_DELAY = timedelta(minutes=15)
_WEBHOOK_HOSTS = {"discord.com", "ptb.discord.com", "canary.discord.com"}
_WEBHOOK_PATH = re.compile(r"^/api(?:/v[0-9]+)?/webhooks/[0-9]+/[A-Za-z0-9._-]+/?$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")


class DiscordConfigurationError(ValueError):
    """Discord delivery is unavailable because configuration is missing or unsafe."""


@dataclass(frozen=True)
class DiscordDeliveryResult:
    delivered: bool
    error_code: str | None = None
    retry_after: timedelta | None = None
    status_code: int | None = None


def format_alert_message(message: OutboxMessage) -> str:
    """Return the frozen ticker/direction/strength Discord content."""

    if not isinstance(message, OutboxMessage):
        raise ValueError("message must be an OutboxMessage")
    if not _SYMBOL.fullmatch(message.symbol or ""):
        raise ValueError("alert symbol is invalid")
    if message.direction not in {"LONG", "SHORT"}:
        raise ValueError("alert direction is invalid")
    if message.kind not in {"INITIAL", "REMINDER"}:
        raise ValueError("alert notification kind is invalid")
    if message.status != "inflight":
        raise ValueError("only a claimed in-flight alert can be delivered")
    if (
        isinstance(message.strength, bool)
        or not isinstance(message.strength, int)
        or not 1 <= message.strength <= 100
    ):
        raise ValueError("alert strength must be an integer from 1 through 100")
    return f"{message.symbol} — {message.direction} — Strength {message.strength}/100"


def _validated_webhook_url(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiscordConfigurationError(f"{DISCORD_WEBHOOK_ENV} is not configured")
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise DiscordConfigurationError("Discord webhook configuration is invalid") from exc
    if (
        parsed.scheme != "https"
        or hostname not in _WEBHOOK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not _WEBHOOK_PATH.fullmatch(parsed.path)
    ):
        raise DiscordConfigurationError("Discord webhook configuration is invalid")
    return candidate


def _bounded_retry_delay(value: object, default: timedelta = DEFAULT_RETRY_DELAY) -> timedelta:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(seconds) or seconds < 0.0:
        return default
    maximum = MAX_RATE_LIMIT_DELAY.total_seconds()
    return timedelta(seconds=min(maximum, max(1.0, seconds)))


def _rate_limit_delay(response) -> timedelta:
    value = None
    try:
        body = response.json()
        if isinstance(body, dict):
            value = body.get("retry_after")
    except (TypeError, ValueError, requests.RequestException):
        pass
    if value is None:
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("Retry-After") or headers.get("X-RateLimit-Reset-After")
    return _bounded_retry_delay(value)


class DiscordWebhookClient:
    """Send one already-claimed outbox message without leaking its webhook."""

    def __init__(
        self,
        webhook_url: str,
        *,
        session=None,
        connect_timeout_s: float = 3.05,
        read_timeout_s: float = 10.0,
    ):
        self._webhook_url = _validated_webhook_url(webhook_url)
        self._session = session or requests.Session()
        self._timeout = (
            self._positive_timeout(connect_timeout_s, "connect_timeout_s"),
            self._positive_timeout(read_timeout_s, "read_timeout_s"),
        )

    @staticmethod
    def _positive_timeout(value: float, name: str) -> float:
        if isinstance(value, bool):
            raise DiscordConfigurationError(f"{name} must be a positive finite number")
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise DiscordConfigurationError(f"{name} must be a positive finite number") from exc
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise DiscordConfigurationError(f"{name} must be a positive finite number")
        return timeout

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        session=None,
        connect_timeout_s: float = 3.05,
        read_timeout_s: float = 10.0,
    ) -> "DiscordWebhookClient":
        source = os.environ if environ is None else environ
        return cls(
            source.get(DISCORD_WEBHOOK_ENV),
            session=session,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def __repr__(self) -> str:
        return "DiscordWebhookClient(webhook=<configured>)"

    def send(self, message: OutboxMessage) -> DiscordDeliveryResult:
        content = format_alert_message(message)
        payload = {
            "content": content,
            "allowed_mentions": {"parse": []},
        }
        try:
            response = self._session.post(
                self._webhook_url,
                json=payload,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_TIMEOUT",
                retry_after=DEFAULT_RETRY_DELAY,
            )
        except requests.ConnectionError:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_CONNECTION_ERROR",
                retry_after=DEFAULT_RETRY_DELAY,
            )
        except requests.RequestException:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_REQUEST_ERROR",
                retry_after=timedelta(minutes=1),
            )

        try:
            status = int(response.status_code)
        except (AttributeError, TypeError, ValueError):
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_INVALID_RESPONSE",
                retry_after=timedelta(minutes=1),
            )

        if 200 <= status < 300:
            return DiscordDeliveryResult(delivered=True, status_code=status)
        if status == 429:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_RATE_LIMITED",
                retry_after=_rate_limit_delay(response),
                status_code=status,
            )
        if status == 408:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_REQUEST_TIMEOUT",
                retry_after=DEFAULT_RETRY_DELAY,
                status_code=status,
            )
        if 500 <= status < 600:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_SERVER_ERROR",
                retry_after=timedelta(minutes=1),
                status_code=status,
            )
        if status in {401, 403}:
            error_code = "DISCORD_AUTH_REJECTED"
        elif status == 404:
            error_code = "DISCORD_WEBHOOK_NOT_FOUND"
        elif 300 <= status < 400:
            error_code = "DISCORD_REDIRECT_REJECTED"
        elif 400 <= status < 500:
            error_code = "DISCORD_CLIENT_ERROR"
        else:
            return DiscordDeliveryResult(
                delivered=False,
                error_code="DISCORD_UNEXPECTED_STATUS",
                retry_after=timedelta(minutes=1),
                status_code=status,
            )
        return DiscordDeliveryResult(
            delivered=False,
            error_code=error_code,
            retry_after=MAX_RATE_LIMIT_DELAY,
            status_code=status,
        )
