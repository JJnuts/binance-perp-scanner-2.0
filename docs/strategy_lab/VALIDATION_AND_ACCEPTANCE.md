# Strategy Lab Validation And Acceptance

Accuracy is handled through explicit gates. A run may complete technically while
still failing research acceptance.

## 1. Validation Layers

### Gate A: Contract

- JSON is valid against the exact contract schema version.
- No unknown fields are present.
- Semantic checks pass.
- The user-confirmed contract checksum matches the executed contract.
- Engine capabilities support every requested feature.

Failure result: do not execute.

### Gate B: Data

- Source is the declared Binance public endpoint or approved archive.
- Timestamps are UTC, unique, ordered, and aligned to the requested interval.
- The current unclosed bar is absent.
- Duplicate and gap counts are reported.
- Requested and actual coverage are compared.
- Raw files and normalized files have checksums.
- Derived indicators can be rebuilt from the normalized bars.
- Funding coverage is checked when enabled.

Failure result: reject the run or mark missing coverage explicitly before the
user confirms a narrower study.

### Gate C: Calculation

- EMA/SMA values match an independent reference implementation.
- Signal timestamps match hand-verified fixture rows.
- No signal uses future OHLCV or future indicator values.
- Entry timing matches the contract.
- Warmup bars cannot create events.
- Cooldown and overlap rules are deterministic.
- Barrier ordering follows lower-timeframe and fallback rules.
- Gross-to-net calculations reconcile with fees, slippage, and funding.

Failure result: engine adapter is not trusted.

### Gate D: Statistical

- Chronological splits are respected.
- The final holdout remains hidden during selection.
- Sample size and effective independent sample size are reported.
- Rates include numerator, denominator, unresolved count, and interval estimate.
- Return metrics include uncertainty, not only point estimates.
- Multiple testing is disclosed and corrected as specified.
- Regime and subperiod stability are shown.
- A low-sample or unstable result is labeled inconclusive.

Failure result: results may be shown as exploratory but cannot be promoted.

### Gate E: Reproducibility

- Same contract, data manifest, engine image/version, and seed reproduce the
  result within tolerance.
- A second clean environment can run the reference fixture.
- Run artifacts are complete and immutable.
- Differences across VectorBT, Freqtrade, and the reference calculator are
  explained within documented tolerances.

Failure result: do not treat the result as reliable.

### Gate F: Presentation

- Gross and net metrics are clearly separated.
- "Probability" is labeled as a historical empirical rate unless a separately
  validated probabilistic model exists.
- In-sample, validation, and final-holdout results cannot be visually confused.
- Warnings and failed gates remain visible in exports.
- The UI does not recommend deployment merely because a metric is positive.

Failure result: block final export as an accepted result.

## 2. Reference Fixture Tests

Before broad research, the system must pass a small deterministic fixture with
known candles and expected outputs:

- Exact EMA and SMA values after warmup.
- Touch without penetration.
- Allowed and disallowed penetration.
- Close-confirmed and failed bounces.
- Signal on the final closed bar.
- Exclusion of an unclosed bar.
- Next-bar-open entry.
- Target first, stop first, both in one bar, and neither.
- Lower-timeframe resolution and unresolved fallback.
- Duplicate timestamp and missing-bar detection.
- Fee/slippage/funding reconciliation.
- Overlap and cooldown behavior.

The expected rows should be checked by hand and committed as test fixtures.

## 3. Cross-Engine Parity

For a simple strategy expressible in both engines:

- Use the same normalized candles.
- Use the same indicator implementation or verified equivalent.
- Use the same signal timestamps.
- Use the same next-bar entry rule.
- Disable features one engine cannot model identically.
- Compare event/trade counts and timestamps first.
- Compare returns only after lifecycle parity passes.

Initial tolerances:

