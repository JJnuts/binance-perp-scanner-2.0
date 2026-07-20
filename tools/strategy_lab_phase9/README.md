# Strategy Lab Phase 9 Proof

Phase 9 is the final framework stress test. It runs offline in the already pinned
Phase 8 Streamlit image and is limited to one CPU, 1 GB RAM, and 128 processes.
It does not download market data, inherit exchange credentials, open the final
holdout, or authorize live trading.

The proof runs these gates sequentially to keep memory pressure low:

- interruption and exact resume of a 6,000-row synthetic kline fixture;
- two external workers active together with isolated job directories;
- cooperative cancellation and recovery after a new Streamlit session;
- injected engine failure with no published result;
- corrupt market cache and corrupt persisted UI state rejection;
- byte-identical reproduction of the Phase 6 reference bundle;
- direct verification of the container CPU and memory limits.

## Run on Windows

Start Docker Desktop and wait for **Engine running**, then use PowerShell:

```powershell
cd "C:\Users\mkpc\Documents\Codex\2026-04-26\i-asked-cloud-sonnet-4-6\binance-perp-scanner\tools\strategy_lab_phase9"
powershell -ExecutionPolicy Bypass -File .\run_phase9.ps1
```

Success ends with `Phase 9 runtime proof passed.` The retained reviewer evidence
is written to `artifacts\phase9-proof-report.json` and
`artifacts\run-manifest.json`.

If the Phase 8 image is missing, the script prints the exact one-time rebuild
command. Phase 9 deliberately reuses that image instead of adding another large
Docker image.
