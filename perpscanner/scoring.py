"""Cross-sectional metric building and ranking."""

import numpy as np
import pandas as pd
import streamlit as st

from .config import (
    BTC_SYMBOL,
    CACHE_TTL,
    EMA_FAST,
    EMA_MID,
    EMA_SLOW,
    ETH_SYMBOL,
    HTF_ALPHA_WEIGHTS,
    HTF_MOMENTUM_SCORE_WEIGHTS,
    HTF_RS_WEIGHTS,
    HTF_SETUP_OVEREXTENSION_PENALTY,
    HTF_TREND_BLEND_WEIGHTS,
    LTF_ALPHA_WEIGHTS,
    LTF_CACHE_TTL,
    LTF_INTERVALS,
    LTF_RS_WEIGHTS,
    MAX_BEST_SETUP_TRIGGER_BARS,
    MIN_TF_ALIGNMENT,
    MOMENTUM_SCORE_WEIGHTS,
    OVEREXTENSION_WEIGHTS,
    SETUP_OVEREXTENSION_PENALTY,
    TREND_BLEND_WEIGHTS,
    VOLUME_BLEND_WEIGHTS,
    VOL_ADJUSTED_WEIGHTS,
    VWAP_FAST,
    VWAP_HTF,
    VWAP_SLOW,
    WS_LTF_ENABLED,
)
from .indicators import (
    _aligned_return_frame,
    _alpha_from_beta,
    _atr_series,
    _compression_score,
    _ema,
    _estimate_beta,
    _funding_quality_score,
    _funding_trend_quality_score,
    _percentile_score,
    _range_context,
    _return_n,
    _rolling_percentile,
    _score_from_threshold,
    _tanh_scale,
    _vol_adjusted_return,
    _volume_ratio,
    _volume_zscore,
    _vwap_zscore,
)
from .data_binance import (
    fetch_htf_daily_contexts,
    fetch_ltf_spot_contexts,
    fetch_ltf_symbol_contexts,
    fetch_symbol_contexts,
    fetch_ticker_stats,
)
from .features_ltf import _direction_from_state, _ltf_interval_metrics
from .features_htf import _btc_daily_regime, _daily_swing_context
from .regime import thresholds_for_regime


