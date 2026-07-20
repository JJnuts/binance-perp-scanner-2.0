# Strategy Lab Phase 3 Status

Status: Complete

Date: 2026-07-16

## Completed

- Added a strict capability gate that accepts only the frozen BTCUSDT 5m EMA-9
  long-bounce contract.
- Validated the reference contract against the Phase 0 JSON Schema.
- Added delay-1 entry, warmup, touch, penetration, rejection, cooldown, and
  overlap semantics.
- Added gross and fee/slippage/funding-adjusted forward returns.
- Added target/stop/neither/unresolved barrier accounting.
- Added a checksummed historical funding store with actual exchange timestamps
  and scheduled eight-hour coverage auditing.
- Downloaded and verified 2,190 funding settlements with no gaps or duplicates.
- Detected six first-hit ambiguous 5m windows and downloaded only their thirty
  required 1m bars.
- Added lower-timeframe replay; same-1m target/stop hits remain unresolved.
- Added sample size, Wilson success intervals, deterministic bootstrap return
  intervals, regime views, and half-year subperiod views.
- Added chronological train/validation/final-holdout labeling and kept 1,604
  final-holdout event outcomes hidden.
- Added a hand fixture where vectorized and independent recursive EMA
  calculations agree within `1e-12`.
- Added a deliberate cooldown exclusion and ambiguous-bar resolution fixture.
- Added an isolated Docker runner for pandas/VectorBT parity.
- Full scanner and Strategy Lab suite: 76 tests passed.
- Executed both the pandas reference and VectorBT `0.28.5` backends inside the
  pinned Phase 1 container.
- Compared 8,244 accepted events across exact signal, entry, split, holdout,
  barrier-status, and barrier-resolution fields.
- Compared EMA, price, funding, barrier-return, and all forward-return fields
  with a maximum absolute numeric difference of `0.0`.
- Retained engine summaries, event logs, run manifests, and `parity.json` under
  `tools/strategy_lab_phase3/artifacts/`.

## Provisional Reference-Backend Evidence

- Raw candidate signals: 35,014.
- Accepted independent events: 8,244.
- Development events with visible outcomes: 6,640.
- Hidden final-holdout events: 1,604.
- Overlap exclusions: 23,662.
- Cooldown exclusions: 3,107.
- Incomplete-tail exclusions: 1.
- Development ambiguous events: 4.
- Remaining unresolved after 1m replay: 2.

These counts validate the mechanics only. They are not a strategy
recommendation, and final-holdout outcomes remain unopened.

## Phase 3 Acceptance

- [x] The pandas reference executes in the pinned VectorBT container.
- [x] The VectorBT `0.28.5` backend executes on the same frozen inputs.
- [x] Signal/event timestamps and barrier outcomes match exactly.
- [x] EMA and return fields agree within the documented numeric tolerance.
- [x] Runtime artifacts and parity report are retained.
- [x] The existing scanner regression suite remains green after the runtime proof.

All Phase 3 gates passed. Phase 4 requires explicit user approval before work
begins.
