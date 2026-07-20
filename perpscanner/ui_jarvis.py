"""Rule-based narrator and positioning cards."""

import pandas as pd
import streamlit as st
from html import escape

from .utils import _format_gex_billions, _safe_float


def _jarvis_vwap_read(latest: pd.Series) -> str:
    close = _safe_float(latest.get("close"))
    avwap = _safe_float(latest.get("avwap"))
    band_1_up = _safe_float(latest.get("band_1_up"))
    band_1_dn = _safe_float(latest.get("band_1_dn"))
    band_2_up = _safe_float(latest.get("band_2_up"))
    band_2_dn = _safe_float(latest.get("band_2_dn"))
    if close >= band_2_up and band_2_up > 0:
        return "price is above +2 sigma from anchored VWAP, which is stretched and easier to fade than chase"
    if close <= band_2_dn and band_2_dn > 0:
        return "price is below -2 sigma from anchored VWAP, which is stretched to the downside and vulnerable to snapback"
    if close > band_1_up and band_1_up > 0:
        return "price is above anchored VWAP and leaning strong, but already outside the first deviation band"
    if close < band_1_dn and band_1_dn > 0:
        return "price is below anchored VWAP and trading weak beneath the first deviation band"
    if close >= avwap:
        return "price is holding above anchored VWAP, which keeps intraday structure constructive"
    return "price is below anchored VWAP, which keeps intraday structure softer unless VWAP is reclaimed"
def _jarvis_funding_read(funding_rate: float, oi_change_1h: float, funding_z: float = 0.0) -> str:
    # Hybrid label: regime z-score (unusual vs its own trailing 30d) AND
    # absolute level (economically large regardless of recent variance);
    # the displayed state is the WORSE of the two, so a dead-flat regime
    # can't make 0.005% look extreme and a hot regime can't hide a fat rate.
    abs_rate = abs(funding_rate)
    abs_level = 0 if abs_rate < 0.0001 else 1 if abs_rate < 0.0004 else 2
    z_level = 0 if abs(funding_z) < 1.0 else 1 if abs(funding_z) < 2.0 else 2
    level = max(abs_level, z_level)
    z_note = f" (z {funding_z:+.1f} vs its 30d regime)" if abs(funding_z) >= 1.0 else ""
    if level == 0:
        funding_text = "funding is calm"
    elif level == 1:
        funding_text = f"funding is elevated but not extreme{z_note}"
    else:
        funding_text = f"funding is stretched and crowding risk is higher{z_note}"

    if oi_change_1h > 0.01:
        oi_text = "OI is expanding, which means new exposure is joining the move"
    elif oi_change_1h < -0.01:
        oi_text = "OI is contracting, which points more toward de-risking or squeeze dynamics than clean new positioning"
    else:
        oi_text = "OI is roughly flat, so the tape is not showing a major fresh positioning surge right now"
    return f"{funding_text}; {oi_text}."
def _jarvis_iv_read(atm_iv: pd.DataFrame) -> str:
    if atm_iv.empty:
        return "ATM IV is unavailable from the current snapshot."
    first_iv = _safe_float(atm_iv["effective_iv"].iloc[0])
    last_iv = _safe_float(atm_iv["effective_iv"].iloc[-1])
    slope = last_iv - first_iv
    if slope > 2.0:
        shape = "the IV curve is upward sloping, which usually means the front is calmer than later expiries"
    elif slope < -2.0:
        shape = "the IV curve is front-loaded, which usually means near-term stress or event premium is heavier"
    else:
        shape = "the IV curve is fairly flat, so there is no dramatic near-term vol distortion"
    return f"Front ATM IV is {first_iv:.1f}; {shape}."
def _jarvis_sweep_read(sweeps: pd.DataFrame) -> str:
    if sweeps.empty:
        return "No recent 5-minute sweep candidates were detected by the current wick, volume, and OI rules."
    latest = sweeps.sort_values("ts").iloc[-1]
    direction = str(latest.get("direction", "Sweep"))
    broken_level = _safe_float(latest.get("broken_level"))
    vol_z = _safe_float(latest.get("volume_z"))
    taker_imb = _safe_float(latest.get("taker_imbalance"))
    oi_delta = _safe_float(latest.get("oi_value_change"))
    return (
        f"Latest sweep signal is <strong>{escape(direction)}</strong> through {broken_level:,.0f}, "
        f"with volume z-score {vol_z:.2f}, taker imbalance {taker_imb:+.2f}, and OI change {oi_delta:+.2%}."
    )
