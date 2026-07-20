# Strategy Lab Risk Register

Ratings are qualitative for Phase 0 and must be revisited when evidence changes.

| ID | Risk | Likelihood | Impact | Required mitigation | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| R-01 | Natural-language ambiguity changes the tested idea | High | Critical | Strict contract, visible defaults, human confirmation, immutable revisions | Prompt fixtures compile to expected contracts |
| R-02 | Look-ahead through candle fields or indicators | Medium | Critical | Closed bars, delay-1 entry, shifted features, deliberate leakage tests | Bad fixture fails; clean fixture has exact timestamps |
| R-03 | Same-candle target/stop ordering is unknowable | High | High | Lower-timeframe replay, unresolved count, conservative fallback | Barrier-order fixture passes |
| R-04 | Data gaps, duplicates, or incomplete history bias results | Medium | High | Manifest, checksums, coverage report, gap/duplicate gates | Two-year download audit passes |
| R-05 | Survivorship and listing-date bias affect universe studies | High | High | Point-in-time universe metadata and coverage filters | Required before multi-symbol ranking |
| R-06 | Fees, slippage, funding, tick size, or rounding are wrong | Medium | Critical | Explicit contract inputs and reconciliation tests | Gross-to-net ledger matches fixture |
| R-07 | Too many parameter trials produce a false winner | High | Critical | Full search log, correction, train/validation/final split | Winner trace includes all candidates |
| R-08 | Reusing final holdout turns it into training data | High | Critical | Holdout lock and one-time opening policy | Audit log proves access sequence |
| R-09 | Regime dependence makes aggregate success misleading | High | High | Subperiod, volatility, and trend-regime breakdowns | Stability report accompanies result |
| R-10 | Events overlap and inflate effective sample size | High | High | Cooldown, overlap policy, effective sample reporting | Overlap fixture and counts pass |
| R-11 | VectorBT and Freqtrade model behavior differently | Medium | High | Cross-engine parity on shared subset, document mismatches | Counts/timestamps reconcile |
| R-12 | Framework or dependency updates change results | Medium | High | Pin versions/images and store environment manifest | Clean pinned rerun reproduces |
| R-13 | AI emits unsupported or unsafe executable code | Medium | Critical | Allowlisted compiler, no arbitrary execution, isolated worker | Adversarial prompt suite is rejected |
| R-14 | AI explanation overstates evidence | High | High | Fixed terminology, claim levels, visible warnings | Report snapshots pass review |
| R-15 | Indicator warmup or recursive behavior changes past signals | Medium | High | Warmup rules and truncated-data recursive analysis | Full/truncated signal parity passes |
| R-16 | Optimization overfits targets, stops, and filters | High | Critical | Bounded search, nested selection discipline, locked holdout | Selection audit is reproducible |
| R-17 | Randomized statistics are not reproducible | Low | Medium | Stored seed and deterministic settings | Same-run interval estimates reproduce |
| R-18 | Streamlit reruns duplicate long jobs | Medium | High | Persisted job IDs, idempotent submission, external worker | Double-click/rerun test creates one job |
| R-19 | Large data/jobs exhaust memory, disk, or CPU | Medium | High | Limits, batching, cancellation, cache quotas | Stress and cancellation tests pass |
| R-20 | Partial engine failure is mistaken for a complete result | Medium | High | Atomic status transition and artifact completeness gate | Kill/restart test remains failed/recoverable |
| R-21 | Existing scanner behavior regresses | Low | High | Isolated modules/page, regression tests, additive rollout | Existing test suite and page smoke tests pass |
| R-22 | Research system accidentally reaches live trading | Low | Critical | No trading credentials, no live mode, network/API allowlist | Configuration and security review pass |
| R-23 | Exchange API revisions or rate limits alter collection | Medium | Medium | Versioned downloader, retry/backoff, raw response metadata | Interrupted download resumes consistently |
| R-24 | Historical empirical rate is read as future probability | High | High | Mandatory wording and confidence/sample context | UI/export wording test passes |

## Risk Handling Rules

