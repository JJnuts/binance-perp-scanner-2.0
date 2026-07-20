# Strategy Lab Phase 1 Status

Status: Complete

Date: 2026-07-16

## Completed

- Audited the host for WSL2, Docker Desktop, architecture, and memory.
- Confirmed 64-bit Windows, 16 logical processors, and 16 GB total RAM.
- Selected VectorBT `0.28.5` for the initial correctness baseline.
- Selected the official Freqtrade `2026.6` Docker image.
- Added an isolated Docker Compose proof harness under
  `tools/strategy_lab_phase1/`.
- Added one shared, checksummed, hand-verified EMA 3 bounce fixture.
- Added two-run reproducibility and cross-engine comparison tooling.
- Added two-CPU and 2 GB per-container resource limits.
- Added explicit no-key, dry-run, no-port, and backtest-only safeguards.
- Compiled every Python file.
- Parsed the PowerShell runner and all JSON/YAML files.
- Ran the VectorBT fixture twice locally with exact repeated results.
- Tested Freqtrade fixture conversion and result parsing against a synthetic
  Freqtrade `2026.6` archive.
- Installed WSL2 and Docker Desktop with the Linux backend.
- Built the VectorBT `0.28.5` image and pulled the official Freqtrade `2026.6`
  image successfully.
- Ran both real container proofs twice with exact repeated canonical results.
- Recorded the Docker environment, image IDs, and repository digests in
  `tools/strategy_lab_phase1/artifacts/environment-manifest.json`.
- Promoted the reviewed VectorBT dependency freeze to
  `tools/strategy_lab_phase1/vectorbt/requirements.lock`.
- Confirmed temporary Compose containers were removed and Docker Desktop was
  stopped after the proof.
- Re-ran the existing scanner regression suite: 63 tests passed.

## Findings

### EMA Warmup Semantics

VectorBT EMA returns `NaN` for the first `window - 1` rows. Plain pandas EMA
does not unless `min_periods` is specified. Both implementations now explicitly
use a three-row warmup for EMA 3.

### Native Return Semantics

VectorBT and Freqtrade use different denominators for their native trade-return
fields:

- VectorBT divides net PnL by entry value before entry fees.
- Freqtrade divides by entry value including entry fees.

The parity report therefore compares prices, fees, and a canonical normalized
net return. It retains both native values and documents why they differ.

### Host Resources

The audit found only 2.3 GB free RAM at that moment because several desktop
applications were open. The runner refuses to begin below 3 GB free RAM and
runs one capped job at a time.

## Runtime Evidence

- Reproducibility report: `status: passed`, two repeated runs.
- Freqtrade image:
  `freqtradeorg/freqtrade@sha256:d451af021d5e08b70580c0eea5848534e9846b57391b34821c0a5814416397e6`.
- VectorBT image:
  `strategy-lab-vectorbt@sha256:41f2b02f2c22ea01f7d229528a11918f30379fd10c58ad4d8cba1c25b5022fed`.
- Docker Desktop `4.82.0`, Engine `29.6.1`, Linux/amd64 through WSL2.

## Phase 1 Acceptance

Phase 1 acceptance:

- [x] The official Freqtrade image and VectorBT image build successfully.
- [x] Both real container proofs pass twice.
- [x] Image IDs and repository digests are recorded.
- [x] The generated VectorBT dependency freeze is reviewed and promoted to a lock.
- [x] Docker containers are confirmed stopped after execution.
- [x] The current scanner remains unaffected.

All Phase 1 gates passed. Phase 2 may begin.
