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

## Notes

This is a market scanner, not a trading system. The values are derived from
public exchange data and may be delayed, temporarily unavailable, or affected by
Binance API limits.

The scanner currently covers Binance USDT-M perpetual futures only. It does not
scan spot pairs, options, other exchanges, on-chain data, order books, or news.

## Possible Next Improvements

- Add alert rules for setup-score changes and new entrants into the top ranks
- Add CSV export and saved watchlists
- Add taker buy/sell imbalance and order-book spread filters
- Add spot and multi-exchange support
- Add tests around metric calculations and API parsing
- Add deployment config for Streamlit Community Cloud, Docker, or a VPS