def _jarvis_level_line(levels: pd.DataFrame, field: str, label: str, metric_label: str) -> str:
    if levels.empty:
        return f"No nearby {label.lower()} level is available in the current GEX window."
    top = levels.iloc[0]
    strike = _safe_float(top.get("strike"))
    gex = _format_gex_billions(_safe_float(top.get(field)))
    distance = _safe_float(top.get("distance_pct"))
    oi = _safe_float(top.get("total_oi"))
    return f"Nearest high-probability {label.lower()} is {strike:,.0f} with {metric_label} {gex}, OI {oi:,.0f} BTC, and distance {distance:+.2f}%."
def _jarvis_level_list(levels: pd.DataFrame, field: str, label: str) -> str:
    if levels.empty:
        return f"<li>No nearby {escape(label.lower())} levels are available in the current GEX window.</li>"
    items = []
    for _, row in levels.head(3).iterrows():
        strike = _safe_float(row.get("strike"))
        gex = _format_gex_billions(_safe_float(row.get(field)))
        distance = _safe_float(row.get("distance_pct"))
        items.append(
            f"<li><strong>{strike:,.0f}</strong>: {gex} GEX, {distance:+.2f}% from spot.</li>"
        )
    return "".join(items)
def _jarvis_dealer_hedging_section(
    spot: float,
    gamma_flip: object,
    signed_gex: float,
    support_levels: pd.DataFrame,
    resistance_levels: pd.DataFrame,
) -> str:
    flip = float(gamma_flip) if gamma_flip else 0.0
    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0
    support_gex = _format_gex_billions(_safe_float(support_levels.iloc[0].get("put_gex"))) if top_support else "n/a"
    resistance_gex = _format_gex_billions(_safe_float(resistance_levels.iloc[0].get("call_gex"))) if top_resistance else "n/a"

    if signed_gex > 0 and (not flip or spot >= flip):
        regime = "The visible map leans long gamma / stabilizing."
        dealer_action = "Dealers are more likely to lean against moves: selling strength and buying weakness."
        regime_effect = "That can compress volatility and help price stay range-bound around heavy strikes."
    elif signed_gex < 0 and (not flip or spot < flip):
        regime = "The visible map leans short gamma / unstable."
        dealer_action = "Dealers are more likely to chase the move: buying as price rises and selling as price falls."
        regime_effect = "That can expand volatility once a key level breaks."
    else:
        regime = "The visible map is mixed."
        dealer_action = "Dealer hedging pressure is less one-sided right now."
        regime_effect = "The important read is whether price accepts above resistance or loses support with perp flow confirming."

    flip_line = (
        f"The gamma flip near {flip:,.0f} is the main regime switch. Above it, hedge flow should be more stabilizing; below it, hedge flow can become more momentum-following."
        if flip
        else "No clean gamma flip is available in this snapshot, so use the nearest support and resistance zones as the practical hedge-pressure levels."
    )
    downside_line = (
        f"If BTC loses {top_support:,.0f}, the put-heavy support zone ({support_gex}) is the key downside hedge trigger. A clean break below it can make dealers sell into weakness, especially if spot is also below the gamma flip."
        if top_support
        else "No clean downside put-heavy support zone is available in this snapshot."
    )
    upside_line = (
        f"If BTC pushes into {top_resistance:,.0f}, the call-heavy resistance zone ({resistance_gex}) is the key upside hedge zone. In a long-gamma regime this can force selling into strength and create pinning; a clean acceptance through it weakens that cap."
        if top_resistance
        else "No clean upside call-heavy resistance zone is available in this snapshot."
    )

    return f"""
        <h4>Dealer Hedging Pressure</h4>
        <p>{escape(regime)} <strong>{escape(dealer_action)}</strong> {escape(regime_effect)}</p>
        <ul>
            <li><strong>Regime switch:</strong> {escape(flip_line)}</li>
            <li><strong>Downside trigger:</strong> {escape(downside_line)}</li>
            <li><strong>Upside hedge zone:</strong> {escape(upside_line)}</li>
        </ul>
    """
