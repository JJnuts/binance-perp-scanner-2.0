import unittest

import pandas as pd

import scanner


class ScoringHelperTests(unittest.TestCase):
    def test_vwap_uses_typical_price_weighted_by_volume(self):
        df = pd.DataFrame(
            {
                "high": [12.0, 15.0],
                "low": [8.0, 9.0],
                "close": [10.0, 12.0],
                "vol": [100.0, 300.0],
            }
        )

        self.assertAlmostEqual(scanner._vwap(df, 2), 11.5)

    def test_vwap_falls_back_to_last_close_when_volume_is_zero(self):
        df = pd.DataFrame(
            {
                "high": [12.0, 15.0],
                "low": [8.0, 9.0],
                "close": [10.0, 12.0],
                "vol": [0.0, 0.0],
            }
        )

        self.assertEqual(scanner._vwap(df, 2), 12.0)

    def test_percentile_score_orders_values(self):
        scores = scanner._percentile_score(pd.Series([10.0, 20.0, 30.0]))

        for actual, expected in zip(scores.tolist(), [100.0 / 3.0, 200.0 / 3.0, 100.0]):
            self.assertAlmostEqual(actual, expected)

    def test_percentile_score_can_invert_order(self):
        scores = scanner._percentile_score(pd.Series([10.0, 20.0, 30.0]), ascending=False)

        for actual, expected in zip(scores.tolist(), [200.0 / 3.0, 100.0 / 3.0, 0.0]):
            self.assertAlmostEqual(actual, expected)

    def test_funding_quality_rewards_mild_positive_uncrowded_funding(self):
        funding_z = pd.Series([0.0, 2.5, -3.0])
        funding_rate = pd.Series([0.0004, 0.0010, -0.0005])

        scores = scanner._funding_quality_score(funding_z, funding_rate)

        self.assertAlmostEqual(scores.iloc[0], 100.0)
        self.assertAlmostEqual(scores.iloc[1], 0.0)
        self.assertAlmostEqual(scores.iloc[2], 0.0)

    def test_beta_adjusted_alpha_strips_beta_move(self):
        btc = pd.Series([100.0 + i for i in range(40)])
        asset = pd.Series([200.0 + 4.0 * i for i in range(40)])
        beta = scanner._estimate_beta(scanner._aligned_return_frame(asset, btc), beta_lookback=30)
        alpha = scanner._alpha_from_beta(asset, btc, 4, beta)

        self.assertGreater(beta, 1.0)
        self.assertAlmostEqual(alpha, scanner._beta_adjusted_alpha(asset, btc, 4, beta_lookback=30))


if __name__ == "__main__":
    unittest.main()
