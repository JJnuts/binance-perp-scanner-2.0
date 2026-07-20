# Strategy Lab Phase 5 Validation Pipeline

Phase 5 validates the frozen Phase 3 event study without opening final-holdout
outcomes or searching alternative parameters.

## Gates

- Exact chronological train, validation, and final-holdout labels.
- Two anchored walk-forward validation folds covering the contract's validation
  block.
- Twelve-bar purge and embargo boundaries.
- Strict delay-1 entry and pre-decision feature availability.
- Truncated-history recursive EMA parity.
- Newey-West uncertainty and effective sample size.
- Deterministic moving-block bootstrap intervals.
- Benjamini-Hochberg correction across all four declared horizons.
- Subperiod, regime, and validation-fold stability.
- Deliberately bad leakage, recursive-indicator, uncorrected-testing, and
  exposed-holdout fixtures must all fail.

The final 20% holdout remains locked. Phase 5 cannot promote the strategy to a
holdout-tested or deployment claim.

## Runtime Proof

Start Docker Desktop, then run:

```powershell
cd "C:\Users\mkpc\Documents\Codex\2026-04-26\i-asked-cloud-sonnet-4-6\binance-perp-scanner\tools\strategy_lab_phase5"
powershell -ExecutionPolicy Bypass -File .\run_phase5.ps1
```

The proof is offline and sequential, with one CPU, 768 MB RAM, and a
128-process limit. Artifacts are written under `artifacts/`.