def _jarvis_options_pressure_section(bundle: dict[str, object]) -> str:
    front_24h = bundle.get("front_24h", {})
    front_7d = bundle.get("front_7d", {})
    pin = bundle.get("pin", {})
    iv_term = bundle.get("iv_term", {})
    risk_reversal = bundle.get("risk_reversal", pd.DataFrame())
    pressure = bundle.get("pressure_forecast", {})
    history = bundle.get("history_comparison", {})
    front_rr = _safe_float(risk_reversal["risk_reversal"].iloc[0]) if isinstance(risk_reversal, pd.DataFrame) and not risk_reversal.empty else 0.0
    return f"""
        <h4>Front-Week Options Pressure</h4>
        <ul>
            <li><strong>Front 24H GEX:</strong> {_format_gex_billions(_safe_float(front_24h.get("abs_gex")))} absolute, {_format_gex_billions(_safe_float(front_24h.get("signed_gex")), signed=True)} signed. Top strike { _safe_float(front_24h.get("top_strike")):,.0f} ({_safe_float(front_24h.get("top_distance_pct")):+.2f}% from spot).</li>
            <li><strong>Front 7D GEX:</strong> {_format_gex_billions(_safe_float(front_7d.get("abs_gex")))} absolute, {_format_gex_billions(_safe_float(front_7d.get("signed_gex")), signed=True)} signed. Use this before the full 120-day chain for today's dealer-pressure read.</li>
            <li><strong>Pin:</strong> {escape(str(pin.get("pin_copy", "No pin candidate is visible.")))}</li>
            <li><strong>Risk reversal:</strong> Front 25D call IV minus put IV is {front_rr:+.2f}; RR z-score is {_safe_float(history.get("rr_zscore")):+.2f} while history builds.</li>
            <li><strong>IV term:</strong> {escape(str(iv_term.get("term_regime", "Unavailable")))} at {_safe_float(iv_term.get("iv_ratio")):.2f}x front/back IV. {escape(str(iv_term.get("term_copy", "")))}</li>
            <li><strong>Estimated charm/vanna:</strong> {escape(str(pressure.get("pressure_copy", "Unavailable.")))}</li>
            <li><strong>Historical context:</strong> Current GEX is {_safe_float(history.get("gex_vs_30d_median")):+.1%} vs the rolling history median; 24H OI delta is {_safe_float(history.get("oi_24h_delta")):+,.0f} contracts.</li>
        </ul>
    """
def _jarvis_block_flow_section(bundle: dict[str, object]) -> str:
    flow = bundle.get("block_flow_7d", {}) if isinstance(bundle.get("block_flow_7d", {}), dict) else {}
    disagreements = bundle.get("block_disagreements", pd.DataFrame())
    update = bundle.get("block_store_update", {}) if isinstance(bundle.get("block_store_update", {}), dict) else {}
    matched = int(flow.get("matched_legs", 0) or 0)
    blocks = int(flow.get("blocks", 0) or 0)
    rfq = int(flow.get("rfq_trades", 0) or 0)
    stored = int(flow.get("stored_trades", 0) or 0)
    inserted = int(update.get("inserted", 0) or 0)
    error = str(update.get("error", ""))

    if matched <= 0:
        status = (
            f"No matched block-flow gamma estimate is available yet. Stored block legs: {stored:,}. "
            "The page will improve as the local 30D block-trade store accumulates."
        )
        if error:
            status += f" Latest Deribit block poll error: {error}"
        return f"""
            <h4>Block-Adjusted Gamma Map</h4>
            <p>{escape(status)}</p>
        """

    confidence = "Medium" if rfq > 0 else "Medium-Low"
    if isinstance(disagreements, pd.DataFrame) and not disagreements.empty:
        items = []
        for _, row in disagreements.iterrows():
            strike = _safe_float(row.get("strike"))
            raw = _format_gex_billions(_safe_float(row.get("signed_gex")), signed=True)
            block = _format_gex_billions(_safe_float(row.get("block_adjusted_gex")), signed=True)
            distance = _safe_float(row.get("distance_pct"))
            rfq_count = int(_safe_float(row.get("rfq_trades")))
            items.append(
                f"<li><strong>{strike:,.0f}</strong> ({distance:+.2f}%): raw strike map {raw}, block-adjusted estimate {block}; RFQ legs {rfq_count}.</li>"
            )
        disagreement_copy = (
            "<p><strong>Important disagreement:</strong> The raw strike map and recent block flow disagree at these strikes. "
            "Treat those levels with lower confidence because institution-sized flow may be pointing to the opposite dealer-side exposure.</p>"
            f"<ul>{''.join(items)}</ul>"
        )
    else:
        disagreement_copy = (
            "<p>No major nearby disagreement is visible between the raw strike map and the 7D block-adjusted estimate. "
            "That raises confidence where block flow exists, but no-block strikes remain map-only.</p>"
        )

    return f"""
        <h4>Block-Adjusted Gamma Map</h4>
        <p><strong>Confidence: {escape(confidence)}.</strong> This is an estimate from Deribit block trades, not paid dealer inventory. Direction is interpreted as customer/aggressor side: buy = dealer short gamma, sell = dealer long gamma. Fresh inserted block legs this refresh: {inserted:,}.</p>
        <p>7D matched block flow: {matched:,} legs across {blocks:,} blocks; RFQ-tagged legs: {rfq:,}. Total block-adjusted gamma estimate: {_format_gex_billions(_safe_float(flow.get("total_block_gex")), signed=True)}.</p>
        {disagreement_copy}
    """
