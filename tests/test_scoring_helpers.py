import unittest
import tempfile
from pathlib import Path

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

    def test_strike_expiry_context_adds_hover_and_table_fields(self):
        options = self._sample_options_df()
        strike_map = scanner._strike_map_for_options(options)

        context = scanner._strike_expiry_context(options, strike_map, 100.0)
        breakdown = scanner._strike_expiry_breakdown_table(options, 100.0)

        row = context[context["strike"] == 100.0].iloc[0]
        self.assertIn("0DTE", row["call_expiry_hover"])
        self.assertIn("0DTE", row["put_expiry_hover"])
        self.assertAlmostEqual(row["front_24h_share"], 1.0 / 3.0)
        self.assertFalse(breakdown.empty)
        self.assertIn("top_call_expiry", breakdown.columns)
        self.assertIn("front_7d_share", breakdown.columns)

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

    def test_prepare_bubble_frame_adds_spot_flow_fields(self):
        df = pd.DataFrame(
            {
                "ts": pd.date_range("2026-01-01", periods=12, freq="1D"),
                "close": [100.0 + i for i in range(12)],
                "quote_volume": [1000.0 + i * 100.0 for i in range(12)],
                "aggressive_buy_volume": [650.0 + i * 10.0 for i in range(12)],
                "aggressive_sell_volume": [350.0 + i * 5.0 for i in range(12)],
                "source": "Binance spot",
            }
        )

        out = scanner._prepare_bubble_frame(df, z_window=10)

        self.assertIn("spot_imbalance", out)
        self.assertIn("spot_cvd", out)
        self.assertIn("flow_state", out)
        self.assertGreater(out["spot_imbalance"].iloc[-1], 0.0)
        self.assertEqual(out["flow_state"].iloc[-1], "Strong Buy")

    def test_coinbase_premium_frame_computes_basis_points(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="1D")
        binance = pd.DataFrame({"ts": idx, "close": [100.0, 100.0, 100.0]})
        coinbase = pd.DataFrame({"ts": idx, "close": [101.0, 99.0, 100.5]})

        premium = scanner._coinbase_premium_frame(binance, coinbase)

        self.assertEqual(len(premium), 3)
        self.assertAlmostEqual(premium["coinbase_premium_bp"].iloc[0], 100.0)
        self.assertAlmostEqual(premium["coinbase_premium_bp"].iloc[1], -100.0)

    def test_spot_flow_summary_detects_accumulation(self):
        df = pd.DataFrame(
            {
                "ts": pd.date_range("2026-01-01", periods=8, freq="1D"),
                "close": [100.0, 100.1, 100.0, 100.2, 100.1, 100.2, 100.1, 100.2],
                "spot_cvd": [0.0, 100.0, 220.0, 340.0, 460.0, 600.0, 750.0, 900.0],
                "spot_imbalance": [0.12] * 8,
                "flow_state": ["Buy"] * 8,
            }
        )

        summary = scanner._spot_flow_summary(df, pd.DataFrame(), {"summary": {"latest_proxy_ratio": 0.2}})

        self.assertEqual(summary["state"], "Accumulation")
        self.assertGreater(summary["cvd_delta"], 0.0)

    def test_block_flow_gamma_map_signs_customer_direction(self):
        # Patch the constant in the module namespace that actually reads it
        # at call time (post-split, scanner.py is only a re-export shim).
        from perpscanner import data_deribit

        original_path = data_deribit.BTC_OPTIONS_BLOCK_DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                data_deribit.BTC_OPTIONS_BLOCK_DB_PATH = Path(tmpdir) / "blocks.sqlite"
                now_ms = int(scanner._utc_now_naive().timestamp() * 1000)
                scanner._store_deribit_block_trades(
                    [
                        {
                            "trade_id": "t1",
                            "block_trade_id": "b1",
                            "block_rfq_id": "rfq1",
                            "timestamp": now_ms,
                            "instrument_name": "BTC-30MAY26-100000-C",
                            "direction": "buy",
                            "amount": 2.0,
                            "price": 0.01,
                            "mark_price": 0.01,
                            "iv": 50.0,
                            "index_price": 100000.0,
                            "block_trade_leg_count": 1,
                        },
                        {
                            "trade_id": "t2",
                            "block_trade_id": "b2",
                            "timestamp": now_ms,
                            "instrument_name": "BTC-30MAY26-90000-P",
                            "direction": "sell",
                            "amount": 1.0,
                            "price": 0.01,
                            "mark_price": 0.01,
                            "iv": 55.0,
                            "index_price": 100000.0,
                            "block_trade_leg_count": 1,
                        },
                    ]
                )
                options = pd.DataFrame(
                    [
                        {
                            "instrument_name": "BTC-30MAY26-100000-C",
                            "strike": 100000.0,
                            "option_type": "call",
                            "gamma": 0.0001,
                            "contract_size": 1.0,
                            "underlying_price": 100000.0,
                        },
                        {
                            "instrument_name": "BTC-30MAY26-90000-P",
                            "strike": 90000.0,
                            "option_type": "put",
                            "gamma": 0.0001,
                            "contract_size": 1.0,
                            "underlying_price": 100000.0,
                        },
                    ]
                )

                flow = scanner._block_flow_gamma_map(options, days=7)
                block_map = flow["strike_map"].set_index("strike")

                self.assertLess(block_map.loc[100000.0, "block_adjusted_gex"], 0.0)
                self.assertGreater(block_map.loc[90000.0, "block_adjusted_gex"], 0.0)
                self.assertEqual(flow["matched_legs"], 2)
                self.assertEqual(flow["rfq_trades"], 1)
        finally:
            data_deribit.BTC_OPTIONS_BLOCK_DB_PATH = original_path


