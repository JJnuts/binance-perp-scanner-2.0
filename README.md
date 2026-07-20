# Binance Perp Scanner 2.0

A Streamlit dashboard for scanning active Binance USDT-M perpetual futures.

It fetches public Binance Futures market data and ranks altcoins by momentum,
relative strength vs BTC, and overextension.

## Features

- Excludes `BTCUSDT` and focuses on Binance-listed altcoin perpetuals
- Uses 1-hour candles and separates LTF momentum from HTF leadership
- Adds BTC-beta-adjusted alpha to separate real alt strength from BTC beta
- Adds volatility-adjusted return so clean movers outrank chaotic movers
- Combines volume ratio and volume z-score into a volume expansion sub-score
- Uses 20 / 36 / 50 EMA structure plus VWAP bias for trend scoring
- Pulls open interest statistics for liquidity gating and OI expansion scoring
- Pulls funding history for cumulative funding and funding-trend quality
- Produces separate Momentum, Overextension, and Setup rankings
- Includes a separate `BITCOIN` section with 1d spot volume bubble maps
- Auto-refreshes every 5 minutes
- Runs without a Binance account or API key

## Scoring Model

The LTF implementation focuses on 1H / 4H / 24H rotation:

```text
LTF Momentum Score
= 25% BTC-beta-adjusted alpha
+ 15% simple relative strength vs BTC
+ 25% volume expansion
+ 20% VWAP / EMA trend
+ 10% open interest expansion
+ 5% latest funding quality
```

The HTF implementation focuses on 24H / 72H / 7D leadership:

```text
HTF Momentum Score
= 30% BTC-beta-adjusted alpha
+ 20% volatility-adjusted return
+ 20% simple relative strength vs BTC
+ 15% HTF EMA / VWAP trend
+ 10% open interest expansion
+ 5% funding trend quality
```

Where:

- LTF relative strength uses `1H`, `4H`, and `24H` returns vs `BTCUSDT`
- HTF relative strength uses `24H`, `72H`, and `7D` returns vs `BTCUSDT`
- BTC-beta-adjusted alpha estimates each alt's BTC beta and strips it out
- Volatility-adjusted return divides returns by recent realized volatility
- Volume expansion combines a `1H` volume ratio with a `1H` volume z-score
- Trend uses `EMA20`, `EMA36`, `EMA50`, alignment, and short-term VWAP bias
- Open interest uses recent `openInterestHist` changes
- Funding quality uses latest funding plus cumulative/trending funding history

Overextension is tracked separately so strong names can still be flagged as hot.

## Bitcoin Bubble Map

The app also includes a `BITCOIN` section with three 1d bubble-map views:

- Binance spot volume
- Coinbase spot volume
- Aggregated spot volume across the public venues that respond cleanly

Bubble size reflects absolute BTC spot volume in USD terms. Color reflects a
30-day rolling volume z-score:

- `Cooling` = blue
- `Neutral` = gray
- `Heating` = pink
- `Overheating` = red

The aggregated view currently uses public spot data from Binance, Coinbase,
Bybit, OKX, and Kraken when available. Hyperliquid spot is not included in this
first version.

## Gate Defaults

Balanced defaults in the app:

- Minimum 24H quote volume: `10,000,000` USDT
- Minimum 24H trades: `15,000`
- Minimum open interest value: `5,000,000`

These are intentionally moderate. Tighten them if you want only the cleanest,
most liquid contracts.

## Quick Start

On Windows, run:

```bat
install.bat
run.bat
```

Or run manually:

```bash
python -m pip install -r requirements.txt
python -m streamlit run scanner.py
```

Then open:

```text
http://localhost:8501
```

## Architecture

The implementation lives in the `perpscanner/` package (config, net,
indicators, data layers, LTF/HTF features, options analytics, scoring,
research, UI). `scanner.py` is a thin entry shim that re-exports every
name, so `streamlit run scanner.py` and `import scanner` both keep
working.

Signal hygiene notes:

- All signals are computed on **closed bars only** - the in-progress
  candle from every venue is dropped before any z-score or trigger.
- Ignition triggers use **hard vetoes** (VWAP side + break-and-hold)
  plus a weighted confluence of expansion / volume / OI / taker / basis
  confirmations, instead of a 7-way AND.
- Trigger gates are **regime-conditional**: volume/OI z and ATR ROC
  thresholds scale with the BTC daily regime.

## Research Loop

Every scored scan is logged to `data/research_snapshots.sqlite`
(throttled, exception-proof). Forward returns are joined from later
snapshots of the same store - no lookahead - and turned into:

- **Rank IC**: per-factor Spearman IC vs 1h/4h/24h forward returns with
  Newey-West-adjusted t-stats, so overlapping snapshots do not overstate
  confidence and each score has to prove it ranks future winners.
- **Trigger event study**: direction-adjusted returns after fresh
  ignition triggers, in excess of the same-scan cross-section.

View it on the app's *Research / Signal Quality* page, or run:

```bash
py -m perpscanner.research
```

Leave the app running on the Altcoins page so snapshots accumulate; a
day of uptime gives a few hundred cross-sections.

## Strategy Lab

The sidebar's **Strategy Lab** page is a separate, offline research workflow.
It interprets a supported question, exposes material assumptions for review,
binds confirmation to the exact contract checksum, and submits a persisted
external-worker job. Streamlit only reads status and artifacts, so reruns do not
repeat long work.

Phase 8 deliberately executes only the frozen BTCUSDT 5m EMA9 reference study.
Revised contracts can be reviewed and confirmed, but remain non-runnable until
their full pipeline capability is implemented and validated. The page never
opens the final holdout and never authorizes live trading.

To verify the page and its recoverable job lifecycle in the pinned container,
start Docker Desktop and run:

```powershell
cd tools\strategy_lab_phase8
powershell -ExecutionPolicy Bypass -File .\run_phase8.ps1
```

The final stress proof reuses the Phase 8 image and adds no new large Docker
image. It exercises restartable data, concurrent isolation, cancellation,
corruption and engine failures, app restart, resource limits, and reviewer
reproduction:

```powershell
cd tools\strategy_lab_phase9
powershell -ExecutionPolicy Bypass -File .\run_phase9.ps1
```

## Websocket LTF Feed

An optional websocket feed streams closed 5m/15m/1h bars instead of
re-polling REST every scan (sidebar toggle, default off). It seeds from
one REST fetch, serves frames in the same schema, and silently falls
back to REST whenever a buffer is cold or stale. Note: some networks
never receive `fstream.binance.com` frames even though REST works -
enable it where fstream actually delivers (e.g. a VPS).

## Tests

```bash
py -m unittest discover tests
```

Run from the repo root (the tests import the `perpscanner` package via
the `scanner` shim).

## Notes

This is a market scanner, not a trading system. The values are derived from
public exchange data and may be delayed, temporarily unavailable, or affected by
Binance API limits.

The scanner currently covers Binance USDT-M perpetual futures only (plus
Deribit BTC options, multi-venue BTC spot volume, and US BTC ETF context
on the Bitcoin pages).

## Possible Next Improvements

- Add alert rules for setup-score changes and new entrants into the top ranks
- Recalibrate score weights once the research store has a few weeks of data
- Add CSV export and saved watchlists
- Extend the websocket feed to spot klines (basis confirmation currently REST-polls)
- Add deployment config for Streamlit Community Cloud, Docker, or a VPS
