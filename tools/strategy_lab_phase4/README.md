# Strategy Lab Phase 4 Freqtrade Adapter

Phase 4 proves that one reviewed strategy contract can pass through a safe
adapter into Freqtrade without generating or executing arbitrary strategy code.
It validates lifecycle mechanics only; it is not a strategy recommendation.

## Frozen Scope

- BTCUSDT Binance USD-M futures
- 5-minute closed candles
- EMA 9 long bounce
- next-bar-open entry
- fixed 12-bar hold
- one position at a time
- fixed 1,000 USDT notional
- 4 bps fee per side
- 1.5 bps modeled slippage per side
- historical funding included

The compiler accepts only the reviewed contract and the exact checksum of
`ApprovedEma9FixedHoldStrategy.py`. It writes a dry-run futures config with no
credentials, Telegram, API server, live-trading command, or generated Python.
The runner copies the reviewed strategy and config into a disposable
`runtime/user_data` directory. Freqtrade may create its normal working
subdirectories there without receiving write access to the reviewed source.

## Runtime Proof

Start Docker Desktop and wait for the engine, then run:

```powershell
cd "C:\Users\mkpc\Documents\Codex\2026-04-26\i-asked-cloud-sonnet-4-6\binance-perp-scanner\tools\strategy_lab_phase4"
powershell -ExecutionPolicy Bypass -File .\run_phase4.ps1
```

The proof runs sequentially with a limit of one CPU, 768 MB RAM, and 128
processes per container:

1. adapter unit tests in an offline container;
2. allowlisted contract compilation;
3. deterministic futures, mark-price, and funding fixture generation;
4. Freqtrade futures backtest;
5. exact trade timestamp and cost-ledger reconciliation;
6. an actual isolated container start, cancellation, and stopped-state check.

Only the Freqtrade backtest service has ordinary outbound networking, because
Freqtrade loads public Binance market and leverage metadata. It has no API
credentials and exposes no ports. All other proof services use no network.

Successful artifacts are written under `artifacts/`.

If a run already printed a passed verification JSON and then stopped during
the cancellation probe, resume without repeating the backtest:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_phase4.ps1 -ResumeAfterVerification
```