- `Critical` impact risks block advancement until mitigations are implemented and
  tested in the relevant phase.
- Accepted residual risks must name an owner, rationale, review date, and affected
  claims.
- A failed accuracy gate cannot be converted into a warning merely to complete a
  phase.
- New features require a risk review before implementation, not after release.
- Live execution remains prohibited even if a future backtest looks strong.

## Phase 1 Priority Risks

The framework proof must address these first:

- R-11: engine-model differences.
- R-12: dependency and version drift.
- R-13: unsafe executable generation.
- R-19: resource use on the local machine.
- R-22: accidental live-trading access.

## Phase 5 Validation Evidence

- R-02: delay-1, feature-availability, and deliberate future-feature tests pass.
- R-07 and R-16: all declared hypotheses are logged and corrected with
  Benjamini-Hochberg; no parameter search is performed.
- R-08: 1,604 final-holdout event outcomes remain absent and unopened.
- R-09: validation-fold, subperiod, and regime stability are reported.
- R-10: Newey-West uncertainty and effective sample size accompany point
  estimates.
- R-15: full-history EMA values match truncated-history recomputation at every
  checkpoint; a future-shifted indicator fails.
- R-17: bootstrap samples and random seeds are fixed and reproducible.

## Phase 6 Reporting Evidence

- R-08: exports fail closed if the final holdout is marked opened.
- R-12 and R-17: the export manifest fixes every upstream checksum and a clean
  second build must reproduce every export byte-for-byte.
- R-14: sample, split, costs, exclusions, warnings, claim level, and deployment
  prohibition are mandatory export fields and HTML snapshot labels.
- R-20: incomplete or checksum-inconsistent source chains block export.
- R-24: historical empirical terminology and the future-probability warning are
  mandatory presentation gates.

## Phase 7 Prompt-Engine Evidence

- R-01: unresolved material definitions remain `needs_review` with grouped
  questions and visible proposed values.
- R-02 and R-08: same-bar entry, validation bypass, and holdout-access prompts
  are rejected before a candidate contract exists.
- R-13 and R-22: instruction override, arbitrary code, credentials, and live
  order prompts fail closed; confirmation never authorizes execution.
- R-14: confirmation is bound to the exact candidate checksum and contract diff.
- R-16: optimization and parameter-sweep language is rejected by the current
  capability gate.

## Phase 8 UI Evidence

- R-18: deterministic job IDs plus an atomic claim kept repeated preparation,
  Streamlit reruns, and a fresh UI session to one external worker job.
- R-20: completion required the passed worker summary and all five export files;
  an unsupported confirmed revision remained failed and recoverable.
- R-21: the Lab is lazily imported and returns before scanner market-data
  collection; the existing research logger call remains in the scanner path.
- R-22: the worker inherits no credentials, runs offline in the proof, never
  opens the holdout, and never authorizes live trading.
- R-24: the UI uses the traceable Phase 6 metric labels, sample/split/cost help,
  fixed evidence warning, and exploratory negative conclusion.
- R-19 remains open for Phase 9 stress, cancellation, and quota testing. Phase 8
  only proves its one-CPU, 1-GB reference workflow.

## Phase 9 Stress Evidence

- R-19: the UI state has explicit job and byte quotas; a 6,000-row interrupted
  fixture resumes in bounded pages; cooperative cancellation publishes no
  result; and the proof checks its one-CPU, 1-GB cgroup limit directly.
- R-20: concurrent jobs use isolated directories, injected engine failure stays
  failed, cancellation stays cancelled, and completed artifacts are rehashed
  before display.
- R-21: the full scanner and Strategy Lab regression suite passes after the
  stress hardening.
- R-22: the stress container has no network, inherits no exchange credentials,
  opens no holdout, and never authorizes live trading.
- R-23: the large synthetic downloader proof resumes from its exact checkpoint
  and rejects a corrupted immutable raw page.

The Phase 9 local stress suite passes. Final acceptance of R-19 remains pending
until the user-run Docker report confirms the cgroup limits on Docker Desktop.
