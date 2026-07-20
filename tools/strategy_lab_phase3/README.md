# Strategy Lab Phase 3 One EMA Event Study

Phase 3 executes only the frozen reference contract:

```text
docs/strategy_lab/examples/btcusdt_ema9_bounce.event-study.json
```

It does not search alternative moving-average lengths, filters, targets,
timeframes, or strategy parameters.

## Inputs

- Trusted two-year BTCUSDT 5m dataset from Phase 2.
- Trusted Binance historical funding records.
- Six checksummed 1m windows used only where a 5m candle first touches both the
  target and stop.
- Frozen contract and JSON Schema.

Prepare or offline-verify the inputs:

```powershell
python tools\strategy_lab_phase3\prepare_inputs.py
```

## Runtime Proof

Start Docker Desktop, wait for the engine, then run from this directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_phase3.ps1
```

The proof runs the study twice in the pinned VectorBT image:

1. Pandas EWM reference backend.
2. VectorBT `0.28.5` EMA backend.
3. Exact event/timestamp/status comparison and numeric-tolerance comparison.

Both runs use the same immutable data and funding manifests. Containers have no
network access, publish no ports, and are removed after execution.

## Research Guardrails

- Signals use closed bars only.
- Entry is the next bar open.
- Final-holdout outcomes remain hidden.
- Overlap and cooldown exclusions are counted.
- Same-5m target/stop hits use prepared 1m replay.
- Same-1m target/stop hits remain unresolved.
- Success rate is labeled a historical empirical rate, not a future
  probability.
- No parameter comparison or optimization occurs in Phase 3.