def _jarvis_plain_vwap_state(latest: pd.Series) -> tuple[str, str]:
    close = _safe_float(latest.get("close"))
    avwap = _safe_float(latest.get("avwap"))
    band_1_up = _safe_float(latest.get("band_1_up"))
    band_1_dn = _safe_float(latest.get("band_1_dn"))
    band_2_up = _safe_float(latest.get("band_2_up"))
    band_2_dn = _safe_float(latest.get("band_2_dn"))
    if close >= band_2_up and band_2_up > 0:
        return (
            "stretched above anchored VWAP",
            "BTC is trading above the +2 sigma VWAP band. That usually means the move is hot; chasing late longs is riskier unless price keeps accepting above the band.",
        )
    if close <= band_2_dn and band_2_dn > 0:
        return (
            "stretched below anchored VWAP",
            "BTC is trading below the -2 sigma VWAP band. That usually means downside is extended; shorts need confirmation instead of blindly pressing lows.",
        )
    if close >= avwap:
        return (
            "above anchored VWAP",
            "BTC is above anchored VWAP. For a novice read, that means buyers currently control the intraday average price from the selected anchor.",
        )
    return (
        "below anchored VWAP",
        "BTC is below anchored VWAP. For a novice read, that means sellers currently control the intraday average price from the selected anchor.",
    )
def _positioning_color(state: str) -> tuple[str, str]:
    mapping = {
        "green": ("#7ef0a0", "positioning-green"),
        "yellow": ("#f4d35e", "positioning-yellow"),
        "red": ("#ff6b6b", "positioning-red"),
    }
    return mapping.get(state, mapping["yellow"])