class ClosedBarTests(unittest.TestCase):
    def test_drop_unclosed_by_close_ts_removes_in_progress_bar(self):
        now_ms = scanner._epoch_ms_now()
        df = pd.DataFrame(
            {
                "close": [1.0, 2.0, 3.0],
                "close_ts": [now_ms - 120_000, now_ms - 60_000, now_ms + 240_000],
            }
        )

        out = scanner._drop_unclosed_by_close_ts(df)

        self.assertEqual(len(out), 2)
        self.assertEqual(out["close"].tolist(), [1.0, 2.0])

    def test_drop_unclosed_by_close_ts_keeps_all_closed_bars(self):
        now_ms = scanner._epoch_ms_now()
        df = pd.DataFrame({"close": [1.0, 2.0], "close_ts": [now_ms - 120_000, now_ms - 1_000]})

        self.assertEqual(len(scanner._drop_unclosed_by_close_ts(df)), 2)

    def test_drop_unclosed_by_interval_removes_partial_bucket(self):
        now = scanner._utc_now_naive()
        df = pd.DataFrame(
            {
                "ts": [now - pd.Timedelta(hours=24), now - pd.Timedelta(hours=12), now - pd.Timedelta(hours=3)],
                "close": [1.0, 2.0, 3.0],
            }
        )

        out = scanner._drop_unclosed_by_interval(df, pd.Timedelta(hours=12))

        self.assertEqual(out["close"].tolist(), [1.0, 2.0])

    def test_bybit_interval_to_timedelta(self):
        self.assertEqual(scanner._bybit_interval_to_timedelta("D"), pd.Timedelta(days=1))
        self.assertEqual(scanner._bybit_interval_to_timedelta("720"), pd.Timedelta(hours=12))
        self.assertEqual(scanner._bybit_interval_to_timedelta("240"), pd.Timedelta(hours=4))

    def test_fetch_klines_interval_drops_unclosed_candle(self):
        now_ms = scanner._epoch_ms_now()
        bar_ms = 60 * 60 * 1000
        rows = []
        for i in range(60, 0, -1):
            open_ms = now_ms - i * bar_ms
            rows.append(
                [open_ms, "1.0", "1.1", "0.9", "1.0", "10.0", open_ms + bar_ms - 1, "100.0", 50, "5.0", "50.0", "0"]
            )
        # In-progress candle: opened in the past, closes in the future.
        rows.append([now_ms, "1.0", "1.2", "0.9", "1.1", "3.0", now_ms + bar_ms - 1, "30.0", 9, "2.0", "15.0", "0"])

        # Patch in the consumer module: data_binance binds _get_json at import.
        from perpscanner import data_binance

        original_get_json = data_binance._get_json
        data_binance._get_json = lambda path, params=None, timeout=scanner.API_TIMEOUT: rows
        try:
            df = scanner._fetch_klines_interval("TESTUSDT", "1h", 240)
        finally:
            data_binance._get_json = original_get_json

        self.assertIsNotNone(df)
        self.assertEqual(len(df), 60)
        # The last remaining bar must be a fully closed one.
        self.assertEqual(float(df["quote_vol"].iloc[-1]), 100.0)


if __name__ == "__main__":
    unittest.main()
