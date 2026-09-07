"""Explicit interactive live Discord delivery proof.

Normal discovery skips this test. It runs only when named directly and reads
the webhook through the masked terminal prompt in perpscanner.discord_smoke.
"""

import sys
import unittest

from perpscanner.discord_smoke import main


def _explicitly_requested():
    return any(
        argument == __name__ or argument.startswith(f"{__name__}.")
        for argument in sys.argv[1:]
    )


@unittest.skipUnless(_explicitly_requested(), "live Discord smoke is interactive and opt-in")
class LiveDiscordSmokeTests(unittest.TestCase):
    def test_one_masked_input_delivery(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()
