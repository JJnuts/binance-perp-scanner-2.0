# Strategy Lab Phase 7 Proof

Phase 7 translates allowlisted research language into a non-executable contract
draft. Missing material decisions become grouped questions. Confirmation is
bound to the exact reviewed contract checksum.

The current capability remains deliberately narrow: BTCUSDT, Binance USDT-M,
5-minute EMA9 long-bounce event studies. Safe numeric assumptions may be revised
through structured answers, but unsupported markets, timeframes, indicators,
entry timing, optimization, live trading, holdout access, and arbitrary code are
rejected.

Run from Windows PowerShell after starting Docker Desktop:

```powershell
cd "C:\Users\mkpc\Documents\Codex\2026-04-26\i-asked-cloud-sonnet-4-6\binance-perp-scanner\tools\strategy_lab_phase7"
powershell -ExecutionPolicy Bypass -File .\run_phase7.ps1
```

The proof is offline and capped at one CPU, 768 MB RAM, and 128 processes.
