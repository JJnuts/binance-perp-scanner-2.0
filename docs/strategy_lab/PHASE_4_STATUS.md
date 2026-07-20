# Strategy Lab Phase 4 Status

Status: Complete

Date: 2026-07-17

## Implemented

- Added a strict Phase 4 contract for one BTCUSDT 5m EMA-9 fixed-hold strategy.
- Added an allowlisted compiler that rejects contract changes and any strategy
  file whose checksum differs from the reviewed static strategy.
- Generated only a dry-run Freqtrade futures configuration with no credentials,
  RPC service, Telegram integration, live-trading command, or generated Python.
- Added a deterministic 560-bar futures fixture with three candidate signals,
  one deliberately overlapping signal, two expected trades, hourly mark data,
  and one funding settlement.
- Added independent reconciliation for trade timestamps, fixed position size,
  fees, funding, and separately modeled slippage.
- Added a persisted worker state store whose command and Compose service are
  fixed by the application rather than supplied by a research contract.
- Added worker queued, running, completed, failed, and cancelled state handling.
- Added an isolated Docker cancellation probe and resource-control assertions.
- Limited each sequential proof container to one CPU and 768 MB RAM.
- Added eleven Phase 4 adapter and fixture tests; all pass locally.

## Runtime Evidence

- Freqtrade `2026.6` executed the approved futures fixture.
- Exactly two expected trades were produced.
- Entry and exit timestamps matched the independent expectation.
- The deliberately overlapping signal was rejected.
- Native fees, funding, absolute profit, and fee-inclusive return reconciled.
- Slippage-adjusted returns were calculated independently.
- Both positions reconciled to the Binance 0.001 BTC amount step.
- Lifecycle verification status: `passed`.
- Isolated cancellation probe status: `passed`.
- The probe was stopped and was not running after cancellation.
- The probe had no network, a read-only root, no-new-privileges, one CPU,
  768 MB RAM, and a 128-process limit.
- Full scanner and Strategy Lab regression suite: 87 tests passed.

Artifacts are retained under `tools/strategy_lab_phase4/artifacts/`:

- `verified-result.json`
- `cancellation-proof.json`
- `environment-manifest.json`
- `resolved-compose.json`

## Phase 4 Acceptance

- [x] The approved strategy compiles without arbitrary code generation.
- [x] Trade timestamps reconcile against an independent expectation.
- [x] Fees, slippage, funding, and position sizing reconcile.
- [x] Worker isolation and cancellation are tested.

All Phase 4 gates passed. Phase 5 requires explicit user approval before work
begins.
