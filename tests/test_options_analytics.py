import unittest

import pandas as pd

from perpscanner.options_frames import _merge_deribit_option_tickers


class DeribitOptionMergeTests(unittest.TestCase):
    def test_live_ticker_iv_and_underlying_remain_unsuffixed(self):
        summaries = pd.DataFrame(
            {
                "instrument_name": ["BTC-30AUG26-65000-C"],
                "mark_iv": [19.0],
                "underlying_price": [63_500.0],
                "open_interest": [10.0],
            }
        )
        tickers = pd.DataFrame(
            {
                "instrument_name": ["BTC-30AUG26-65000-C"],
                "mark_iv": [24.5],
                "underlying_price": [64_250.0],
                "bid_iv": [24.0],
                "ask_iv": [25.0],
            }
        )

        result = _merge_deribit_option_tickers(summaries, tickers)

        self.assertEqual(result.loc[0, "mark_iv"], 24.5)
        self.assertEqual(result.loc[0, "underlying_price"], 64_250.0)
        self.assertEqual(result.loc[0, "mark_iv_summary"], 19.0)
        self.assertEqual(result.loc[0, "underlying_price_summary"], 63_500.0)
        self.assertEqual(result.loc[0, "open_interest"], 10.0)


if __name__ == "__main__":
    unittest.main()
