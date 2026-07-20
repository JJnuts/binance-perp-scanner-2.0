"""Tools Hub — read-only windows into the external monitoring stack.

The daemons (funding-clock, flight-recorder, dual-entry, regime-monitor) and
the journal live OUTSIDE this app, in the vault's tools folder, and keep
running when the terminal is closed. This page renders their data by calling
THEIR OWN functions — no metric is ever recomputed in UI code, so the numbers
here are identical to the tools' CLI output by construction.

The single write path is the quick-entry trade form, which goes through
journal_db.insert_trade() (same validation and dedupe as the CSV importer).
"""

import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

# The external tools root. When the daemons move to a VPS, point this at the
# synced copy of the tools folder (or mount) — everything else stays intact.
TOOLS_ROOT = r"E:\Obsidian mindmap\Obsidian mindmap - Finance & Trading\FINANCE\TRADING\tools"

_FOLDERS = ("journal", "funding-clock", "regime-monitor", "flight-recorder", "dual-entry")
for _f in _FOLDERS:
    _p = os.path.join(TOOLS_ROOT, _f)
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _fmt(v, spec="+.2f"):
    return "n/a" if v is None else format(v, spec)


def _render_regime_strip():
    st.subheader("Regime state (HTF, 4h cadence — regime-def pinned)")
    log_path = os.path.join(TOOLS_ROOT, "regime-monitor", "regime_log.csv")
    if not os.path.exists(log_path):
        st.warning("regime_log.csv not found — is the regime monitor running?")
        return
    row = pd.read_csv(log_path).iloc[-1]
    age_h = "?"
    try:
        ts = datetime.strptime(str(row["ts_utc"]), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        age_h = f"{(datetime.now(timezone.utc) - ts).total_seconds() / 3600:.1f}"
    except ValueError:
        pass
    c = st.columns(6)
    c[0].metric("BTC daily regime", str(row["btc_daily_regime"]), f"score {row['regime_score']:.0f}",
                delta_color="off", help="Pinned snapshot of the terminal's own regime formula "
                f"(regime-def v{row['regime_def_version']}). Terminal edits do not affect this until a deliberate versioned sync.")
    c[1].metric("vs 200d", _fmt(row.get("price_vs_200d"), "+.1%"))
    c[2].metric("Funding z", _fmt(row.get("funding_z")))
    c[3].metric("OI pctile (30d)", _fmt(row.get("oi_pctile"), ".0f"))
    c[4].metric("V/%move z", _fmt(row.get("vpm_z")))
    c[5].metric("Drawdown", _fmt(row.get("drawdown_pct"), "+.1%"))
    quad = row.get("oi_price_quadrant", "")
    liq = row.get("liq_z_4h")
    liq_txt = _fmt(liq) if pd.notna(liq) else str(row.get("liq_status", "n/a"))
    st.caption(f"{quad} · liq z {liq_txt} · yesterday {_fmt(row.get('range_pct'), '.2%')} "
               f"({row.get('range_state', '')}) · BBWP {_fmt(row.get('bbwp'), '.0f')} · "
               f"logged {age_h}h ago (updates every 4h — HTF context, not an intraday signal)")


def _render_validation_tab():
    st.subheader("Forward validation — pre-registered verdicts in progress")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Funding-clock: settlement-flush fade v1.0**")
        try:
            import ledger
            s = ledger.summary()
            st.metric("TRIGGER events", f"{s['trigger_n']} / 40",
                      f"mean {s['trigger_mean_bp']}bp · hit {s['trigger_hit_pct']}%"
                      if s["trigger_n"] else "none yet", delta_color="off")
            st.caption(f"{s['events_logged']} settlements logged {s['by_decision']} · "
                       f"prediction: {s['prediction']} · verdict: **{s['verdict']}**")
        except Exception as e:
            st.warning(f"funding-clock ledger unavailable: {e}")
    with col2:
        st.markdown("**Dual-entry gate v1.0 (SHADOW)**")
        try:
            import journal_db as jdb
            con = jdb.connect()
            rows = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN outcome_logged=1 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome_logged=1 AND outcome_value>0 THEN 1 ELSE 0 END) "
                "FROM signals g JOIN setups s ON s.setup_id=g.setup_id "
                "WHERE s.name='compression-dual-entry'").fetchone()
            n, logged, hits = (rows[0] or 0), (rows[1] or 0), (rows[2] or 0)
            hit_pct = f"{100 * hits / logged:.0f}%" if logged else "n/a"
            st.metric("SHADOW signals", f"{logged} scored / 40",
                      f"expansion hit {hit_pct}" if logged else f"{n} fired, none ripe",
                      delta_color="off")
            st.caption("CRITERIA.md v1.0: at n=40 pooled — hit ≥25% PROMOTE, <18% KILL, "
                       "else extend to 80. Backtest reference 31.7%/29.9% vs ~14% baseline.")
        except Exception as e:
            st.warning(f"journal unavailable: {e}")


