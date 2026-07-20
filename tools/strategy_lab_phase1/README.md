# Strategy Lab Phase 1 Framework Proof

This directory proves that pinned VectorBT and Freqtrade environments can
produce deterministic results from one hand-verified fixture. It is not part of
the scanner runtime and contains no live-trading configuration.

## Scope

- VectorBT `0.28.5` in a dedicated Python 3.12 container.
- Freqtrade `2026.6` in its official Docker image.
- One synthetic BTC/USDT 5-minute EMA 3 bounce.
- One delayed entry and one delayed exit.
- Two identical executions of each engine.
- Exact timestamp/price parity and normalized return comparison.
- Resource limits of two CPUs and 2 GB RAM per job.

The Freqtrade proof uses spot mode only to validate framework lifecycle and
signal timing without requiring futures funding, mark-price, or leverage-tier
fixtures. Binance USDT-M futures behavior remains a separate mandatory gate for
the production Freqtrade adapter.

## Fixture Definition

The shared CSV contains forty increasing candles. Exactly one candle is altered
so its low touches EMA 3:

1. EMA uses `adjust=False` and `min_periods=3`.
2. The previous three closes must be above EMA.
3. The signal candle low must be at or below EMA.
4. The signal candle close must remain above EMA.
5. Entry occurs at the following candle open.
6. Exit is signaled two candles after the bounce and executes at the next open.
7. Fees are 0.1 percent per side.

Expected timestamps:

- Event: `2026-01-01T01:15:00Z`
- Entry: `2026-01-01T01:20:00Z` at `115.8`
- Exit: `2026-01-01T01:30:00Z` at `117.8`

## Safety Boundaries

- No exchange key or secret.
- Freqtrade configuration has `dry_run: true`.
- No `trade`, `webserver`, or live RPC command is present.
- No container publishes a port.
- VectorBT runs without network access.
- Preparation and verification jobs run without network access.
- Backtesting is the only Freqtrade command.
- Every job is temporary and uses `docker compose run --rm`.
- Docker Compose has no restart policy.
- VectorBT's Numba JIT cache is written only to the temporary `/tmp` filesystem
  because the container root filesystem is read-only.

The Freqtrade backtest may access Binance public market metadata because the
engine validates exchange precision. Historical candles still come only from
the committed synthetic fixture.

## One-Time Prerequisite

Run this from an Administrator PowerShell and restart Windows:

```powershell
wsl.exe --install --no-distribution
```

After restart, install Docker Desktop in per-user mode with the WSL2 backend.
Disable `Start Docker Desktop when you sign in`.

## Running The Proof

Start Docker Desktop manually, wait until its engine is ready, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_phase1.ps1
```

The script:

1. Checks WSL, Docker, and available memory.
2. Pulls the pinned Freqtrade tag and builds the VectorBT image.
3. Converts the shared CSV into Freqtrade JSON data.
4. Runs each proof twice with cache disabled.
5. Compares both runs and both engines.
6. Writes manifests and results under `artifacts/`.
7. Removes temporary containers.

Phase 1 does not pass until image digests are recorded, both runs reproduce, and
the generated dependency freeze is reviewed and promoted to a lock file.
