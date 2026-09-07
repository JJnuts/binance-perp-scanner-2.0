import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import requests

from perpscanner.alert_store import OutboxMessage
from perpscanner.discord_transport import (
    DISCORD_WEBHOOK_ENV,
    MAX_RATE_LIMIT_DELAY,
    DiscordConfigurationError,
    DiscordWebhookClient,
    format_alert_message,
)


class FakeResponse:
    def __init__(self, status_code, *, body=None, headers=None, json_error=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._body


class FakeSession:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class DiscordTransportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def _webhook_url(token="unit-test-token"):
        # Keep a webhook-shaped literal out of source and secret-scanner output.
        return "https://discord.com/api/" + "webhooks/123456789/" + token

    def _message(self, **overrides):
        values = {
            "notification_id": "a" * 64,
            "kind": "INITIAL",
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "strength": 84,
            "score": 84.2,
            "created_at_utc": self.now,
            "status": "inflight",
            "attempt_count": 1,
            "next_attempt_at_utc": None,
            "lease_until_utc": self.now + timedelta(minutes=2),
            "last_attempt_at_utc": self.now,
            "sent_at_utc": None,
            "last_error_code": None,
        }
        values.update(overrides)
        return OutboxMessage(**values)

    def _client(self, outcome, **kwargs):
        session = FakeSession(outcome)
        client = DiscordWebhookClient(self._webhook_url(), session=session, **kwargs)
        return client, session

    def test_frozen_message_contains_only_ticker_direction_and_strength(self):
        initial = self._message()
        reminder = replace(initial, kind="REMINDER", strength=79)

        self.assertEqual(format_alert_message(initial), "SOLUSDT — LONG — Strength 84/100")
        self.assertEqual(format_alert_message(reminder), "SOLUSDT — LONG — Strength 79/100")

    def test_message_validation_blocks_mentions_newlines_and_invalid_values(self):
        invalid_messages = (
            self._message(symbol="@everyone"),
            self._message(symbol="SOLUSDT\nattack"),
            self._message(symbol="solusdt"),
            self._message(direction="BUY"),
            self._message(kind="UNKNOWN"),
            self._message(status="pending"),
            self._message(strength=0),
            self._message(strength=101),
            self._message(strength=True),
        )
        for message in invalid_messages:
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    format_alert_message(message)

    def test_success_posts_exact_payload_without_mentions_or_redirects(self):
        client, session = self._client(FakeResponse(204))

        result = client.send(self._message())

        self.assertTrue(result.delivered)
        self.assertEqual(result.status_code, 204)
        self.assertIsNone(result.error_code)
        self.assertEqual(len(session.calls), 1)
        _, kwargs = session.calls[0]
        self.assertEqual(
            kwargs["json"],
            {
                "content": "SOLUSDT — LONG — Strength 84/100",
                "allowed_mentions": {"parse": []},
            },
        )
        self.assertEqual(kwargs["timeout"], (3.05, 10.0))
        self.assertFalse(kwargs["allow_redirects"])

    def test_every_two_xx_response_is_success(self):
        for status in (200, 201, 204, 299):
            with self.subTest(status=status):
                client, _ = self._client(FakeResponse(status))
                self.assertTrue(client.send(self._message()).delivered)

    def test_timeout_connection_and_generic_request_failures_are_sanitized(self):
        cases = (
            (requests.Timeout(self._webhook_url("do-not-leak-timeout")), "DISCORD_TIMEOUT"),
            (requests.ConnectionError(self._webhook_url("do-not-leak-connection")), "DISCORD_CONNECTION_ERROR"),
            (requests.RequestException(self._webhook_url("do-not-leak-request")), "DISCORD_REQUEST_ERROR"),
        )
        for exception, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                client, _ = self._client(exception)
                result = client.send(self._message())
                self.assertFalse(result.delivered)
                self.assertEqual(result.error_code, expected_code)
                self.assertIsNotNone(result.retry_after)
                self.assertNotIn("do-not-leak", repr(result))

    def test_rate_limit_uses_json_retry_after(self):
        client, _ = self._client(FakeResponse(429, body={"retry_after": 12.5}))

        result = client.send(self._message())

        self.assertFalse(result.delivered)
        self.assertEqual(result.error_code, "DISCORD_RATE_LIMITED")
        self.assertEqual(result.retry_after, timedelta(seconds=12.5))

    def test_rate_limit_falls_back_to_header_and_bounds_delay(self):
        cases = (
            (FakeResponse(429, body={}, headers={"Retry-After": "7"}), timedelta(seconds=7)),
            (
                FakeResponse(429, json_error=ValueError("bad"), headers={"X-RateLimit-Reset-After": "999999"}),
                MAX_RATE_LIMIT_DELAY,
            ),
            (FakeResponse(429, body={"retry_after": -1}), timedelta(seconds=30)),
            (FakeResponse(429, body={"retry_after": 0}), timedelta(seconds=1)),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                client, _ = self._client(response)
                self.assertEqual(client.send(self._message()).retry_after, expected)

    def test_retryable_http_failures_have_machine_readable_codes(self):
        cases = (
            (408, "DISCORD_REQUEST_TIMEOUT"),
            (500, "DISCORD_SERVER_ERROR"),
            (503, "DISCORD_SERVER_ERROR"),
            (700, "DISCORD_UNEXPECTED_STATUS"),
        )
        for status, expected_code in cases:
            with self.subTest(status=status):
                client, _ = self._client(FakeResponse(status))
                result = client.send(self._message())
                self.assertEqual(result.error_code, expected_code)
                self.assertIsNotNone(result.retry_after)

    def test_client_and_redirect_failures_remain_safely_retryable(self):
        cases = (
            (302, "DISCORD_REDIRECT_REJECTED"),
            (400, "DISCORD_CLIENT_ERROR"),
            (401, "DISCORD_AUTH_REJECTED"),
            (403, "DISCORD_AUTH_REJECTED"),
            (404, "DISCORD_WEBHOOK_NOT_FOUND"),
        )
        for status, expected_code in cases:
            with self.subTest(status=status):
                client, session = self._client(FakeResponse(status))
                result = client.send(self._message())
                self.assertFalse(result.delivered)
                self.assertEqual(result.error_code, expected_code)
                self.assertEqual(result.retry_after, MAX_RATE_LIMIT_DELAY)
                self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_invalid_response_shape_is_retryable_without_response_details(self):
        client, _ = self._client(FakeResponse(None, body={"secret": "do-not-leak"}))

        result = client.send(self._message())

        self.assertEqual(result.error_code, "DISCORD_INVALID_RESPONSE")
        self.assertNotIn("do-not-leak", repr(result))

    def test_environment_factory_fails_closed_without_exposing_values(self):
        with self.assertRaisesRegex(DiscordConfigurationError, "not configured"):
            DiscordWebhookClient.from_environment(environ={})

        unsafe = "http://user:password@example.invalid/path?token=secret"
        with self.assertRaises(DiscordConfigurationError) as caught:
            DiscordWebhookClient.from_environment(environ={DISCORD_WEBHOOK_ENV: unsafe})
        self.assertNotIn("password", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_environment_factory_accepts_only_exact_runtime_variable(self):
        session = FakeSession(FakeResponse(204))
        client = DiscordWebhookClient.from_environment(
            environ={DISCORD_WEBHOOK_ENV: self._webhook_url()},
            session=session,
        )

        self.assertIn("configured", repr(client))
        self.assertNotIn("unit-test-token", repr(client))
        self.assertTrue(client.send(self._message()).delivered)

    def test_invalid_webhook_shapes_are_rejected_with_generic_error(self):
        invalid = (
            "http://discord.com/not-secure",
            "https://example.invalid/path",
            "https://discord.com/api/not-webhooks/123/token",
            self._webhook_url() + "?wait=true",
            "https://discord.com:invalid/api/path",
            "https://user:password@discord.com/api/path",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(DiscordConfigurationError) as caught:
                    DiscordWebhookClient(value, session=FakeSession(FakeResponse(204)))
                self.assertEqual(str(caught.exception), "Discord webhook configuration is invalid")
                self.assertNotIn(value, str(caught.exception))

    def test_timeout_configuration_must_be_positive_and_finite(self):
        for value in (0, -1, float("nan"), float("inf"), True, "bad"):
            with self.subTest(value=value):
                with self.assertRaises(DiscordConfigurationError):
                    DiscordWebhookClient(
                        self._webhook_url(),
                        session=FakeSession(FakeResponse(204)),
                        connect_timeout_s=value,
                    )


if __name__ == "__main__":
    unittest.main()