def _render_journal_tab():
    st.subheader("Journal — rolling 60-trade report (METRICS.md v1.0)")
    try:
        import journal_db as jdb
        import report as jreport
    except Exception as e:
        st.warning(f"journal modules unavailable: {e}")
        return
    con = jdb.connect()
    rows = jreport.load_trades(con)
    s = jreport.stats(rows)
    if not s["n"]:
        st.info("No trades logged yet. Import a CSV (tools/journal/import_trades_csv.py) "
                "or use the quick-entry form below.")
    else:
        c = st.columns(6)
        c[0].metric("Trades (window)", s["n"])
        c[1].metric("Expectancy", f"{s['expectancy_r']:+.2f}R")
        c[2].metric("Win rate", f"{s['win_rate_pct']:.0f}%")
        c[3].metric("Avg win / loss", f"{_fmt(s['avg_win_r'])} / {_fmt(s['avg_loss_r'])}")
        c[4].metric("Max DD", f"{s['max_dd_r']:.1f}R")
        c[5].metric("Cost in R", _fmt(s.get("cost_in_r_pct"), ".1f") + "%")
        for label, v, verdict in jreport.band_checks(s):
            icon = "🟢" if verdict in ("in band", "met") else ("🔴" if "OUT" in verdict or "below" in verdict else "⚪")
            st.caption(f"{icon} {label}: {_fmt(v)} → {verdict}")
        kills = [k for k, hit in jreport.kill_checks(s) if hit]
        if kills:
            st.error("KILL CRITERIA TRIGGERED: " + "; ".join(kills))
        splits = jreport.all_splits(rows)
        split_rows = [{"dimension": dim, "bucket": k,
                       "expectancy_R": g["expectancy_r"], "n": g["n"]}
                      for dim, groups in splits.items() for k, g in groups.items()]
        if split_rows:
            st.dataframe(pd.DataFrame(split_rows), use_container_width=True, hide_index=True)

    sig = jreport.signals_summary(con)
    if sig:
        st.markdown("**Signal feeds**")
        for setup, states in sig.items():
            for state, d in states.items():
                st.caption(f"{setup} · {state}: n={d['n']}, mean {_fmt(d['mean'])} "
                           f"{d['metric']}, hit {_fmt(d['hit_pct'], '.0f')}%")

    with st.expander("Quick-entry: log a trade"):
        with st.form("journal_quick_entry", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            symbol = c1.text_input("Symbol", "BTCUSDT")
            side = c2.selectbox("Side", ["LONG", "SHORT"])
            qty = c3.number_input("Qty", min_value=0.0, format="%.6f")
            c4, c5, c6, c7 = st.columns(4)
            entry = c4.number_input("Entry", min_value=0.0, format="%.4f")
            exit_px = c5.number_input("Exit", min_value=0.0, format="%.4f")
            stop = c6.number_input("Stop (required)", min_value=0.0, format="%.4f")
            fees = c7.number_input("Fees USD", min_value=0.0, format="%.4f")
            c8, c9, c10 = st.columns(3)
            entry_ts = c8.text_input("Entry UTC (YYYY-MM-DD HH:MM)")
            exit_ts = c9.text_input("Exit UTC (YYYY-MM-DD HH:MM)")
            tags = c10.text_input("Tags (comma-sep)")
            notes = st.text_input("Notes")
            if st.form_submit_button("Log trade"):
                try:
                    new, key = jdb.insert_trade(dict(
                        symbol=symbol.strip().upper(), side=side,
                        entry_ts_ms=jdb.parse_ts(entry_ts),
                        exit_ts_ms=jdb.parse_ts(exit_ts),
                        entry_px=float(entry), exit_px=float(exit_px),
                        stop_px=float(stop), qty=float(qty),
                        fees_usd=float(fees), tags=tags.strip() or None,
                        notes=notes.strip() or None))
                    st.success("Trade logged." if new else "Duplicate — already in the journal.")
                    st.caption("MAE/MFE enrich on the next `import_trades_csv.py --enrich` run.")
                except (ValueError, KeyError) as e:
                    st.error(f"Rejected: {e}")


def _render_recorder_tab():
    st.subheader("Flight recorder — non-backfillable data collection")
    db_path = os.path.join(TOOLS_ROOT, "flight-recorder", "flight.db")
    if not os.path.exists(db_path):
        st.warning("flight.db not found — is the recorder running?")
        return
    con = sqlite3.connect(db_path, timeout=10)
    now_ms = int(time.time() * 1000)
    day_ago = now_ms - 24 * 3600_000
    liq_n, liq_usd = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(notional_usdt),0) FROM liquidations "
        "WHERE recv_ts_ms > ?", (day_ago,)).fetchone()
    total_liq = con.execute("SELECT COUNT(*) FROM liquidations").fetchone()[0]
    last_cycle = con.execute("SELECT MAX(cycle_ts_ms) FROM snapshots_oi").fetchone()[0]
    ws_open = con.execute("SELECT COUNT(*) FROM sessions WHERE kind='ws' "
                          "AND ended_ts_ms IS NULL").fetchone()[0]
    recon_24h = con.execute("SELECT COUNT(*) FROM sessions WHERE kind='ws' "
                            "AND started_ts_ms > ?", (day_ago,)).fetchone()[0]
    age_min = (now_ms - last_cycle) / 60_000 if last_cycle else None
    c = st.columns(5)
    c[0].metric("Liqs 24h", f"{liq_n:,}", f"${liq_usd:,.0f} (lower bound)", delta_color="off")
    c[1].metric("Liqs total", f"{total_liq:,}")
    c[2].metric("Snapshot age", f"{age_min:.0f}m" if age_min is not None else "n/a")
    c[3].metric("WS live", "yes" if ws_open else "NO")
    c[4].metric("WS sessions 24h", recon_24h)
    if age_min is not None and age_min > 15:
        st.error(f"Snapshots stale ({age_min:.0f} min) — check the recorder daemon.")
    if not ws_open:
        st.error("No open websocket session — liquidations are NOT being recorded.")
    big = pd.read_sql_query(
        "SELECT datetime(trade_ts_ms/1000, 'unixepoch') AS utc, symbol, side, "
        "ROUND(notional_usdt) AS notional_usd FROM liquidations "
        "ORDER BY notional_usdt DESC LIMIT 10", con)
    if not big.empty:
        st.markdown("**Largest liquidations recorded**")
        st.dataframe(big, use_container_width=True, hide_index=True)


def render_tools_hub():
    st.title("Tools Hub")
    st.caption("Read-only windows into the standalone monitoring stack (daemons keep "
               "running when this app is closed). Numbers come from the tools' own "
               "functions — identical to their CLI output by construction.")
    _render_regime_strip()
    st.divider()
    tab_val, tab_journal, tab_rec = st.tabs(
        ["Validation verdicts", "Journal", "Flight recorder"])
    with tab_val:
        _render_validation_tab()
    with tab_journal:
        _render_journal_tab()
    with tab_rec:
        _render_recorder_tab()
