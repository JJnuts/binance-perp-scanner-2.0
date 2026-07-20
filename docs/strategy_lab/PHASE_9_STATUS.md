# Strategy Lab Phase 9 Status

Status: Ready for Docker verification

Date: 2026-07-17

## Scope

Phase 9 is the final framework stress phase. It hardens and tests the existing
offline reference workflow; it does not add strategy selection, open the final
holdout, authorize deployment, or connect the Lab to live trading.

## Implemented

- A 6,000-row deterministic kline fixture that is interrupted after seven pages
  and must resume from the exact persisted cursor.
- Concurrent external workers for two confirmed contracts, with separate state,
  logs, artifacts, and terminal outcomes.
- Cooperative cancellation with persisted requests, worker heartbeats, a
  terminal cancelled state, safe retry, and Streamlit restart recovery.
- Test-only engine-failure injection that must remain failed and cannot publish
  a completed result.
- Fail-closed corruption checks for raw market cache, persisted job state, result
  summaries, and completed export hashes.
- Bounded UI state with a 100-job and 512-MB default quota; active work is never
  silently deleted to make space.
- Byte-identical reviewer reproduction against both a supplied manifest and the
  retained Phase 6 reference bundle.
- A Docker proof that verifies its cgroup limit directly and runs offline with
  one CPU, 1 GB RAM, a read-only repository, and 128-process maximum.
- Reuse of the pinned Phase 8 image so Phase 9 adds no extra large Docker image.

## Local Validation Evidence

- Phase 9 stress integration test: passed.
- Cooperative cancellation recovered as cancelled after a new Streamlit
  session and published no result.
- Concurrent reference and revised-contract jobs were observed active together;
  the reference completed and the unsupported revision failed without sharing
  artifacts.
- Injected engine failure remained failed after store restart.
- Corrupt data and corrupt UI state were rejected without an uncaught Streamlit
  exception.
- Reviewer outputs were byte-identical across two builds and to the Phase 6
  baseline.
- Full scanner and Strategy Lab regression suite: 137 passed.
- Final-holdout access: false; live-trading authorization: false.

## Remaining Verification Gate

Run `tools/strategy_lab_phase9/run_phase9.ps1` with Docker Desktop. Phase 9 can
be marked complete after the container prints `Phase 9 runtime proof passed.`
The retained evidence must include `phase9-proof-report.json` and
`run-manifest.json` under `tools/strategy_lab_phase9/artifacts`.