def _build_institutional_positioning_cards(bundle: dict[str, object]) -> list[dict[str, str]]:
    spot = _safe_float(bundle.get("spot"))
    gamma_flip = bundle.get("gamma_flip")
    signed_gex = _safe_float(bundle.get("total_signed_gex"))
    support_levels = bundle.get("support_levels", pd.DataFrame())
    resistance_levels = bundle.get("resistance_levels", pd.DataFrame())
    perp = bundle.get("perp_snapshot", {})
    ibit = bundle.get("ibit_context", {})
    avwap_df = bundle.get("avwap_df", pd.DataFrame())
    sweeps = bundle.get("sweeps", pd.DataFrame())

    ibit_flow_state = str(ibit.get("flow_state", "Neutral"))
    if ibit_flow_state == "Supportive":
        ibit_state = "green"
        ibit_status = "Bullish"
    elif ibit_flow_state == "Weak":
        ibit_state = "red"
        ibit_status = "Bearish"
    else:
        ibit_state = "yellow"
        ibit_status = "Neutral"
    ibit_copy = str(ibit.get("flow_copy", "ETF flow is unavailable right now."))

    if signed_gex > 0 and gamma_flip and spot >= float(gamma_flip):
        gex_state = "green"
        gex_status = "Constructive"
        gex_copy = f"Positive net gamma and spot above the flip near {float(gamma_flip):,.0f} point to a calmer, more supportive options regime."
    elif signed_gex < 0 and gamma_flip and spot < float(gamma_flip):
        gex_state = "red"
        gex_status = "Volatile"
        gex_copy = f"Negative net gamma and spot below the flip near {float(gamma_flip):,.0f} favor faster moves and shakier downside reactions."
    else:
        gex_state = "yellow"
        gex_status = "Mixed"
        gex_copy = "Options structure is not cleanly one-sided right now, so use the strike map and perp tape together."

    support_distance = _safe_float(support_levels.iloc[0].get("distance_pct")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    resistance_distance = _safe_float(resistance_levels.iloc[0].get("distance_pct")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0
    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0
    if top_support and spot > top_support and resistance_distance > 1.5:
        levels_state = "green"
        levels_status = "Room Above"
        levels_copy = f"BTC is sitting above nearby support at {top_support:,.0f} with some room before the first major resistance at {top_resistance:,.0f}."
    elif top_support and spot <= top_support:
        levels_state = "red"
        levels_status = "Fragile"
        levels_copy = f"BTC is leaning on or below the nearest support zone around {top_support:,.0f}; breaks here deserve respect."
    else:
        levels_state = "yellow"
        levels_status = "Trapped"
        levels_copy = f"BTC is caught between nearby support and resistance. Expect more level-to-level behavior than open air movement."

    funding_rate = abs(_safe_float(perp.get("last_funding_rate")))
    oi_change_1h = _safe_float(bundle.get("oi_change_1h"))
    if funding_rate < 0.0004 and 0.0 <= oi_change_1h <= 0.03:
        perp_state = "green"
        perp_status = "Healthy"
        perp_copy = "Funding is not crowded and OI expansion still looks constructive rather than overheated."
    elif funding_rate >= 0.0008 or oi_change_1h > 0.05:
        perp_state = "red"
        perp_status = "Crowded"
        perp_copy = "Perp positioning looks stretched. Late leverage is more likely to destabilize the move than support it."
    else:
        perp_state = "yellow"
        perp_status = "Mixed"
        perp_copy = "Perp leverage is active, but not clean enough to call supportive or outright dangerous on its own."

    if isinstance(avwap_df, pd.DataFrame) and not avwap_df.empty:
        latest = avwap_df.iloc[-1]
        close = _safe_float(latest.get("close"))
        avwap = _safe_float(latest.get("avwap"))
        band_2_up = _safe_float(latest.get("band_2_up"))
        band_2_dn = _safe_float(latest.get("band_2_dn"))
        # Empty string (not None) when no sweeps: these branches call
        # .startswith, and a None here crashed the whole options page
        # whenever the sweep detector came back empty.
        latest_sweep = "" if sweeps.empty else str(sweeps.sort_values("ts").iloc[-1].get("direction", ""))
        if close > avwap and close < band_2_up and (not latest_sweep or latest_sweep.startswith("Down-sweep")):
            tape_state = "green"
            tape_status = "Supportive"
            tape_copy = "Price is above anchored VWAP and the latest tape does not show a fresh bearish rejection."
        elif close < avwap and latest_sweep.startswith("Up-sweep"):
            tape_state = "red"
            tape_status = "Weak"
            tape_copy = "Price is below anchored VWAP and the latest sweep behavior leans bearish, so upside needs cleaner proof."
        else:
            tape_state = "yellow"
            tape_status = "Chop"
            tape_copy = "VWAP and the latest sweep/tape behavior are mixed, so execution quality matters more than broad bias."
    else:
        tape_state = "yellow"
        tape_status = "Mixed"
        tape_copy = "Anchored VWAP context is unavailable right now."

    cards = [
        {"title": "IBIT Flow", "status": ibit_status, "state": ibit_state, "copy": ibit_copy},
        {"title": "GEX Regime", "status": gex_status, "state": gex_state, "copy": gex_copy},
        {"title": "Dealer Levels", "status": levels_status, "state": levels_state, "copy": levels_copy},
        {"title": "Perp Positioning", "status": perp_status, "state": perp_state, "copy": perp_copy},
        {"title": "VWAP / Tape", "status": tape_status, "state": tape_state, "copy": tape_copy},
    ]
    return cards
def _render_institutional_positioning_summary(bundle: dict[str, object]):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    cards = _build_institutional_positioning_cards(bundle)
    fragments = []
    for card in cards:
        dot_color, class_name = _positioning_color(str(card["state"]))
        fragments.append(
            f"""
            <div class="positioning-card">
                <div class="positioning-head">
                    <span class="positioning-dot" style="background:{dot_color};"></span>
                    <div class="positioning-title">{escape(str(card["title"]))}</div>
                </div>
                <div class="positioning-status {class_name}">{escape(str(card["status"]))}</div>
                <div class="positioning-copy">{escape(str(card["copy"]))}</div>
            </div>
            """
        )
    render(f'<div class="positioning-grid">{"".join(fragments)}</div>')
def _build_jarvis_summary(bundle: dict[str, object], anchor_mode: str) -> str:
    spot = _safe_float(bundle.get("spot"))
    gamma_flip = bundle.get("gamma_flip")
    signed_gex = _safe_float(bundle.get("total_signed_gex"))
    top5_concentration = _safe_float(bundle.get("top5_concentration"))
    support_levels = bundle.get("support_levels", pd.DataFrame())
    resistance_levels = bundle.get("resistance_levels", pd.DataFrame())
    perp = bundle.get("perp_snapshot", {})
    ibit = bundle.get("ibit_context", {})
    funding_rate = _safe_float(perp.get("last_funding_rate"))
    oi_change_1h = _safe_float(bundle.get("oi_change_1h"))
    atm_iv = bundle.get("atm_iv", pd.DataFrame())
    sweeps = bundle.get("sweeps", pd.DataFrame())

    if signed_gex < 0:
        big_picture = (
            "Put-side gamma is dominating this snapshot. In simple terms, the options map is more sensitive to downside levels, "
            "and breaks below key support can become faster if perp flow confirms."
        )
    elif signed_gex > 0:
        big_picture = (
            "Call-side gamma is dominating this snapshot. In simple terms, upside strike zones matter more right now, "
            "and BTC may react or slow down around the strongest resistance levels."
        )
    else:
        big_picture = "Net GEX is close to balanced, so the strike map is less directional and perp flow deserves more weight."

    flip_read = "The gamma flip is not available, so do not use it as a trigger today."
    if gamma_flip:
        flip = float(gamma_flip)
        if spot >= flip:
            flip_read = f"BTC is above the gamma flip near {flip:,.0f}. Staying above it supports a more constructive intraday read."
        else:
            flip_read = f"BTC is below the gamma flip near {flip:,.0f}. Reclaiming it would improve the bullish read; rejection keeps pressure on."

    support_text = _jarvis_level_line(support_levels, "put_gex", "Support", "put GEX")
    resistance_text = _jarvis_level_line(resistance_levels, "call_gex", "Resistance", "call GEX")
    funding_text = _jarvis_funding_read(funding_rate, oi_change_1h, _safe_float(perp.get("funding_z")))
    iv_text = _jarvis_iv_read(atm_iv)
    sweep_text = _jarvis_sweep_read(sweeps)
    ibit_state = str(ibit.get("flow_state", "Unavailable"))
    ibit_copy = str(ibit.get("flow_copy", "IBIT flow context is unavailable right now."))
    ibit_price = _safe_float(ibit.get("price"))
    ibit_return = _safe_float(ibit.get("session_return"))
    ibit_ratio = _safe_float(ibit.get("volume_ratio"))

    top_support = _safe_float(support_levels.iloc[0].get("strike")) if isinstance(support_levels, pd.DataFrame) and not support_levels.empty else 0.0
    top_resistance = _safe_float(resistance_levels.iloc[0].get("strike")) if isinstance(resistance_levels, pd.DataFrame) and not resistance_levels.empty else 0.0

    bullish_case = (
        f"If BTC holds above {top_support:,.0f} and flow stays supportive, the cleaner long idea is a push toward {top_resistance:,.0f}."
        if top_support and top_resistance
        else "If BTC holds its intraday structure, the bullish case improves; use the visible GEX resistance zones as upside checkpoints."
    )
    bearish_case = (
        f"If BTC loses {top_support:,.0f} with weak tape and negative OI/funding confirmation, the next support zones become more important."
        if top_support
        else "If BTC loses intraday structure with weak perp flow, the bearish case improves; use the visible support zones as downside checkpoints."
    )
    resistance_case = (
        f"If BTC reaches {top_resistance:,.0f}, watch whether it accepts above that level or rejects. Acceptance can open continuation; rejection makes it a fade/pullback zone."
        if top_resistance
        else "If BTC reaches a visible resistance zone, watch acceptance vs rejection rather than treating the level as guaranteed."
    )

    support_items = _jarvis_level_list(support_levels, "put_gex", "Support")
    resistance_items = _jarvis_level_list(resistance_levels, "call_gex", "Resistance")
    dealer_hedging_section = _jarvis_dealer_hedging_section(
        spot,
        gamma_flip,
        signed_gex,
        support_levels,
        resistance_levels,
    )
    options_pressure_section = _jarvis_options_pressure_section(bundle)
    block_flow_section = _jarvis_block_flow_section(bundle)

    return f"""
        <h4>Big Picture</h4>
        <p><strong>Spot:</strong> {spot:,.2f}. <strong>Net GEX:</strong> {_format_gex_billions(signed_gex, signed=True)}. {escape(big_picture)}</p>
        <p>{escape(flip_read)} Top-5 GEX concentration is {top5_concentration:.1%}, so a small number of strikes are carrying a meaningful part of the options pressure.</p>

        <h4>Key Levels To Watch</h4>
        <p><strong>Support zones:</strong></p>
        <ul>{support_items}</ul>
        <p><strong>Resistance zones:</strong></p>
        <ul>{resistance_items}</ul>
        <p>{escape(support_text)} {escape(resistance_text)}</p>

        {dealer_hedging_section}

        {block_flow_section}

        {options_pressure_section}

        <h4>Intraday Read</h4>
        <ul>
            <li><strong>Funding / OI:</strong> {escape(funding_text)}</li>
            <li><strong>IV:</strong> {escape(iv_text)}</li>
            <li><strong>IBIT:</strong> {escape(ibit_state)} flow. {escape(ibit_copy)} Current IBIT price is {ibit_price:,.2f} with session return {ibit_return:+.2%} and volume at {ibit_ratio:.2f}x its 20-day average.</li>
            <li><strong>Sweeps:</strong> {sweep_text}</li>
        </ul>

        <h4>How To Use This Today</h4>
        <ul>
            <li><strong>Bullish scenario:</strong> {escape(bullish_case)}</li>
            <li><strong>Bearish scenario:</strong> {escape(bearish_case)}</li>
            <li><strong>Resistance test:</strong> {escape(resistance_case)}</li>
        </ul>

        <h4>Simple Summary</h4>
        <p class="jarvis-simple-summary">Do not treat any GEX level as magic. Treat it as a map of where dealer hedging pressure may appear. The highest-alpha read is whether price is entering a stabilizing zone where hedging can compress the move, or breaking into an unstable zone where hedging can fuel expansion.</p>
    """
def _render_jarvis_widget(bundle: dict[str, object], anchor_mode: str):
    html_fn = getattr(st, "html", None)
    render = html_fn if callable(html_fn) else lambda markup: st.markdown(markup, unsafe_allow_html=True)
    summary_html = _build_jarvis_summary(bundle, anchor_mode)
    render(
        f"""
        <details class="jarvis-fab">
            <summary>Ask Jarvis</summary>
            <div class="jarvis-panel">
                <div class="jarvis-panel-head">
                    <div class="jarvis-panel-title">Ask Jarvis</div>
                    <div class="jarvis-panel-copy">Read-only live interpreter of the current BTC options screener state. It explains what the current snapshot is implying; it does not invent data or place trades for you.</div>
                </div>
                <div class="jarvis-faq">
                    <details class="jarvis-faq-item" open>
                        <summary>Summarise how to use current BTC options data in my daytrading</summary>
                        <div class="jarvis-answer">
                            {summary_html}
                        </div>
                    </details>
                </div>
            </div>
        </details>
        """
    )
