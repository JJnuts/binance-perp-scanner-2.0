# Binance Perp Scanner

A Streamlit dashboard for scanning active Binance USDT-M perpetual futures.

It fetches public Binance Futures market data, computes VWAP z-scores across
2-day, 5-day, and 7-day windows, adds funding and BTC-correlation context, and
clusters assets so unusual regimes are easier to spot.

## Features

- Scans all active USDT-M perpetual contracts from Binance Futures
- Computes 2D, 5D, and 7D VWAP z-scores from 4-hour candles
- Shows weekly return, BTC correlation, funding rate, and funding z-score
- Clusters assets with HDBSCAN when available
- Falls back to KMeans if HDBSCAN is unavailable
- Auto-refreshes every 5 minutes
- Runs without a Binance account or API key

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
scan spot pairs, options, other exchanges, on-chain data, order books, social
signals, or news.

## Possible Next Improvements

- Add alert rules for extreme z-score, funding, and correlation changes
- Add CSV export and saved watchlists
- Add timeframe controls instead of fixed 4-hour candles
- Add spot and multi-exchange support
- Add tests around metric calculations and API parsing
- Add deployment config for Streamlit Community Cloud, Docker, or a VPS
