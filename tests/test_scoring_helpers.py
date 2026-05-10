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

    def test_bars_since_latest_true_reports_age(self):
        series = pd.Series([False, True, False, True, False])

        self.assertEqual(scanner._bars_since_latest_true(series), 1)
        self.assertEqual(scanner._bars_since_latest_true(pd.Series([False, False])), 999)

    def test_basis_context_uses_spot_alignment(self):
        idx = pd.date_range("2026-01-01", periods=4, freq="5min")
        perp = pd.DataFrame({"close": [100.0, 101.0, 103.0, 105.0]}, index=idx)
        spot = pd.DataFrame({"close": [100.0, 100.5, 101.0, 102.0]}, index=idx)

        basis_bp, basis_delta, available = scanner._basis_context(perp, spot)

        self.assertTrue(available)
        self.assertGreater(basis_bp, 0.0)
        self.assertGreater(basis_delta, 0.0)

    def test_ltf_interval_metrics_emits_precision_fields(self):
        idx = pd.date_range("2026-01-01", periods=80, freq="5min")
        close = pd.Series([100.0 + i * 0.1 for i in range(80)], index=idx)
        df = pd.DataFrame(
            {
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
                "vol": [100.0 + i for i in range(80)],
                "quote_vol": [10000.0 + i * 100.0 for i in range(80)],
                "trades": [100 + i for i in range(80)],
                "tb_quote": [5200.0 + i * 55.0 for i in range(80)],
            },
            index=idx,
        )
        spot = df.copy()
        spot["close"] = spot["close"] * 0.999

        row = scanner._ltf_interval_metrics("TESTUSDT", "5m", df, pd.DataFrame(), df, df, spot)

        for field in [
            "bars_since_trigger",
            "trigger_fresh",
            "taker_imbalance",
            "cvd_3bar_slope",
            "basis_bp",
            "basis_delta_3bar_bp",
            "break_hold_confirmed",
        ]:
            self.assertIn(field, row)

    def test_daily_swing_context_emits_swing_fields(self):
        idx = pd.date_range("2026-01-01", periods=60, freq="1D")
        close = pd.Series([100.0 + i * 0.5 for i in range(60)], index=idx)
        daily = pd.DataFrame(
            {
                "open": close - 0.3,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "vol": [100.0 + i for i in range(60)],
                "quote_vol": [10000.0 + i * 250.0 for i in range(60)],
                "trades": [100 + i for i in range(60)],
                "tb_quote": [5200.0 + i * 130.0 for i in range(60)],
            },
            index=idx,
        )
        daily.iloc[-1, daily.columns.get_loc("close")] = daily["high"].iloc[-2] + 2.0
        daily.iloc[-1, daily.columns.get_loc("high")] = daily["close"].iloc[-1] + 1.0
        oi = pd.DataFrame({"oi_value": [1000.0 + i * 10.0 for i in range(20)]}, index=idx[-20:])

        context = scanner._daily_swing_context(daily, oi, daily)

        self.assertIn("daily_structure_score", context)
        self.assertTrue(context["daily_close_above_prior_high"])
        self.assertGreaterEqual(context["daily_oi_persistence_days"], 1)
        self.assertGreaterEqual(context["daily_volume_persistence_days"], 1)

    def test_btc_daily_regime_scores_bull_trend(self):
        idx = pd.date_range("2026-01-01", periods=60, freq="1D")
        close = pd.Series([100.0 + i for i in range(60)], index=idx)
        daily = pd.DataFrame(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "vol": 100.0,
                "quote_vol": 10000.0,
                "trades": 100,
                "tb_quote": 5200.0,
            },
            index=idx,
        )

        regime = scanner._btc_daily_regime(daily)

        self.assertEqual(regime["btc_daily_regime"], "Bull trend")
        self.assertGreater(regime["btc_long_multiplier"], 1.0)

    def _sample_options_df(self):
        now = scanner._utc_now_naive()
        rows = []
        for hours, label in [(8, "0DTE"), (72, "3D"), (720, "30D")]:
            expiry = now + pd.Timedelta(hours=hours)
            for opt_type, delta, strike, iv in [
                ("call", 0.25, 105.0, 62.0),
                ("call", 0.50, 100.0, 60.0),
                ("put", -0.25, 95.0, 66.0),
                ("put", -0.50, 100.0, 64.0),
            ]:
                rows.append(
                    {
                        "expiry_label": label,
                        "expiration_ts": expiry,
                        "strike": strike,
                        "option_type": opt_type,
                        "effective_iv": iv,
                        "delta": delta,
                        "gamma": 0.001,
                        "vega": 1.2,
                        "open_interest": 10.0,
                        "contract_size": 1.0,
                        "hours_to_expiry": hours,
                        "moneyness_pct": (strike / 100.0 - 1.0) * 100.0,
                        "gex_abs": 10.0,
                        "call_gex": 10.0 if opt_type == "call" else 0.0,
                        "put_gex": 10.0 if opt_type == "put" else 0.0,
                        "signed_gex": 10.0 if opt_type == "call" else -10.0,
                    }
                )
        return pd.DataFrame(rows)

    def test_front_gex_summary_and_pin_candidate(self):
        options = self._sample_options_df()

        front = scanner._front_gex_summary(options, 100.0, 24, "Front 24H")
        pin = scanner._pin_candidate(options, 100.0)

        self.assertGreater(front["abs_gex"], 0.0)
        self.assertEqual(front["contracts"], 4)
        self.assertGreater(pin["pin_score"], 0.0)
        self.assertEqual(pin["pin_strike"], 100.0)

    def test_risk_reversal_and_term_structure(self):
        options = self._sample_options_df()
        rr = scanner._risk_reversal_by_expiry(options)
        atm_iv = pd.DataFrame(
            {
                "expiry_label": ["0DTE", "30D"],
                "expiration_ts": [options["expiration_ts"].min(), options["expiration_ts"].max()],
                "effective_iv": [70.0, 60.0],
            }
        )

        term = scanner._iv_term_structure(atm_iv)

        self.assertFalse(rr.empty)
        self.assertAlmostEqual(rr["risk_reversal"].iloc[0], -4.0)
        self.assertEqual(term["term_regime"], "Backwardation")

    def test_pressure_forecast_is_labeled_estimate(self):
        pressure = scanner._pressure_forecast(self._sample_options_df(), iv_change_points=-2.0)

        self.assertIn("Estimated", pressure["pressure_copy"])
        self.assertIn(pressure["pressure_bias"], {"Upside pressure", "Downside pressure", "Neutral"})


if __name__ == "__main__":
    unittest.main()
