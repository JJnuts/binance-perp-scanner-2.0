"""Compile the approved Phase 4 contract into safe Freqtrade artifacts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(
    os.environ.get("STRATEGY_LAB_REPO_ROOT", Path(__file__).resolve().parents[2])
)
PHASE4_ROOT = Path(
    os.environ.get("PHASE4_ROOT", Path(__file__).resolve().parent)
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.freqtrade_adapter import (  # noqa: E402
    compile_strategy_contract,
)


def main() -> None:
    root = PHASE4_ROOT
    compiled = compile_strategy_contract(
        contract_path=(
            REPO_ROOT
            / "docs"
            / "strategy_lab"
            / "examples"
            / "btcusdt_ema9_fixed-hold.strategy-backtest.json"
        ),
        schema_path=REPO_ROOT / "docs" / "strategy_lab" / "research_contract.schema.json",
        strategy_path=(
            REPO_ROOT
            / "tools"
            / "strategy_lab_phase4"
            / "freqtrade"
            / "user_data"
            / "strategies"
            / "ApprovedEma9FixedHoldStrategy.py"
        ),
        output_dir=root / "compiled",
    )
    print(f"Compiled contract: {compiled.contract_path}")
    print(f"Safe config: {compiled.config_path}")
    print(f"Manifest: {compiled.manifest_path}")


if __name__ == "__main__":
    main()
