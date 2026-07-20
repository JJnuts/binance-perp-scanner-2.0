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

        # 2026-07-19 rename: "Pressure Est." -> "8H Chain Delta-Drift Proxy".
        # The invariant this test protects is unchanged and stricter: the copy
        # must state that dealer direction is inferred, never observed.
        self.assertIn("proxy", pressure["pressure_copy"])
        self.assertIn("inferred, not observed", pressure["pressure_copy"])
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


class RegimeThresholdTests(unittest.TestCase):
    def test_bull_trend_raises_gates(self):
        thr = scanner.thresholds_for_regime({"btc_daily_regime": "Bull trend"})

        self.assertGreater(thr.volume_z, scanner.VOLUME_Z_THRESHOLD)
        self.assertGreater(thr.oi_z, scanner.OI_Z_THRESHOLD)
        self.assertGreater(thr.atr_roc, scanner.ATR_ROC_THRESHOLD)
        self.assertEqual(thr.regime, "Bull trend")

    def test_range_lowers_gates(self):
        thr = scanner.thresholds_for_regime({"btc_daily_regime": "Range"})

        self.assertLess(thr.volume_z, scanner.VOLUME_Z_THRESHOLD)
        self.assertLess(thr.oi_z, scanner.OI_Z_THRESHOLD)

    def test_unknown_regime_keeps_static_defaults(self):
        for regime_dict in (None, {}, {"btc_daily_regime": "Sideways-ish"}):
            thr = scanner.thresholds_for_regime(regime_dict)
            self.assertEqual(thr.volume_z, scanner.VOLUME_Z_THRESHOLD)
            self.assertEqual(thr.oi_z, scanner.OI_Z_THRESHOLD)
            self.assertEqual(thr.atr_roc, scanner.ATR_ROC_THRESHOLD)

    def _trending_frame(self):
        idx = pd.date_range("2026-01-01", periods=80, freq="5min")
        close = pd.Series([100.0 + i * 0.1 for i in range(80)], index=idx)
        return pd.DataFrame(
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

    def test_ltf_metrics_emit_confluence_and_gate_fields(self):
        df = self._trending_frame()
        row = scanner._ltf_interval_metrics("TESTUSDT", "5m", df, pd.DataFrame(), df, df, None)

        for field in ("confluence_long", "confluence_short", "regime", "volume_z_gate", "oi_z_gate", "atr_roc_gate"):
            self.assertIn(field, row)
        self.assertGreaterEqual(row["confluence_long"], 0.0)
        self.assertLessEqual(row["confluence_long"], 1.0)

    def test_ltf_metrics_emit_continuous_conviction(self):
        df = self._trending_frame()
        row = scanner._ltf_interval_metrics("TESTUSDT", "5m", df, pd.DataFrame(), df, df, None)

        for field in (
            "conviction_long",
            "conviction_short",
            "conviction_net",
            "comp_expansion",
            "comp_volume",
            "comp_oi",
            "comp_taker_net",
            "comp_basis_net",
            "veto_side",
        ):
            self.assertIn(field, row)
        self.assertGreaterEqual(row["conviction_long"], 0.0)
        self.assertLessEqual(row["conviction_long"], 1.0)
        self.assertGreaterEqual(row["conviction_short"], 0.0)
        self.assertLessEqual(row["conviction_short"], 1.0)
        self.assertAlmostEqual(row["conviction_net"], row["conviction_long"] - row["conviction_short"])
        for comp in ("comp_expansion", "comp_volume", "comp_oi"):
            self.assertGreaterEqual(row[comp], 0.0)
            self.assertLessEqual(row[comp], 1.0)
        self.assertGreaterEqual(row["comp_taker_net"], -1.0)
        self.assertLessEqual(row["comp_taker_net"], 1.0)
        self.assertIn(row["veto_side"], {"Long", "Short", "None"})

    def test_tighter_thresholds_lower_spike_scores(self):
        df = self._trending_frame()
        loose = scanner._ltf_interval_metrics(
            "TESTUSDT", "5m", df, pd.DataFrame(), df, df, None,
            thresholds=scanner.RegimeThresholds(volume_z=1.0, oi_z=1.0, atr_roc=0.04, regime="Range"),
        )
        tight = scanner._ltf_interval_metrics(
            "TESTUSDT", "5m", df, pd.DataFrame(), df, df, None,
            thresholds=scanner.RegimeThresholds(volume_z=4.0, oi_z=4.0, atr_roc=0.32, regime="Bull trend"),
        )

        self.assertGreaterEqual(loose["atr_expansion_score"], tight["atr_expansion_score"])
        self.assertEqual(loose["regime"], "Range")
        self.assertEqual(tight["regime"], "Bull trend")


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


class LtfSpotMappingTests(unittest.TestCase):
    def test_spot_equivalent_maps_multiplied_perps(self):
        self.assertEqual(scanner._spot_equivalent("1000PEPEUSDT"), ("PEPEUSDT", 1000.0))
        self.assertEqual(scanner._spot_equivalent("1MBABYDOGEUSDT"), ("BABYDOGEUSDT", 1_000_000.0))
        self.assertEqual(scanner._spot_equivalent("1000000MOGUSDT"), ("MOGUSDT", 1_000_000.0))
        # Plain symbols map to themselves.
        self.assertEqual(scanner._spot_equivalent("BTCUSDT"), ("BTCUSDT", 1.0))
        self.assertEqual(scanner._spot_equivalent("ETHUSDT"), ("ETHUSDT", 1.0))

    def test_scale_spot_frame_keeps_basis_math_exact(self):
        df = pd.DataFrame(
            {
                "open": [0.001], "high": [0.0011], "low": [0.0009], "close": [0.001],
                "vol": [1_000_000.0], "quote_vol": [1000.0], "trades": [10.0], "tb_quote": [500.0],
            }
        )

        scaled = scanner._scale_spot_frame(df, 1000.0)

        self.assertAlmostEqual(float(scaled["close"].iloc[0]), 1.0)
        self.assertAlmostEqual(float(scaled["vol"].iloc[0]), 1000.0)
        self.assertAlmostEqual(float(scaled["quote_vol"].iloc[0]), 1000.0)  # quote units untouched
        # Multiplier 1.0 must be a no-op passthrough.
        self.assertIs(scanner._scale_spot_frame(df, 1.0), df)

    def test_ltf_spot_context_falls_back_to_stripped_symbol(self):
        from perpscanner import data_binance

        idx_ms = 1_700_000_000_000
        closed_ms = scanner._epoch_ms_now() - 60_000

        def fake_get_json_url(url, params=None, headers=None, timeout=15):
            params = params or {}
            if params.get("symbol") == "1000FAKEUSDT":
                raise RuntimeError("400 invalid symbol")
            rows = []
            for i in range(70):
                open_ms = idx_ms + i * 300_000
                rows.append(
                    [open_ms, "0.001", "0.0011", "0.0009", "0.001", "1000000.0",
                     closed_ms, "1000.0", 10, "500000.0", "500.0", "0"]
                )
            return rows

        original = data_binance._get_json_url
        data_binance._get_json_url = fake_get_json_url
        try:
            symbol, klines = data_binance._fetch_ltf_spot_symbol_context("1000FAKEUSDT")
        finally:
            data_binance._get_json_url = original

        self.assertEqual(symbol, "1000FAKEUSDT")
        self.assertEqual(set(klines), set(scanner.LTF_INTERVALS))
        # Spot close must be rescaled into the perp's 1000x price space.
        self.assertAlmostEqual(float(klines["5m"]["close"].iloc[-1]), 1.0)


class BtcScreenerTests(unittest.TestCase):
    def test_positioning_cards_survive_empty_sweeps(self):
        # Regression: latest_sweep was None with empty sweeps and the
        # .startswith call crashed the whole options page.
        avwap_df = pd.DataFrame(
            [{"close": 101.0, "avwap": 100.0, "band_2_up": 105.0, "band_2_dn": 95.0}]
        )
        bundle = {
            "spot": 100000.0,
            "gamma_flip": 98000.0,
            "total_signed_gex": 50.0,
            "support_levels": pd.DataFrame(),
            "resistance_levels": pd.DataFrame(),
            "perp_snapshot": {"last_funding_rate": 0.0001},
            "ibit_context": {"flow_state": "Neutral", "flow_copy": ""},
            "avwap_df": avwap_df,
            "sweeps": pd.DataFrame(),
            "oi_change_1h": 0.01,
        }

        cards = scanner._build_institutional_positioning_cards(bundle)

        self.assertEqual(len(cards), 5)
        tape_card = next(card for card in cards if card["title"] == "VWAP / Tape")
        self.assertEqual(tape_card["state"], "green")  # above VWAP, no bearish sweep

    def test_history_comparison_median_spans_days_not_snapshots(self):
        # 40 same-day snapshots at gex=1000 plus 30 older daily closes at
        # gex=100: a raw tail(30) median would read 1000; the daily-collapsed
        # median must blend the days instead.
        ts_today = pd.Timestamp("2026-03-01 00:00:00")
        rows = []
        for day in range(30):
            rows.append({"ts": ts_today - pd.Timedelta(days=30 - day), "total_abs_gex": 100.0, "total_oi": 1.0, "front_iv": 50.0, "front_rr": 0.0})
        for snap in range(40):
            rows.append({"ts": ts_today + pd.Timedelta(minutes=5 * snap), "total_abs_gex": 1000.0, "total_oi": 1.0, "front_iv": 50.0, "front_rr": 0.0})
        history = pd.DataFrame(rows)
        current = {"ts": history["ts"].max(), "total_abs_gex": 100.0, "total_oi": 1.0, "front_iv": 50.0, "front_rr": 0.0}

        comparison = scanner._history_comparison(history, current)

        # Daily median over ~30 days is 100 (29 old days at 100, 1 day at
        # 1000), so current 100 shows ~0% -- not the -90% a snapshot-window
        # median would produce.
        self.assertAlmostEqual(comparison["gex_vs_30d_median"], 0.0, places=6)

    def test_avwap_fetch_plan_reaches_anchor(self):
        interval, limit = scanner._avwap_fetch_plan("Monthly Open")
        self.assertEqual(interval, "15m")
        self.assertGreaterEqual(limit * 15, 31 * 24 * 60)  # >= 31 days
        interval, limit = scanner._avwap_fetch_plan("Weekly Open")
        self.assertEqual(interval, "5m")
        self.assertGreaterEqual(limit * 5, 8 * 24 * 60)  # >= 8 days

    def test_btc_perp_klines_paginates_past_1500(self):
        from perpscanner import data_binance

        bar_ms = 5 * 60 * 1000
        now_ms = scanner._epoch_ms_now()

        def fake_get_json(path, params=None, timeout=15):
            # Mirrors the Binance contract: bars with grid-aligned open
            # times <= endTime, newest last.
            params = params or {}
            chunk_limit = int(params.get("limit", 1500))
            end = int(params.get("endTime", now_ms - bar_ms))
            last_open = (end // bar_ms) * bar_ms
            rows = []
            for i in range(chunk_limit - 1, -1, -1):
                open_ms = last_open - i * bar_ms
                rows.append(
                    [open_ms, "1.0", "1.1", "0.9", "1.0", "10.0", open_ms + bar_ms - 1, "100.0", 5, "5.0", "50.0", "0"]
                )
            return rows

        original = data_binance._get_json
        data_binance._get_json = fake_get_json
        try:
            df = data_binance._fetch_binance_btc_perp_klines("5m", 2400)
        finally:
            data_binance._get_json = original

        self.assertEqual(len(df), 2400)
        self.assertTrue(df["ts"].is_monotonic_increasing)
        self.assertEqual(df["ts"].duplicated().sum(), 0)
        # Window must actually span ~2400 bars back, not 1500.
        span_minutes = (df["ts"].iloc[-1] - df["ts"].iloc[0]).total_seconds() / 60.0
        self.assertGreaterEqual(span_minutes, (2400 - 1) * 5)


class PremiumOiFactorTests(unittest.TestCase):
    def test_oi_context_separates_one_bar_change_from_window_trend(self):
        idx = pd.date_range("2026-01-01", periods=8, freq="1h")
        # OI unwinding all window, with a last-bar uptick: the 1-bar change
        # is positive while the multi-hour trend stays firmly negative.
        values = [1000.0, 950.0, 900.0, 850.0, 800.0, 750.0, 700.0, 710.0]
        df = pd.DataFrame({"oi_value": values}, index=idx)

        oi_value, oi_change, oi_trend = scanner._oi_context_from_frame(df)

        self.assertEqual(oi_value, 710.0)
        self.assertGreater(oi_change, 0.0)
        self.assertLess(oi_trend, 0.0)
        self.assertAlmostEqual(oi_trend, 710.0 / 1000.0 - 1.0)

    def test_oi_context_handles_empty_and_short_frames(self):
        self.assertEqual(scanner._oi_context_from_frame(None), (0.0, 0.0, 0.0))
        self.assertEqual(scanner._oi_context_from_frame(pd.DataFrame()), (0.0, 0.0, 0.0))
        one_row = pd.DataFrame({"oi_value": [100.0]})
        self.assertEqual(scanner._oi_context_from_frame(one_row), (0.0, 0.0, 0.0))

    def test_premium_snapshot_computes_basis_points(self):
        raw = [
            {"symbol": "AAAUSDT", "markPrice": "100.10", "indexPrice": "100.00"},
            {"symbol": "BBBUSDT", "markPrice": "99.90", "indexPrice": "100.00"},
            {"symbol": "BADUSDT", "markPrice": "1.0", "indexPrice": "0.0"},  # guarded
            "garbage",
        ]

        snapshot = scanner._premium_snapshot_from_raw(raw)

        self.assertAlmostEqual(snapshot["AAAUSDT"], 10.0, places=6)
        self.assertAlmostEqual(snapshot["BBBUSDT"], -10.0, places=6)
        self.assertNotIn("BADUSDT", snapshot)
        self.assertEqual(scanner._premium_snapshot_from_raw({"not": "a list"}), {})

    def test_premium_roc_uses_oldest_usable_snapshot(self):
        now = pd.Timestamp("2026-01-01 12:00:00")
        history = [
            (now - pd.Timedelta(minutes=30), {"AAAUSDT": 0.0}),
            (now - pd.Timedelta(minutes=1), {"AAAUSDT": 9.0}),  # too fresh to be a base
        ]
        snapshot = {"AAAUSDT": 5.0, "NEWUSDT": 3.0}

        roc = scanner._premium_roc_from_history(history, snapshot, now, min_age_s=240)

        self.assertAlmostEqual(roc["AAAUSDT"], 10.0, places=6)  # +5 bp over 0.5h
        self.assertEqual(roc["NEWUSDT"], 0.0)  # symbol absent from the base
        self.assertEqual(
            scanner._premium_roc_from_history([], snapshot, now), {"AAAUSDT": 0.0, "NEWUSDT": 0.0}
        )

    def test_fetch_premium_index_all_warms_roc_buffer(self):
        from perpscanner import data_binance

        original_get_json = data_binance._get_json
        original_history = list(data_binance._PREMIUM_HISTORY)
        data_binance._PREMIUM_HISTORY.clear()
        try:
            data_binance._get_json = lambda path, params=None, timeout=15: [
                {"symbol": "AAAUSDT", "markPrice": "100.10", "indexPrice": "100.00"}
            ]
            first = data_binance.fetch_premium_index_all()
            self.assertAlmostEqual(first["AAAUSDT"]["premium_bp"], 10.0, places=6)
            self.assertEqual(first["AAAUSDT"]["premium_roc_bp_h"], 0.0)  # cold buffer

            # Age the buffered snapshot past the minimum base age, then move
            # the premium: RoC must become measurable.
            ts, snap = data_binance._PREMIUM_HISTORY[0]
            data_binance._PREMIUM_HISTORY[0] = (ts - pd.Timedelta(hours=1), snap)
            data_binance._get_json = lambda path, params=None, timeout=15: [
                {"symbol": "AAAUSDT", "markPrice": "100.20", "indexPrice": "100.00"}
            ]
            second = data_binance.fetch_premium_index_all()
            self.assertAlmostEqual(second["AAAUSDT"]["premium_bp"], 20.0, places=6)
            self.assertAlmostEqual(second["AAAUSDT"]["premium_roc_bp_h"], 10.0, places=2)

            # Upstream failure degrades to {} instead of raising.
            def _boom(path, params=None, timeout=15):
                raise RuntimeError("down")

            data_binance._get_json = _boom
            self.assertEqual(data_binance.fetch_premium_index_all(), {})
        finally:
            data_binance._get_json = original_get_json
            data_binance._PREMIUM_HISTORY.clear()
            data_binance._PREMIUM_HISTORY.extend(original_history)


class ResearchLoopTests(unittest.TestCase):
    def _with_temp_db(self):
        from perpscanner import research

        tmpdir = tempfile.TemporaryDirectory()
        original = research.RESEARCH_DB_PATH
        research.RESEARCH_DB_PATH = Path(tmpdir.name) / "research.sqlite"
        return research, original, tmpdir

    def test_forward_return_frame_uses_later_snapshot(self):
        from perpscanner import research

        t0 = pd.Timestamp("2026-01-01 00:00:00")
        t1 = t0 + pd.Timedelta(hours=1)
        prices = pd.DataFrame(
            {
                "scan_ts": [t0, t0, t1, t1],
                "symbol": ["AAA", "BBB", "AAA", "BBB"],
                "price": [100.0, 100.0, 110.0, 90.0],
            }
        )

        fwd = research._forward_return_frame(prices, 1.0)
        fwd_t0 = fwd[fwd["scan_ts"] == t0].set_index("symbol")["fwd_ret"]

        self.assertAlmostEqual(fwd_t0["AAA"], 0.10)
        self.assertAlmostEqual(fwd_t0["BBB"], -0.10)
        # t1 snapshots have no later exit inside tolerance -> NaN
        self.assertTrue(fwd[fwd["scan_ts"] == t1]["fwd_ret"].isna().all())

    def test_log_scan_snapshot_throttles_and_survives_bad_input(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            df = pd.DataFrame({"symbol": ["AAA"], "price": [1.0], "momentum_score": [50.0]})

            first = research.log_scan_snapshot(df, None)
            second = research.log_scan_snapshot(df, None)

            self.assertEqual(first[research.METRIC_TABLE], 1)
            self.assertEqual(second[research.METRIC_TABLE], 0)  # throttled
            # Must never raise, whatever it is fed.
            research.log_scan_snapshot(None, pd.DataFrame())
            counts = research.snapshot_counts()
            self.assertEqual(counts[research.METRIC_TABLE]["rows"], 1)
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()

    def _seed_synthetic_store(self, research):
        conn = research._connect()
        try:
            t0 = pd.Timestamp("2026-01-01 00:00:00")
            t1 = t0 + pd.Timedelta(hours=1)
            symbols = [f"S{i:02d}USDT" for i in range(12)]
            rows = []
            for i, sym in enumerate(symbols):
                rows.append({"scan_ts": t0.isoformat(), "symbol": sym, "price": 100.0, "momentum_score": float(i)})
                rows.append(
                    {"scan_ts": t1.isoformat(), "symbol": sym, "price": 100.0 * (1.0 + i / 100.0), "momentum_score": float(i)}
                )
            pd.DataFrame(rows).to_sql(research.METRIC_TABLE, conn, if_exists="append", index=False)
            ltf_rows = []
            for i, sym in enumerate(symbols):
                ltf_rows.append(
                    {
                        "scan_ts": t0.isoformat(),
                        "symbol": sym,
                        "price": 100.0,
                        "trigger_fresh": 1 if i == 11 else 0,
                        "trigger_direction": "Long" if i == 11 else "None",
                        "ltf_ignition_score": float(i),
                    }
                )
                ltf_rows.append(
                    {
                        "scan_ts": t1.isoformat(),
                        "symbol": sym,
                        "price": 100.0 * (1.0 + i / 100.0),
                        "trigger_fresh": 0,
                        "trigger_direction": "None",
                        "ltf_ignition_score": float(i),
                    }
                )
            pd.DataFrame(ltf_rows).to_sql(research.LTF_TABLE, conn, if_exists="append", index=False)
            conn.commit()
        finally:
            conn.close()

    def test_rank_ic_detects_perfectly_predictive_factor(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            self._seed_synthetic_store(research)
            ic = research.rank_ic_report(horizons=(1.0,))

            self.assertFalse(ic.empty)
            row = ic[ic["factor"] == "momentum_score"].iloc[0]
            self.assertAlmostEqual(row["mean_ic"], 1.0, places=6)
            self.assertEqual(row["cross_sections"], 1)
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()

    def test_log_scan_snapshot_migrates_old_table_schema(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            # Simulate a store created before the conviction columns existed.
            conn = research._connect()
            try:
                pd.DataFrame(
                    {"scan_ts": ["2026-01-01T00:00:00"], "symbol": ["OLDUSDT"], "price": [1.0]}
                ).to_sql(research.METRIC_TABLE, conn, if_exists="append", index=False)
                conn.commit()
            finally:
                conn.close()

            df = pd.DataFrame({"symbol": ["AAA"], "price": [1.0], "momentum_score": [50.0]})
            written = research.log_scan_snapshot(df, None)

            self.assertEqual(written[research.METRIC_TABLE], 1)
            stored = research._load_table(research.METRIC_TABLE)
            self.assertEqual(len(stored), 2)
            self.assertIn("momentum_score", stored.columns)
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()

    def _seed_component_store(self, research, cross_sections=35, symbols=8):
        """Store where comp_volume ranks directional returns perfectly and
        comp_oi ranks them perfectly backwards, under a Long veto."""
        conn = research._connect()
        try:
            rows = []
            t0 = pd.Timestamp("2026-01-01 00:00:00")
            for t in range(cross_sections + 1):
                ts = (t0 + pd.Timedelta(hours=t)).isoformat()
                for i in range(symbols):
                    # Price path: each symbol gains i% per hour -> forward
                    # return ranking always matches the symbol index.
                    rows.append(
                        {
                            "scan_ts": ts,
                            "symbol": f"S{i:02d}USDT",
                            "price": 100.0 * (1.0 + i / 100.0) ** t,
                            "veto_side": "Long",
                            "comp_expansion": (i % 4) / 4.0,
                            "comp_volume": i / float(symbols),
                            "comp_oi": (symbols - 1 - i) / float(symbols),
                            "comp_taker_net": i / float(symbols),
                            "comp_basis_net": (i % 3) / 3.0,
                            "trigger_fresh": 0,
                            "trigger_direction": "None",
                        }
                    )
            pd.DataFrame(rows).to_sql(research.LTF_TABLE, conn, if_exists="append", index=False)
            conn.commit()
        finally:
            conn.close()

    def test_confluence_component_ic_ranks_predictive_component(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            self._seed_component_store(research)
            ic = research.confluence_component_ic(horizons=(1.0,))

            self.assertFalse(ic.empty)
            by_comp = ic.set_index("component")
            self.assertAlmostEqual(by_comp.loc["volume", "mean_ic"], 1.0, places=6)
            self.assertAlmostEqual(by_comp.loc["oi", "mean_ic"], -1.0, places=6)
            self.assertGreaterEqual(by_comp.loc["volume", "cross_sections"], 30)
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()

    def test_suggest_confluence_weights_floors_negative_ic(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            self._seed_component_store(research)
            ic = research.confluence_component_ic(horizons=(1.0,))
            suggestion = research.suggest_confluence_weights(ic)

            self.assertTrue(suggestion["ready"], suggestion["reason"])
            weights = suggestion["weights"]
            self.assertEqual(set(weights), set(scanner.CONFLUENCE_WEIGHTS))
            self.assertAlmostEqual(sum(weights.values()), 1.0, places=2)
            self.assertEqual(weights["oi"], 0.0)  # negative IC floored out
            self.assertGreater(weights["volume"], weights["basis"])
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()

    def test_suggest_confluence_weights_refuses_thin_data(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            self._seed_component_store(research, cross_sections=5)
            ic = research.confluence_component_ic(horizons=(1.0,))
            suggestion = research.suggest_confluence_weights(ic)

            self.assertFalse(suggestion["ready"])
            self.assertEqual(suggestion["weights"], {k: float(v) for k, v in scanner.CONFLUENCE_WEIGHTS.items()})
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()

    def test_trigger_event_study_rewards_winning_long_trigger(self):
        research, original, tmpdir = self._with_temp_db()
        try:
            self._seed_synthetic_store(research)
            ev = research.trigger_event_study(horizons=(1.0,))

            self.assertFalse(ev.empty)
            row = ev.iloc[0]
            self.assertEqual(row["triggers"], 1)
            # Best symbol (+11%) vs cross-section mean (+5.5%): positive excess.
            self.assertAlmostEqual(row["mean_signed_ret"], 0.11, places=6)
            self.assertGreater(row["mean_excess_ret"], 0.0)
            self.assertEqual(row["hit_rate"], 1.0)
        finally:
            research.RESEARCH_DB_PATH = original
            tmpdir.cleanup()


class WSFeedTests(unittest.TestCase):
    def _closed_kline(self, symbol="AAAUSDT", interval="5m", open_ms=1_700_000_000_000, close_price=101.0, closed=True):
        return {
            "data": {
                "e": "kline",
                "k": {
                    "t": open_ms,
                    "s": symbol,
                    "i": interval,
                    "o": "100.0",
                    "h": "102.0",
                    "l": "99.0",
                    "c": str(close_price),
                    "v": "10.0",
                    "q": "1000.0",
                    "n": 25,
                    "Q": "600.0",
                    "x": closed,
                },
            }
        }

    def test_open_bar_is_ignored_closed_bar_lands(self):
        feed = scanner.WSKlineFeed(["AAAUSDT"], ["5m"])

        self.assertFalse(feed.handle_kline_payload(self._closed_kline(closed=False)))
        self.assertTrue(feed.handle_kline_payload(self._closed_kline(closed=True)))
        # duplicate close event (reconnect) replaces, not appends
        self.assertTrue(feed.handle_kline_payload(self._closed_kline(closed=True, close_price=101.5)))
        with feed._lock:
            buf = list(feed._buffers[("AAAUSDT", "5m")])
        self.assertEqual(len(buf), 1)
        self.assertEqual(buf[0]["close"], 101.5)

    def test_frame_requires_seed_depth_and_freshness(self):
        feed = scanner.WSKlineFeed(["AAAUSDT"], ["5m"])
        # One ws bar alone is far below EMA_SLOW + 5 -> unusable
        feed.handle_kline_payload(self._closed_kline())
        self.assertIsNone(feed.frame("AAAUSDT", "5m"))

        # Seed with a fresh REST-style frame -> usable, ws bar merged on top
        now = scanner._utc_now_naive().floor("5min")
        idx = pd.date_range(end=now - pd.Timedelta(minutes=5), periods=70, freq="5min")
        seed_df = pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "vol": 10.0,
                "quote_vol": 1000.0,
                "trades": 20.0,
                "tb_quote": 500.0,
            },
            index=idx,
        )
        feed.seed("AAAUSDT", "5m", seed_df)
        recent_open_ms = int(now.value // 10**6) - 5 * 60 * 1000
        feed.handle_kline_payload(self._closed_kline(open_ms=recent_open_ms, close_price=105.0))

        frame = feed.frame("AAAUSDT", "5m")

        self.assertIsNotNone(frame)
        self.assertListEqual(list(frame.columns), ["open", "high", "low", "close", "vol", "quote_vol", "trades", "tb_quote"])
        self.assertEqual(float(frame["close"].iloc[-1]), 105.0)
        self.assertGreaterEqual(len(frame), 70)

    def test_frame_goes_stale_without_new_bars(self):
        feed = scanner.WSKlineFeed(["AAAUSDT"], ["5m"])
        stale_end = scanner._utc_now_naive() - pd.Timedelta(hours=3)
        idx = pd.date_range(end=stale_end, periods=70, freq="5min")
        seed_df = pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "vol": 10.0,
                "quote_vol": 1000.0,
                "trades": 20.0,
                "tb_quote": 500.0,
            },
            index=idx,
        )
        feed.seed("AAAUSDT", "5m", seed_df)

        self.assertIsNone(feed.frame("AAAUSDT", "5m"))


class HtfRobustnessTests(unittest.TestCase):
    @staticmethod
    def _daily_frame(n=60, price=100.0, vol=1000.0):
        idx = pd.date_range("2026-05-01", periods=n, freq="D")
        close = pd.Series(price, index=idx) * (1 + pd.Series(range(n), index=idx) * 0.001)
        return pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "vol": vol,
                "quote_vol": vol * price,
                "trades": 100.0,
                "tb_quote": vol * price / 2,
            },
            index=idx,
        )

    def test_daily_swing_context_survives_nan_latest_volume(self):
        df = self._daily_frame()
        df.iloc[-1, df.columns.get_loc("quote_vol")] = float("nan")

        result = scanner._daily_swing_context(df, pd.DataFrame(), None)

        for key in ("daily_long_score", "daily_short_score", "daily_structure_score"):
            value = float(result[key])
            self.assertEqual(value, value, f"{key} is NaN")  # NaN != NaN
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)

    def test_range_context_zero_atr_reports_zero_width(self):
        df = self._daily_frame(120)

        high, low, position, width_atr = scanner._range_context(df, 72, 0.0)

        self.assertEqual(width_atr, 0.0)
        self.assertGreaterEqual(position, 0.0)
        self.assertLessEqual(position, 1.0)

    def test_spot_flow_summary_tolerates_non_dict_etf_summary(self):
        df = scanner._prepare_bubble_frame(
            pd.DataFrame(
                {
                    "ts": pd.date_range("2026-07-01", periods=40, freq="h"),
                    "close": 60000.0,
                    "quote_volume": 1e6,
                    "source": "test",
                }
            ),
            30,
        )

        result = scanner._spot_flow_summary(df, pd.DataFrame(), {"summary": "error text"})

        self.assertIn("state", result)


class EtfTapeClvTests(unittest.TestCase):
    """The ETF tape proxy must grade by close location in the day's range,
    not by sign(return): all four BTC ETFs share BTC's daily direction, so
    a sign-based ratio is pinned at +/-100% every single session."""

    @staticmethod
    def _rows(closes, span=0.02, pin=None):
        rows = []
        for i, close in enumerate(closes):
            high = close * (1 + span)
            low = close * (1 - span)
            if pin == "high":
                close = high
            elif pin == "low":
                close = low
            rows.append(
                {
                    "date": f"2026-06-{i + 1:02d}",
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "adjusted_close": close,
                    "volume": 1_000_000.0,
                }
            )
        return rows

    def _tape(self, payload_by_ticker):
        import os as _os

        from perpscanner import data_equities

        original_get = data_equities._get_json_url
        original_token = _os.environ.get("EODHD_API_TOKEN")
        _os.environ["EODHD_API_TOKEN"] = "test-token"

        def fake(url, params=None, timeout=None, **kwargs):
            ticker = url.rsplit("/", 1)[-1].split(".")[0]
            return payload_by_ticker.get(ticker, [])

        data_equities._get_json_url = fake
        try:
            return data_equities._fetch_btc_etf_tape(30)
        finally:
            data_equities._get_json_url = original_get
            if original_token is None:
                _os.environ.pop("EODHD_API_TOKEN", None)
            else:
                _os.environ["EODHD_API_TOKEN"] = original_token

    def test_all_red_day_is_graded_not_pinned(self):
        closes = [60.0 * (0.99**i) for i in range(20)]
        payload = {ticker: self._rows(closes) for ticker in ("IBIT", "FBTC", "ARKB", "BITB")}

        ratio = self._tape(payload)["summary"]["latest_proxy_ratio"]

        self.assertLess(abs(ratio), 0.999)

    def test_close_at_low_is_strongly_negative(self):
        ratio = self._tape({"IBIT": self._rows([60.0] * 20, pin="low")})["summary"]["latest_proxy_ratio"]

        self.assertLess(ratio, -0.9)

    def test_close_at_high_is_strongly_positive(self):
        ratio = self._tape({"IBIT": self._rows([60.0] * 20, pin="high")})["summary"]["latest_proxy_ratio"]

        self.assertGreater(ratio, 0.9)

    def test_missing_high_low_falls_back_to_return_sign(self):
        closes = [60.0 * (0.99**i) for i in range(20)]
        rows = [
            {key: row[key] for key in ("date", "close", "adjusted_close", "volume")}
            for row in self._rows(closes)
        ]

        ratio = self._tape({"IBIT": rows})["summary"]["latest_proxy_ratio"]

        self.assertEqual(ratio, -1.0)


if __name__ == "__main__":
    unittest.main()
