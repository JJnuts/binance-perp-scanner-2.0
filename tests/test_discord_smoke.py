import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from perpscanner.alert_store import AlertStore
from perpscanner.discord_smoke import (
    DiscordSmokeError,
    DiscordSmokeResult,
    _result_json,
    main,
    run_controlled_discord_smoke,
)


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return {}


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class DiscordSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "discord-smoke.sqlite"
        self.now = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)

    @staticmethod
    def _webhook_url(token="controlled-smoke-token"):
        return "https://discord.com/api/" + "webhooks/123456789/" + token

    def test_success_uses_exact_message_and_records_delivery_without_storing_webhook(self):
        session = FakeSession(FakeResponse(204))
        result = run_controlled_discord_smoke(
            self._webhook_url(),
            session=session,
            database_path=self.database_path,
            clock=lambda: self.now,
        )

        self.assertTrue(result.delivered)
        self.assertEqual(result.status_code, 204)
        self.assertEqual(len(session.calls), 1)
        _, request = session.calls[0]
        self.assertEqual(
            request["json"],
            {
                "content": "TESTUSDT — LONG — Strength 75/100",
                "allowed_mentions": {"parse": []},
            },
        )
        self.assertFalse(request["allow_redirects"])
        message = AlertStore(self.database_path).list_outbox()[0]
        self.assertEqual(message.status, "sent")
        self.assertEqual(message.attempt_count, 1)
        self.assertNotIn(b"controlled-smoke-token", self.database_path.read_bytes())

    def test_failed_delivery_is_sanitized_and_left_retryable(self):
        result = run_controlled_discord_smoke(
            self._webhook_url("do-not-persist"),
            session=FakeSession(FakeResponse(503)),
            database_path=self.database_path,
            clock=lambda: self.now,
        )

        self.assertFalse(result.delivered)
        self.assertEqual(result.error_code, "DISCORD_SERVER_ERROR")
        message = AlertStore(self.database_path).list_outbox()[0]
        self.assertEqual(message.status, "pending")
        self.assertEqual(message.last_error_code, "DISCORD_SERVER_ERROR")
        self.assertNotIn("do-not-persist", _result_json(result))
        self.assertNotIn(b"do-not-persist", self.database_path.read_bytes())

    def test_existing_database_is_refused_without_modification(self):
        self.database_path.write_bytes(b"valuable existing bytes")

        with self.assertRaisesRegex(DiscordSmokeError, "new isolated"):
            run_controlled_discord_smoke(
                self._webhook_url(),
                session=FakeSession(FakeResponse(204)),
                database_path=self.database_path,
                clock=lambda: self.now,
            )

        self.assertEqual(self.database_path.read_bytes(), b"valuable existing bytes")

    def test_cli_requires_interactive_input_and_never_accepts_webhook_arguments(self):
        stderr = io.StringIO()
        with patch("perpscanner.discord_smoke.sys.stdin.isatty", return_value=False):
            with patch("perpscanner.discord_smoke.sys.stderr", stderr):
                exit_code = main()

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue().strip(), '{"error_code":"INTERACTIVE_TERMINAL_REQUIRED"}')

    def test_cli_output_does_not_echo_masked_webhook(self):
        secret = self._webhook_url("never-echo-this")
        stdout = io.StringIO()
        expected = DiscordSmokeResult(delivered=True, error_code=None, status_code=204)
        with patch("perpscanner.discord_smoke.sys.stdin.isatty", return_value=True):
            with patch("perpscanner.discord_smoke.getpass.getpass", return_value=secret):
                with patch("perpscanner.discord_smoke.run_controlled_discord_smoke", return_value=expected):
                    with patch("perpscanner.discord_smoke.sys.stdout", stdout):
                        exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertNotIn(secret, stdout.getvalue())
        self.assertNotIn("never-echo-this", stdout.getvalue())
        self.assertIn('"delivered": true', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
