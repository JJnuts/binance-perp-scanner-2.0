# Strategy Lab Phase 6 Proof

Phase 6 converts the trusted Phase 3 and Phase 5 artifacts into a deterministic,
traceable results bundle. It does not open final-holdout outcomes or change the
frozen EMA9 definition.

The bundle contains:

- `results.json`: structured conclusions, labels, warnings, and metric groups.
- `metrics.csv`: one row per metric with sample, split, costs, source checksum,
  JSON pointer, and contract references.
- `report.html`: static human-readable report with no external dependencies.
- `traceability.json`: proof that every metric resolves to a checksummed source.
- `export-manifest.json`: deterministic input and output checksums.
- `reproduction-report.json`: proof that rebuilding from the manifest produced
  byte-identical files.

Run from Windows PowerShell after starting Docker Desktop:

```powershell
cd "C:\Users\mkpc\Documents\Codex\2026-04-26\i-asked-cloud-sonnet-4-6\binance-perp-scanner\tools\strategy_lab_phase6"
powershell -ExecutionPolicy Bypass -File .\run_phase6.ps1
```

The proof is offline and capped at one CPU, 768 MB RAM, and 128 processes.
