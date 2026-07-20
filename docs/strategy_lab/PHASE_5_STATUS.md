# Strategy Lab Phase 5 Status

Status: Complete

Date: 2026-07-17

## Scope

Phase 5 validates the single frozen Phase 3 definition. It does not search
alternative EMA lengths, filters, targets, stops, symbols, or timeframes, and it
does not open final-holdout outcomes.

## Implemented

- Chronological split and hidden-outcome enforcement.
- Two-fold anchored walk-forward validation over the declared validation block.
- Twelve-bar purge and embargo enforcement.
- Delay-1 and feature-availability leakage gates.
- Truncated-history recursive-indicator checks.
- Newey-West uncertainty and effective sample size.
- Deterministic moving-block bootstrap intervals.
- Benjamini-Hochberg correction with a complete hypothesis ledger.
- Validation-fold, subperiod, and regime stability reporting.
- Adversarial fixtures that must reject same-bar entry, future features,
  recursive future indicators, uncorrected multiple testing, and exposed
  holdout outcomes.
- An offline, resource-limited Docker proof runner.

## Local Validation Evidence

- Real accepted event log: 8,244 events.
- Development events evaluated: 6,640.
- Final-holdout events still hidden: 1,604.
- Holdout opened: no.
- Anchored walk-forward folds: 2.
- Fold 1 validation mean 12-bar net return: `-0.0013856302316905338`.
- Fold 2 validation mean 12-bar net return: `-0.0011869991806246324`.
- Benjamini-Hochberg hypotheses tested: 4.
- Corrected positive hypotheses at alpha 0.05: 0.
- Adversarial validation checks rejected as intended: 5 of 5.
- Validation conclusion: `negative`.
- Claim level remains: `exploratory`.
- Full scanner and Strategy Lab regression suite: 98 tests passed.

## Docker Runtime Evidence

- Pinned offline Docker proof: passed.
- Runtime limits: 1 CPU, 768 MB RAM, 128 processes.
- Validation pipeline status: `passed`.
- Validation conclusion: `negative`.
- Generated artifacts retained under `tools/strategy_lab_phase5/artifacts`.

Phase 5 is complete. The framework is validated, the frozen EMA9 hypothesis
has negative development evidence, and the final holdout remains unopened.
