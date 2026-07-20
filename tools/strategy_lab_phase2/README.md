# Strategy Lab Phase 2 Trusted Binance Data

This tool downloads the reference BTCUSDT USDT-M perpetual dataset from the
public Binance Futures kline endpoint. It does not use exchange credentials,
Docker, Streamlit, or live-trading functionality.

## Reference Dataset

- Symbol: `BTCUSDT`
- Venue: Binance USDT-M futures
- Interval: `5m`
- Start: `2024-07-01T00:00:00Z` inclusive
- End: `2026-07-01T00:00:00Z` exclusive
- Bar policy: closed bars only

## Run Or Resume

From the repository root:

```powershell
python tools\strategy_lab_phase2\download_btcusdt_5m.py
```

Progress is checkpointed after every successful page. Running the same command
after an interruption resumes from the first missing timestamp. Running it
after completion performs an offline manifest verification and does not rewrite
the dataset.

For an offline verification only:

```powershell
python tools\strategy_lab_phase2\download_btcusdt_5m.py --verify-only
```

## Storage

The generated dataset is kept under:

```text
data/strategy_lab/binance_usdm/BTCUSDT/5m/20240701T000000Z__20260701T000000Z/
```

It contains:

- `raw/pages/`: immutable canonical JSON API pages.
- `download-state.json`: restart checkpoint and page index.
- `normalized/bars.csv`: canonical UTC closed-bar dataset.
- `quality.json`: coverage, gap, duplicate, alignment, and closed-bar report.
- `manifest.json`: request, transformation, file, and checksum metadata needed
  to reconstruct and verify the normalized dataset.

The generated data directory is intentionally ignored by Git.
