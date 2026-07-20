# Strategy Lab Phase 2 Status

Status: Complete

Date: 2026-07-16

## Delivered

- Added an isolated `perpscanner/strategy_lab/` package with no Streamlit or
  scanner-startup dependency.
- Added a public Binance USDT-M kline client with bounded retry and backoff.
- Added start-inclusive/end-exclusive UTC request contracts with interval
  alignment validation.
- Added page-level atomic checkpoints and restart from the first missing bar.
- Preserved every successful raw API page as canonical immutable JSON.
- Added deterministic normalized CSV generation with explicit UTC timestamps.
- Added duplicate, conflicting-duplicate, gap, alignment, coverage, ordering,
  and unclosed-bar gates.
- Added page, raw aggregate, normalized, quality, checkpoint, and request
  checksums to a reconstruction manifest.
- Added offline manifest verification and exact normalized-file reconstruction.
- Added a Phase 2 command-line runner and operating documentation.
- Kept generated market data separate under `data/strategy_lab/` and ignored it
  from Git.

## Reference Download Evidence

- Request: Binance public `GET /fapi/v1/klines`.
- Symbol and venue: `BTCUSDT`, USDT-M perpetual futures.
- Interval: `5m`.
- Coverage: `2024-07-01T00:00:00Z` inclusive through
  `2026-07-01T00:00:00Z` exclusive.
- Raw pages: 141.
- Expected and normalized rows: 210,240.
- Gaps: 0.
- Duplicates: 0.
- Conflicting duplicates: 0.
- Unclosed rows: 0.
- Unexpected or out-of-range rows: 0.
- Raw aggregate SHA-256:
  `d7c83d7c98c2796e6ef66680884be5c522fc172fed25983752bd4ad162891c93`.
- Normalized CSV SHA-256:
  `55a2a57535e20aba6bc665de85664da0a55a7e8c94e02270138a47bb9f6c1c42`.
- Offline reconstruction verification: passed.
- Local generated size: approximately 61 MB.

## Verification

- Phase 2 focused tests cover interruption/resume, completed-run idempotence,
  duplicate and missing-bar rejection, unclosed-bar rejection, UTC boundary
  validation, checksum tampering, and byte-exact reconstruction.
- Full scanner and Strategy Lab suite: 69 tests passed.
- Existing scanner modules remain independent of the new data service.

## Risks Addressed

- R-04: gaps, duplicates, incomplete coverage, and source inconsistency.
- R-19: bounded page size, sequential collection, and modest local storage.
- R-21: additive isolated package plus full scanner regression suite.
- R-23: bounded retries, raw response preservation, and resumable checkpoints.

## Phase 2 Acceptance

- [x] Paginated two-year 5-minute download is complete and restartable.
- [x] Closed-bar, UTC, duplicate, gap, and checksum tests pass.
- [x] Raw and normalized layers are separated.
- [x] The data manifest is sufficient to reconstruct the dataset.

All Phase 2 gates passed. Phase 3 requires explicit approval before work begins.
