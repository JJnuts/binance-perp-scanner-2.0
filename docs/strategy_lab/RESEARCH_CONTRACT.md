# Strategy Lab Research Contract

The research contract is the boundary between human/AI interpretation and
deterministic execution. Engines receive a confirmed JSON contract, never the
original chat prompt.

The canonical machine-readable definition is
`research_contract.schema.json`. This document explains its semantics and the
additional checks required before execution.

## 1. Contract Guarantees

Every confirmed contract must be:

- Versioned.
- Strict: unknown properties are rejected.
- Complete enough to reproduce the research.
- Immutable after confirmation.
- Compatible with an allowlisted engine capability.
- Linked to a data and environment manifest when executed.

Schema validity alone is not sufficient. Semantic and capability validation are
separate mandatory steps.

## 2. Root Sections

| Section | Meaning |
| --- | --- |
| `contract_version` | Schema/compiler compatibility version |
| `experiment` | Stable identity, name, hypothesis, and notes |
| `research` | Research type and selected engine |
| `market` | Exchange, venue, symbols, and direction |
| `data` | Timeframe, historical range, source, timing, and warmup |
| `signal` | Indicator, exact event definition, and optional filters |
| `entry` | Executable entry timing and event-overlap policy |
| `outcome` | Forward horizons and/or target-stop barrier definition |
| `execution` | Fees, slippage, funding, and position sizing |
| `validation` | Chronological splits, minimum sample, uncertainty, and seed |
| `reporting` | Required metrics, artifacts, and probability terminology |

## 3. Defining A Moving-Average Bounce

"Price bounced from the 9 EMA" is not executable until each of these is fixed:

- Average type and length: for example EMA 9.
- Input price: for example candle close.
- Approach direction: from above, below, or either.
- Precondition: how many prior closes must remain on the approach side.
- Probe field: low, high, close, or open.
- Touch tolerance: how close the probe must come to the average.
- Maximum penetration: how far price may cross the average and still count.
- Confirmation: where the signal candle must close.
- Rejection distance: optional minimum close distance from the average.
- Entry timing: normally next bar open.
- Success: forward return, target before stop, or both.
- Independence: cooldown and overlapping-position rules.

The reference long-bounce definition is:

1. The previous three closes are above the EMA.
2. The signal candle low reaches the EMA touch band.
3. The signal candle does not penetrate more than the allowed amount.
4. The signal candle closes above the EMA.
5. Entry is evaluated at the next bar open.

This is one defensible definition, not the universal definition of a bounce.
Alternative definitions become separate contract revisions.

## 4. Timing Rules

### Closed Bars

`bar_policy` is fixed to `closed_only`. A bar still forming at retrieval or
execution time is excluded.

### Delay-1 Entry

The default entry is `next_bar_open`. A signal calculated from a candle's close,
high, or low cannot assume execution at an earlier price inside that same
candle.

Same-close entry is not in contract version `0.1.0`. It can be introduced only
with an explicit auction/latency model and independent validation.

### Intrabar Ambiguity

If a higher-timeframe candle hits both target and stop, OHLC data does not reveal
which occurred first. The contract must choose:

- `lower_timeframe_replay`: resolve using lower-timeframe bars.
- `unresolved`: exclude the ambiguous event from the resolved success rate and
  report it separately.
- `stop_first`: conservative fallback.
- `target_first`: optimistic and prohibited for promoted results unless the
  actual execution ordering is independently known.

The reference contract requests 1-minute replay and falls back to `unresolved`.

## 5. Outcomes And Probability Language

`forward_return` reports return distributions at fixed future horizons.

`barrier` reports whether target or stop was reached first within a maximum
holding period.

`both` reports both views.

The UI may display:

> Historical empirical success rate: 57.2% (143 successes / 250 resolved
> events), with a 95% confidence interval.

It must not shorten this to "57.2% probability of a bounce." The historical rate
is conditional on the tested sample and definition and is not automatically a
future probability.

Unresolved events, overlapping events, excluded data, and insufficient-warmup
bars must be counted explicitly.

## 6. Costs And Funding

The contract records:

- Fee basis points per side.
- Slippage basis points per side.
- Whether historical funding is included.
- The funding source.
- Position-size model.

Reports distinguish gross and net returns. A zero-cost research run is allowed
only when explicitly requested and visibly labeled. Strategy ranking and final
claims use net metrics unless the user is specifically studying gross price
behavior.

## 7. Validation Splits

Supported methods:

- `anchored_walk_forward`
- `rolling_walk_forward`
- `chronological_holdout`
- `exploratory_only`

`exploratory_only` can help define a hypothesis but cannot support a promoted
strategy claim.

The final holdout:

- Is chronological.
- Is locked before parameter selection.
- Is not shown during iterative filter development.
- Can be opened once for the final candidate revision.
- Requires a new future sample after it has influenced subsequent decisions.

## 8. Multiple Testing

Searching many EMA/SMA lengths, timeframes, symbols, filters, targets, or stops
creates multiple-comparison risk. The contract records the correction method and
the report records the full search space, not only the winner.

Default policy:

- Rank candidates using training data.
- Confirm the shortlist on validation data.
- Evaluate one frozen candidate on the final holdout.
- Report false-discovery-aware statistics for broad searches.

## 9. Semantic Validation

The runtime validator must check rules JSON Schema cannot fully express:

- `start` is earlier than `end`.
- Split fractions total 1.0 within numeric tolerance.
- Detail timeframe is lower than the research timeframe.
- Warmup is sufficient for every indicator.
- Purge and embargo cover the maximum outcome horizon where needed.
- Target, stop, horizon, and position-size values are economically meaningful.
- Requested symbol and interval exist for the full requested period, or missing
  coverage is disclosed.
- Long-bounce definitions use coherent approach, probe, and confirmation fields.
- Metrics are compatible with the research type.
- Final-holdout settings are compatible with the selected validation method.
- Funding data coverage matches the position timestamps when funding is enabled.

## 10. Capability Validation

Each adapter publishes supported:

- Contract versions.
- Research types.
- Exchanges and venues.
- Timeframes.
- Indicators and filters.
- Entry and outcome models.
- Cost and funding models.
- Validation methods.

Unsupported combinations are rejected before downloading a large dataset or
starting an engine.

## 11. Required Run Artifacts

Every completed run contains:

- Confirmed contract JSON and its SHA-256 checksum.
- Parent experiment/revision identifier.
- Raw-data manifest and checksums.
- Data-quality report.
- Compiler and engine versions.
- Dependency or container image lock.
- Timezone and numeric precision.
- Random seed where sampling is used.
- Results table and trade/event-level output.
- Warnings, exclusions, and unresolved-event counts.
- Validation-gate results.
- Human-readable report.