def _candidate_symbols(
    symbols: tuple[str, ...],
    ticker_stats: dict[str, dict[str, float]],
    min_quote_volume: float,
    min_trades: float,
) -> tuple[str, ...]:
    selected = [BTC_SYMBOL]
    for symbol in symbols:
        if symbol == BTC_SYMBOL:
            continue
        stats = ticker_stats.get(symbol, {})
        if stats.get("quote_volume_24h", 0.0) < min_quote_volume:
            continue
        if stats.get("trades_24h", 0.0) < min_trades:
            continue
        selected.append(symbol)
    return tuple(sorted(set(selected)))
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def build_metrics(
    symbols: tuple[str, ...],
    min_quote_volume: float,
    min_trades: float,
    min_oi_value: float,
) -> pd.DataFrame:
    ticker_stats = fetch_ticker_stats()
    candidates = _candidate_symbols(symbols, ticker_stats, min_quote_volume, min_trades)
    contexts = fetch_symbol_contexts(candidates)
    daily_contexts = fetch_htf_daily_contexts(candidates)
    btc_context = contexts.get(BTC_SYMBOL)
    if not btc_context:
        return pd.DataFrame()
    btc_daily_context = daily_contexts.get(BTC_SYMBOL, {})
    btc_regime = _btc_daily_regime(btc_daily_context.get("daily") if isinstance(btc_daily_context, dict) else None)

    btc_close = btc_context["klines"]["close"]  # type: ignore[index]
    btc_ret_1h = _return_n(btc_close, 1)
    btc_ret_4h = _return_n(btc_close, 4)
    btc_ret_24h = _return_n(btc_close, 24)
    btc_ret_72h = _return_n(btc_close, 72)
    btc_ret_168h = _return_n(btc_close, 168)

    rows = []
    for symbol in candidates:
        if symbol == BTC_SYMBOL:
            continue
        context = contexts.get(symbol)
        if not context:
            continue

        df = context["klines"]
        oi_value = float(context["oi_value"])
        oi_change = float(context["oi_change"])
        funding_rate = float(context["funding_rate"])
        funding_cumulative_7d = float(context["funding_cumulative_7d"])
        funding_trend = float(context["funding_trend"])
        if oi_value < min_oi_value:
            continue
        daily_context = daily_contexts.get(symbol, {})
        daily_metrics = _daily_swing_context(
            daily_context.get("daily") if isinstance(daily_context, dict) else None,
            daily_context.get("daily_oi", pd.DataFrame()) if isinstance(daily_context, dict) else pd.DataFrame(),
            daily_context.get("spot_daily") if isinstance(daily_context, dict) else None,
        )

        close = df["close"]
        price = float(close.iloc[-1])
        ema20 = _ema(close, EMA_FAST)
        ema36 = _ema(close, EMA_MID)
        ema50 = _ema(close, EMA_SLOW)

        ret_1h = _return_n(close, 1)
        ret_4h = _return_n(close, 4)
        ret_24h = _return_n(close, 24)
        ret_72h = _return_n(close, 72)
        ret_168h = _return_n(close, 168)
        rs_1h = ret_1h - btc_ret_1h
        rs_4h = ret_4h - btc_ret_4h
        rs_24h = ret_24h - btc_ret_24h
        rs_72h = ret_72h - btc_ret_72h
        rs_168h = ret_168h - btc_ret_168h
        aligned_returns = _aligned_return_frame(close, btc_close)
        beta_by_lookback = {
            lookback: _estimate_beta(aligned_returns, lookback)
            for lookback in (72, 96, 168, 216)
        }
        alpha_1h = _alpha_from_beta(close, btc_close, 1, beta_by_lookback[72])
        alpha_4h = _alpha_from_beta(close, btc_close, 4, beta_by_lookback[72])
        alpha_24h = _alpha_from_beta(close, btc_close, 24, beta_by_lookback[96])
        alpha_72h = _alpha_from_beta(close, btc_close, 72, beta_by_lookback[168])
        alpha_168h = _alpha_from_beta(close, btc_close, 168, beta_by_lookback[216])
        vol_adj_4h = _vol_adjusted_return(close, 4)
        vol_adj_24h = _vol_adjusted_return(close, 24)
        vol_adj_72h = _vol_adjusted_return(close, 72)
        vol_adj_168h = _vol_adjusted_return(close, 168)

        volume_ratio_1h = _volume_ratio(df["quote_vol"])
        volume_z_1h = _volume_zscore(df["quote_vol"])
        z_fast = _vwap_zscore(df, VWAP_FAST)
        z_slow = _vwap_zscore(df, VWAP_SLOW)
        z_htf = _vwap_zscore(df, VWAP_HTF)
        atr_1h = _atr_series(df)
        htf_atr_value = float(atr_1h.iloc[-1]) if not atr_1h.empty and pd.notna(atr_1h.iloc[-1]) else 0.0
        htf_atr_percentile = float(_rolling_percentile(atr_1h).iloc[-1]) if not atr_1h.empty else 50.0
        if len(atr_1h) > 12 and float(atr_1h.iloc[-13]) > 1e-12:
            htf_atr_roc = float(atr_1h.iloc[-1] / atr_1h.iloc[-13] - 1.0)
        else:
            htf_atr_roc = 0.0
        htf_range_high, htf_range_low, htf_range_position, htf_range_width_atr = _range_context(df, 72, htf_atr_value)
        if price > htf_range_high and htf_range_high > 0:
            htf_breakout_distance_atr = (price - htf_range_high) / max(htf_atr_value, 1e-12)
        elif price < htf_range_low and htf_range_low > 0:
            htf_breakout_distance_atr = (price - htf_range_low) / max(htf_atr_value, 1e-12)
        else:
            htf_breakout_distance_atr = 0.0

        dist_ema20 = (price / ema20 - 1.0) if ema20 else 0.0
        dist_ema36 = (price / ema36 - 1.0) if ema36 else 0.0
        dist_ema50 = (price / ema50 - 1.0) if ema50 else 0.0
        ema_alignment = (
            float(price > ema20)
            + float(ema20 > ema36)
            + float(ema36 > ema50)
        ) / 3.0

        ltf_alpha_raw = (
            LTF_ALPHA_WEIGHTS["1h"] * alpha_1h
            + LTF_ALPHA_WEIGHTS["4h"] * alpha_4h
            + LTF_ALPHA_WEIGHTS["24h"] * alpha_24h
        )
        htf_alpha_raw = (
            HTF_ALPHA_WEIGHTS["24h"] * alpha_24h
            + HTF_ALPHA_WEIGHTS["72h"] * alpha_72h
            + HTF_ALPHA_WEIGHTS["168h"] * alpha_168h
        )
        vol_adjusted_raw = (
            VOL_ADJUSTED_WEIGHTS["24h"] * vol_adj_24h
            + VOL_ADJUSTED_WEIGHTS["72h"] * vol_adj_72h
            + VOL_ADJUSTED_WEIGHTS["168h"] * vol_adj_168h
        )
        rs_raw = LTF_RS_WEIGHTS["1h"] * rs_1h + LTF_RS_WEIGHTS["4h"] * rs_4h + LTF_RS_WEIGHTS["24h"] * rs_24h
        htf_rs_raw = (
            HTF_RS_WEIGHTS["24h"] * rs_24h
            + HTF_RS_WEIGHTS["72h"] * rs_72h
            + HTF_RS_WEIGHTS["168h"] * rs_168h
        )
        volume_raw = (
            VOLUME_BLEND_WEIGHTS["ratio"] * _tanh_scale(volume_ratio_1h - 1.0, 1.25)
            + VOLUME_BLEND_WEIGHTS["zscore"] * _tanh_scale(volume_z_1h, 0.40)
        )
        trend_raw = (
            TREND_BLEND_WEIGHTS["ema20"] * _tanh_scale(dist_ema20, 35.0)
            + TREND_BLEND_WEIGHTS["ema36"] * _tanh_scale(dist_ema36, 30.0)
            + TREND_BLEND_WEIGHTS["ema50"] * _tanh_scale(dist_ema50, 25.0)
            + TREND_BLEND_WEIGHTS["alignment"] * ema_alignment
            + TREND_BLEND_WEIGHTS["vwap"] * _tanh_scale(max(z_fast, 0.0), 0.60)
        )
        htf_trend_raw = (
            HTF_TREND_BLEND_WEIGHTS["ema36"] * _tanh_scale(dist_ema36, 30.0)
            + HTF_TREND_BLEND_WEIGHTS["ema50"] * _tanh_scale(dist_ema50, 25.0)
            + HTF_TREND_BLEND_WEIGHTS["alignment"] * ema_alignment
            + HTF_TREND_BLEND_WEIGHTS["vwap"] * _tanh_scale(max(z_htf, 0.0), 0.35)
        )
        oi_raw = _tanh_scale(oi_change, 8.0)
        overextension_raw = (
            OVEREXTENSION_WEIGHTS["vwap"] * max(z_slow, 0.0)
            + OVEREXTENSION_WEIGHTS["ema20"] * max(dist_ema20, 0.0) * 40.0
            + OVEREXTENSION_WEIGHTS["ema36"] * max(dist_ema36, 0.0) * 35.0
            + OVEREXTENSION_WEIGHTS["ret_1h"] * max(ret_1h, 0.0) * 30.0
        )
        htf_atr_compression_score = _compression_score(htf_atr_percentile, volume_z_1h, oi_raw)
        htf_atr_expansion_score = _score_from_threshold(max(htf_atr_roc, 0.0), 0.18, 75.0)
        htf_range_break_score = _score_from_threshold(abs(htf_breakout_distance_atr), 0.65, 80.0)
        htf_long_structure_score = float(
            np.clip(
                40.0 * float(price > ema36)
                + 25.0 * float(ema36 > ema50)
                + 20.0 * max(z_htf, 0.0)
                + 15.0 * max(htf_range_position - 0.50, 0.0) * 2.0,
                0.0,
                100.0,
            )
        )
        htf_short_structure_score = float(
            np.clip(
                40.0 * float(price < ema36)
                + 25.0 * float(ema36 < ema50)
                + 20.0 * max(-z_htf, 0.0)
                + 15.0 * max(0.50 - htf_range_position, 0.0) * 2.0,
                0.0,
                100.0,
            )
        )
        htf_long_rs_score = float(np.clip((rs_24h * 500.0) + (rs_72h * 350.0) + (rs_168h * 250.0), 0.0, 100.0))
        htf_short_rs_score = float(np.clip((-rs_24h * 500.0) + (-rs_72h * 350.0) + (-rs_168h * 250.0), 0.0, 100.0))
        htf_long_expansion_score = (
            0.20 * htf_atr_compression_score
            + 0.20 * htf_atr_expansion_score
            + 0.20 * volume_raw * 100.0
            + 0.15 * max(oi_raw, 0.0) * 100.0
            + 0.15 * htf_long_structure_score
            + 0.10 * htf_long_rs_score
        )
        htf_short_expansion_score = (
            0.20 * htf_atr_compression_score
            + 0.20 * htf_atr_expansion_score
            + 0.20 * volume_raw * 100.0
            + 0.15 * max(oi_raw, 0.0) * 100.0
            + 0.15 * htf_short_structure_score
            + 0.10 * htf_short_rs_score
        )
        daily_long_confirmed = bool(daily_metrics.get("daily_long_confirmed", False))
        daily_short_confirmed = bool(daily_metrics.get("daily_short_confirmed", False))
        daily_oi_persistence_days = int(daily_metrics.get("daily_oi_persistence_days", 0))
        daily_volume_persistence_days = int(daily_metrics.get("daily_volume_persistence_days", 0))
        daily_long_quality = float(daily_metrics.get("daily_long_score", 0.0))
        daily_short_quality = float(daily_metrics.get("daily_short_score", 0.0))
        htf_long_expansion_score = float(
            np.clip(
                (
                    0.72 * htf_long_expansion_score
                    + 0.28 * daily_long_quality
                    + 4.0 * min(daily_oi_persistence_days, 3)
                    + 3.0 * min(daily_volume_persistence_days, 3)
                )
                * float(btc_regime["btc_long_multiplier"]),
                0.0,
                100.0,
            )
        )
        htf_short_expansion_score = float(
            np.clip(
                (
                    0.72 * htf_short_expansion_score
                    + 0.28 * daily_short_quality
                    + 4.0 * min(daily_oi_persistence_days, 3)
                    + 3.0 * min(daily_volume_persistence_days, 3)
                )
                * float(btc_regime["btc_short_multiplier"]),
                0.0,
                100.0,
            )
        )
        if htf_long_expansion_score >= htf_short_expansion_score and htf_long_expansion_score >= 45.0:
            htf_expansion_direction = "Long expansion" if daily_long_confirmed else "Long watch"
            htf_expansion_score = htf_long_expansion_score if daily_long_confirmed else htf_long_expansion_score * 0.82
        elif htf_short_expansion_score > htf_long_expansion_score and htf_short_expansion_score >= 45.0:
            htf_expansion_direction = "Short expansion" if daily_short_confirmed else "Short watch"
            htf_expansion_score = htf_short_expansion_score if daily_short_confirmed else htf_short_expansion_score * 0.82
        elif htf_atr_percentile < 25.0:
            htf_expansion_direction = "Compression"
            htf_expansion_score = htf_atr_compression_score * 0.60
        else:
            htf_expansion_direction = "Neutral"
            htf_expansion_score = max(htf_long_expansion_score, htf_short_expansion_score)

        stats = ticker_stats.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "price": price,
                "quote_volume_24h": float(stats.get("quote_volume_24h", 0.0)),
                "trades_24h": float(stats.get("trades_24h", 0.0)),
                "funding_rate": funding_rate,
                "funding_cumulative_7d": funding_cumulative_7d,
                "funding_trend": funding_trend,
                "oi_value": oi_value,
                "oi_change_1h": oi_change,
                "ret_1h": ret_1h,
                "ret_4h": ret_4h,
                "ret_24h": ret_24h,
                "ret_72h": ret_72h,
                "ret_168h": ret_168h,
                "rs_1h": rs_1h,
                "rs_4h": rs_4h,
                "rs_24h": rs_24h,
                "rs_72h": rs_72h,
                "rs_168h": rs_168h,
                "alpha_1h": alpha_1h,
                "alpha_4h": alpha_4h,
                "alpha_24h": alpha_24h,
                "alpha_72h": alpha_72h,
                "alpha_168h": alpha_168h,
                "vol_adj_4h": vol_adj_4h,
                "vol_adj_24h": vol_adj_24h,
                "vol_adj_72h": vol_adj_72h,
                "vol_adj_168h": vol_adj_168h,
                "volume_ratio_1h": volume_ratio_1h,
                "volume_z_1h": volume_z_1h,
                "ema20": ema20,
                "ema36": ema36,
                "ema50": ema50,
                "ema_alignment": ema_alignment,
                "dist_ema20": dist_ema20,
                "dist_ema36": dist_ema36,
                "dist_ema50": dist_ema50,
                "z_fast": z_fast,
                "z_slow": z_slow,
                "z_htf": z_htf,
                "htf_atr_value": htf_atr_value,
                "htf_atr_percentile": htf_atr_percentile,
                "htf_atr_compression_score": htf_atr_compression_score,
                "htf_atr_roc": htf_atr_roc,
                "htf_atr_expansion_score": htf_atr_expansion_score,
                "htf_breakout_distance_atr": htf_breakout_distance_atr,
                "htf_range_position": htf_range_position,
                "htf_range_width_atr": htf_range_width_atr,
                "htf_expansion_direction": htf_expansion_direction,
                "htf_expansion_score": float(np.clip(htf_expansion_score, 0.0, 100.0)),
                "htf_long_expansion_score": float(np.clip(htf_long_expansion_score, 0.0, 100.0)),
                "htf_short_expansion_score": float(np.clip(htf_short_expansion_score, 0.0, 100.0)),
                "btc_daily_regime": str(btc_regime["btc_daily_regime"]),
                "btc_daily_regime_score": float(btc_regime["btc_daily_regime_score"]),
                **daily_metrics,
                "ltf_alpha_raw": ltf_alpha_raw,
                "htf_alpha_raw": htf_alpha_raw,
                "vol_adjusted_raw": vol_adjusted_raw,
                "rs_raw": rs_raw,
                "htf_rs_raw": htf_rs_raw,
                "volume_raw": volume_raw,
                "trend_raw": trend_raw,
                "htf_trend_raw": htf_trend_raw,
                "oi_raw": oi_raw,
                "overextension_raw": overextension_raw,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    funding_std = float(out["funding_rate"].std())
    out["funding_z"] = (out["funding_rate"] - out["funding_rate"].mean()) / (funding_std + 1e-10)
    funding_cumulative_std = float(out["funding_cumulative_7d"].std())
    out["funding_cumulative_z"] = (
        out["funding_cumulative_7d"] - out["funding_cumulative_7d"].mean()
    ) / (funding_cumulative_std + 1e-10)
    funding_trend_std = float(out["funding_trend"].std())
    out["funding_trend_z"] = (out["funding_trend"] - out["funding_trend"].mean()) / (funding_trend_std + 1e-10)
    out["alpha_score"] = _percentile_score(out["ltf_alpha_raw"])
    out["htf_alpha_score"] = _percentile_score(out["htf_alpha_raw"])
    out["vol_adjusted_score"] = _percentile_score(out["vol_adjusted_raw"])
    out["relative_strength_score"] = _percentile_score(out["rs_raw"])
    out["htf_relative_strength_score"] = _percentile_score(out["htf_rs_raw"])
    out["volume_score"] = _percentile_score(out["volume_raw"])
    out["trend_score"] = _percentile_score(out["trend_raw"])
    out["htf_trend_score"] = _percentile_score(out["htf_trend_raw"])
    out["oi_score"] = _percentile_score(out["oi_raw"])
    out["funding_quality_score"] = _funding_quality_score(out["funding_z"], out["funding_rate"])
    out["funding_trend_quality_score"] = _funding_trend_quality_score(
        out["funding_cumulative_z"], out["funding_trend_z"]
    )
    out["momentum_score"] = (
        MOMENTUM_SCORE_WEIGHTS["alpha"] * out["alpha_score"]
        + MOMENTUM_SCORE_WEIGHTS["rs"] * out["relative_strength_score"]
        + MOMENTUM_SCORE_WEIGHTS["volume"] * out["volume_score"]
        + MOMENTUM_SCORE_WEIGHTS["trend"] * out["trend_score"]
        + MOMENTUM_SCORE_WEIGHTS["oi"] * out["oi_score"]
        + MOMENTUM_SCORE_WEIGHTS["funding"] * out["funding_quality_score"]
    )
    out["htf_momentum_score"] = (
        HTF_MOMENTUM_SCORE_WEIGHTS["alpha"] * out["htf_alpha_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["vol_adjusted"] * out["vol_adjusted_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["rs"] * out["htf_relative_strength_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["trend"] * out["htf_trend_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["oi"] * out["oi_score"]
        + HTF_MOMENTUM_SCORE_WEIGHTS["funding"] * out["funding_trend_quality_score"]
    )
    out["overextension_score"] = _percentile_score(out["overextension_raw"])
    out["setup_score"] = (
        out["momentum_score"] - SETUP_OVEREXTENSION_PENALTY * out["overextension_score"]
    ).clip(lower=0.0)
    out["htf_setup_score"] = (
        out["htf_momentum_score"] - HTF_SETUP_OVEREXTENSION_PENALTY * out["overextension_score"]
    ).clip(lower=0.0)
    return out.sort_values(["momentum_score", "setup_score"], ascending=False).reset_index(drop=True)
@st.cache_data(ttl=LTF_CACHE_TTL, show_spinner=False)
def build_ltf_regime_metrics(
    symbols: tuple[str, ...],
    min_quote_volume: float,
    min_trades: float,
    min_oi_value: float,
    use_ws: bool = WS_LTF_ENABLED,
) -> pd.DataFrame:
    ticker_stats = fetch_ticker_stats()
    candidates = _candidate_symbols(symbols, ticker_stats, min_quote_volume, min_trades)
    context_symbols = tuple(sorted(set(candidates + (ETH_SYMBOL,))))
    contexts = fetch_ltf_symbol_contexts(context_symbols, use_ws)
    spot_contexts = fetch_ltf_spot_contexts(candidates)
    btc_context = contexts.get(BTC_SYMBOL)
    eth_context = contexts.get(ETH_SYMBOL)
    if not btc_context:
        return pd.DataFrame()

    # Scale the ignition gates with the BTC daily regime: fixed z-score
    # thresholds fire too easily in hot tape and too rarely in quiet tape.
    btc_daily_context = fetch_htf_daily_contexts((BTC_SYMBOL,)).get(BTC_SYMBOL, {})
    btc_regime = _btc_daily_regime(
        btc_daily_context.get("daily") if isinstance(btc_daily_context, dict) else None
    )
    thresholds = thresholds_for_regime(btc_regime)

    rows: list[dict[str, object]] = []
    btc_klines = btc_context.get("klines", {})
    eth_klines = eth_context.get("klines", {}) if eth_context else {}

    for symbol in candidates:
        if symbol == BTC_SYMBOL:
            continue
        context = contexts.get(symbol)
        if not context:
            continue

        klines = context.get("klines", {})
        oi_hist = context.get("oi_hist", {})
        spot_klines = spot_contexts.get(symbol, {}).get("klines", {})
        if not isinstance(klines, dict) or not isinstance(oi_hist, dict):
            continue
        if not isinstance(spot_klines, dict):
            spot_klines = {}

        latest_oi_values = [
            float(frame["oi_value"].iloc[-1])
            for frame in oi_hist.values()
            if isinstance(frame, pd.DataFrame) and not frame.empty
        ]
        oi_value = max(latest_oi_values) if latest_oi_values else 0.0
        if oi_value < min_oi_value:
            continue

        for interval in LTF_INTERVALS:
            df = klines.get(interval)
            btc_df = btc_klines.get(interval) if isinstance(btc_klines, dict) else None
            eth_df = eth_klines.get(interval) if isinstance(eth_klines, dict) else None
            spot_df = spot_klines.get(interval)
            oi_df = oi_hist.get(interval, pd.DataFrame())
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            if not isinstance(btc_df, pd.DataFrame) or btc_df.empty:
                continue
            if not isinstance(spot_df, pd.DataFrame):
                spot_df = None
            if not isinstance(oi_df, pd.DataFrame):
                oi_df = pd.DataFrame()
            row = _ltf_interval_metrics(symbol, interval, df, oi_df, btc_df, eth_df, spot_df, thresholds=thresholds)
            row["oi_value"] = oi_value
            row["quote_volume_24h"] = float(ticker_stats.get(symbol, {}).get("quote_volume_24h", 0.0))
            rows.append(row)

    interval_df = pd.DataFrame(rows)
    if interval_df.empty:
        return interval_df

    best_idx = interval_df.groupby("symbol")["ignition_score"].idxmax()
    out = interval_df.loc[best_idx].copy()
    out = out.rename(
        columns={
            "timeframe": "ignition_tf",
            "ignition_score": "ltf_ignition_score",
            "long_ignition_score": "ltf_long_ignition_score",
            "short_ignition_score": "ltf_short_ignition_score",
        }
    )

    for interval in LTF_INTERVALS:
        interval_slice = interval_df[interval_df["timeframe"] == interval].set_index("symbol")
        tf_scores = interval_slice["ignition_score"]
        out[f"ignition_score_{interval}"] = out["symbol"].map(tf_scores).fillna(0.0)
        out[f"direction_{interval}"] = out["symbol"].map(interval_slice["ignition_state"].map(_direction_from_state)).fillna("Neutral")
        out[f"fresh_{interval}"] = out["symbol"].map(interval_slice["trigger_fresh"]).fillna(False).astype(bool)
        out[f"bars_since_trigger_{interval}"] = out["symbol"].map(interval_slice["bars_since_trigger"]).fillna(999).astype(int)

    out["ltf_direction"] = out["ignition_state"].map(_direction_from_state)
    for direction in ("Long", "Short"):
        out[f"tf_{direction.lower()}_alignment"] = sum(
            (out.get(f"direction_{interval}", pd.Series("Neutral", index=out.index)) == direction).astype(int)
            for interval in LTF_INTERVALS
        )
    out["tf_alignment_score"] = np.select(
        [out["ltf_direction"].eq("Long"), out["ltf_direction"].eq("Short")],
        [out["tf_long_alignment"], out["tf_short_alignment"]],
        default=0,
    ).astype(int)
    out["tf_alignment_pass"] = out["tf_alignment_score"] >= MIN_TF_ALIGNMENT
    out["fresh_setup_pass"] = out["bars_since_trigger"] <= MAX_BEST_SETUP_TRIGGER_BARS
    return out.sort_values(["ltf_ignition_score", "volume_zscore", "oi_zscore"], ascending=False).reset_index(drop=True)
def _build_best_setups(ltf_df: pd.DataFrame, htf_df: pd.DataFrame) -> pd.DataFrame:
    if ltf_df.empty or htf_df.empty:
        return pd.DataFrame()

    required_cols = {"tf_alignment_pass", "fresh_setup_pass", "ltf_direction"}
    if required_cols.issubset(ltf_df.columns):
        ltf_df = ltf_df[
            ltf_df["tf_alignment_pass"]
            & ltf_df["fresh_setup_pass"]
            & ltf_df["ltf_direction"].isin(["Long", "Short"])
        ].copy()
        if ltf_df.empty:
            return ltf_df

    htf_cols = [
        "symbol",
        "htf_expansion_direction",
        "htf_expansion_score",
        "htf_momentum_score",
        "htf_setup_score",
        "htf_atr_percentile",
        "htf_atr_roc",
        "htf_breakout_distance_atr",
        "daily_structure_score",
        "daily_long_confirmed",
        "daily_short_confirmed",
        "daily_volume_ratio",
        "daily_volume_persistence_days",
        "daily_oi_persistence_days",
        "daily_swing_high",
        "daily_swing_low",
        "btc_daily_regime",
        "btc_daily_regime_score",
        "rs_24h",
        "rs_72h",
    ]
    merged = ltf_df.merge(htf_df[htf_cols], on="symbol", how="inner")
    if merged.empty:
        return merged

    long_aligned = (merged["ltf_direction"] == "Long") & merged["htf_expansion_direction"].str.startswith("Long")
    short_aligned = (merged["ltf_direction"] == "Short") & merged["htf_expansion_direction"].str.startswith("Short")
    long_aligned = long_aligned & merged["daily_long_confirmed"].fillna(False)
    short_aligned = short_aligned & merged["daily_short_confirmed"].fillna(False)
    compression_context = merged["htf_expansion_direction"].eq("Compression")
    merged["alignment_score"] = np.select(
        [long_aligned | short_aligned, compression_context],
        [100.0, 70.0],
        default=35.0,
    )
    merged["best_setup_score"] = (
        0.55 * merged["ltf_ignition_score"]
        + 0.30 * merged["htf_expansion_score"]
        + 0.15 * merged["alignment_score"]
    ).clip(0.0, 100.0)
    merged["best_setup_state"] = np.select(
        [long_aligned, short_aligned, compression_context],
        ["Long best setup", "Short best setup", "LTF trigger in HTF compression"],
        default="Mixed setup",
    )
    return merged.sort_values("best_setup_score", ascending=False).reset_index(drop=True)
