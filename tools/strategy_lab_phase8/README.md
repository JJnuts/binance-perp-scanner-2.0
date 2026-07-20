# Strategy Lab Phase 8 Proof

Phase 8 adds a Strategy Lab page to the existing Streamlit application while
keeping research jobs outside Streamlit reruns. Drafts, exact confirmations,
jobs, failures, results, and artifact hashes are persisted under
`data/strategy_lab/ui` during normal use.

The proof drives the real page with Streamlit's application test API. It also
starts the real external worker, verifies immediate non-blocking submission,
prevents duplicate work on rerun, recovers completed and failed states from a
new store instance, and renders the Phase 6 result bundle as traceable metrics.

Start Docker Desktop, then run from Windows PowerShell:

```powershell
cd "C:\Users\mkpc\Documents\Codex\2026-04-26\i-asked-cloud-sonnet-4-6\binance-perp-scanner\tools\strategy_lab_phase8"
powershell -ExecutionPolicy Bypass -File .\run_phase8.ps1
```

The one-time image build needs internet access to install the pinned Streamlit
runtime. The proof itself is offline and capped at one CPU, 1 GB RAM, and 128
processes. It never receives credentials, never opens the final holdout, and
never authorizes live trading.