- Signal/event count: exact.
- Signal timestamps: exact.
- Entry timestamps: exact.
- Indicator values: `1e-10` relative tolerance where numeric libraries allow.
- Return differences: explained to the smallest price/tick/fee rounding unit.

Tolerances may be revised only with a documented reason and a regression test.

## 4. Strategy Claim Levels

| Level | Meaning | Minimum requirement |
| --- | --- | --- |
| Draft | Definitions are incomplete | Contract review only |
| Exploratory | Historical pattern observed | Data and calculation gates pass |
| Validated | Pattern survives validation | Statistical gate and locked validation pass |
| Holdout tested | Frozen candidate tested once | Final holdout and reproducibility pass |
| Deployment candidate | Operational review completed | Outside current Strategy Lab scope |

No result in the planned phases automatically becomes a deployment candidate.

## 5. Phase Acceptance Gates

### Phase 0: Design

- Specification, contract documentation, JSON Schema, example, validation plan,
  and risk register exist.
- JSON files parse.
- Example validates against the schema.
- Only new documentation artifacts are added.

### Phase 1: Framework Proof

- Pinned VectorBT environment runs a deterministic fixture.
- Pinned Freqtrade environment runs a deterministic fixture in isolation.
- Exact engine versions and platform instructions are recorded.
- No live-trading configuration or credentials exist.
- A clean re-run reproduces both fixtures.

### Phase 2: Trusted Binance Data

- Paginated two-year 5-minute download is complete and restartable.
- Closed-bar, UTC, duplicate, gap, and checksum tests pass.
- Raw and normalized layers are separated.
- Data manifest is sufficient to reconstruct the dataset.

### Phase 3: One EMA Event Study

- Reference BTCUSDT EMA 9 contract executes.
- Hand fixture and independent reference calculator agree.
- Intrabar ambiguity and overlap counts are visible.
- Results include sample size, confidence intervals, and regime/subperiod views.
- No comparison search begins until this single definition is trusted.

### Phase 4: Freqtrade Adapter

- A fixed approved strategy compiles without arbitrary code generation.
- Trade timestamps reconcile against an independent expectation.
- Fees, slippage, funding, and position sizing reconcile.
- Worker isolation and cancellation are tested.

### Phase 5: Validation Pipeline

- Chronological walk-forward and locked final holdout are enforced.
- Leakage, recursive-indicator, and multiple-testing tests fail when deliberately
  given bad input.
- Stability and uncertainty reporting are complete.

### Phase 6: Results And Exports

- Every metric can be traced to contract and run artifacts.
- Reports label sample, split, costs, exclusions, and warnings.
- Re-running an export manifest reproduces the result.

### Phase 7: Prompt Engine

- Ambiguous prompts generate questions rather than silent assumptions.
- Contract diffs accurately reflect requested revisions.
- Adversarial prompts cannot bypass capability or safety validation.
- A reviewed prompt suite compiles to expected contracts.

### Phase 8: Streamlit UI

- Long jobs do not block or duplicate on rerun.
- Draft, confirmed, running, failed, and completed states are recoverable.
- Desktop and narrow layouts show metrics and warnings without overlap.
- The existing scanner pages and research logging remain unchanged.

### Phase 9: Full Stress Test

- Large downloads resume after interruption.
- Concurrent jobs remain isolated.
- Resource limits, cancellation, corrupt cache, engine failure, and app restart
  scenarios are tested.
- A fresh reviewer can reproduce the reference result from documentation.

## 6. Mandatory Review Before Advancing A Phase

At each phase boundary:

1. Review the implementation diff for unrelated changes.
2. Run the phase-specific tests and retain their outputs.
3. Re-run the previous phase's critical regression tests.
4. Resolve or explicitly accept every new risk.
5. Record dependency and design decisions.
6. Confirm that no later-phase feature was smuggled into the current scope.
7. Obtain explicit approval before starting the next phase.

Additional tests should be proposed only when they address a concrete accuracy,
reproducibility, safety, or operability risk.

