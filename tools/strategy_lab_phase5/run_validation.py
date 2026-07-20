"""Run Phase 5 validation against the frozen Phase 3 pandas event log."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.binance_data import (  # noqa: E402
    DownloadSpec,
    TrustedKlineStore,
    _atomic_write,
    _pretty_json_bytes,
)
from perpscanner.strategy_lab.event_study import (  # noqa: E402
    load_contract,
    load_normalized_bars,
)
from perpscanner.strategy_lab.validation import run_validation_pipeline  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    contract_path = (
        repo
        / "docs"
        / "strategy_lab"
        / "examples"
        / "btcusdt_ema9_bounce.event-study.json"
    )
    events_path = (
        repo
        / "tools"
        / "strategy_lab_phase3"
        / "artifacts"
        / "pandas"
        / "events.csv"
    )
    phase3_manifest_path = events_path.parent / "run-manifest.json"
    contract = load_contract(contract_path)
    phase3_manifest = json.loads(
        phase3_manifest_path.read_text(encoding="utf-8")
    )
    if sha256_file(events_path) != phase3_manifest["events_sha256"]:
        raise AssertionError("Phase 3 event log checksum differs from its manifest")
    if phase3_manifest["holdout_policy"] != "final holdout outcomes hidden":
        raise AssertionError("Phase 3 event log is not holdout-safe")

    data_root = repo / "data" / "strategy_lab"
    spec = DownloadSpec.create(
        symbol="BTCUSDT",
        interval="5m",
        start=contract["data"]["start"],
        end=contract["data"]["end"],
    )
    price_store = TrustedKlineStore(data_root, spec)
    data_manifest = price_store.verify_manifest()
    bars = load_normalized_bars(price_store.normalized_path)
    events = pd.read_csv(events_path)
    report, ledger = run_validation_pipeline(events, bars, contract)

    report_path = output / "validation-report.json"
    ledger_path = output / "multiple-testing.csv"
    folds_path = output / "walk-forward-folds.csv"
    holdout_path = output / "holdout-lock.json"
    adversarial_path = output / "adversarial-checks.json"
    manifest_path = output / "run-manifest.json"
    _atomic_write(report_path, _pretty_json_bytes(report))
    ledger.to_csv(ledger_path, index=False, lineterminator="\n")
    fold_rows = []
    for fold in report["walk_forward"]["folds"]:
        fold_rows.append(
            {
                "fold_id": fold["fold_id"],
                "training_end_bar_exclusive": fold[
                    "training_end_bar_exclusive"
                ],
                "validation_start_bar_inclusive": fold[
                    "validation_start_bar_inclusive"
                ],
                "validation_end_bar_exclusive": fold[
                    "validation_end_bar_exclusive"
                ],
                "purged_training_events": fold["purged_training_events"],
                "embargoed_validation_events": fold[
                    "embargoed_validation_events"
                ],
                "training_event_count": fold["training"]["event_count"],
                "validation_event_count": fold["validation"]["event_count"],
                "training_mean_net_return": fold["training"]["mean_net_return"],
                "validation_mean_net_return": fold["validation"][
                    "mean_net_return"
                ],
            }
        )
    pd.DataFrame(fold_rows).to_csv(
        folds_path,
        index=False,
        lineterminator="\n",
    )
    _atomic_write(holdout_path, _pretty_json_bytes(report["holdout"]))
    _atomic_write(
        adversarial_path,
        _pretty_json_bytes(report["adversarial_checks"]),
    )
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": 5,
        "status": report["status"],
        "contract_file": str(contract_path),
        "contract_sha256": sha256_file(contract_path),
        "phase3_events_file": str(events_path),
        "phase3_events_sha256": sha256_file(events_path),
        "phase3_manifest_file": str(phase3_manifest_path),
        "phase3_manifest_sha256": sha256_file(phase3_manifest_path),
        "data_manifest_file": str(price_store.manifest_path),
        "data_manifest_sha256": sha256_file(price_store.manifest_path),
        "data_quality_status": data_manifest["quality"]["status"],
        "holdout_opened": report["holdout"]["holdout_opened"],
        "random_seed": int(contract["validation"]["random_seed"]),
        "pandas_version": version("pandas"),
        "numpy_version": version("numpy"),
        "artifacts": {
            path.name: sha256_file(path)
            for path in (
                report_path,
                ledger_path,
                folds_path,
                holdout_path,
                adversarial_path,
            )
        },
    }
    _atomic_write(manifest_path, _pretty_json_bytes(manifest))
    print(json.dumps(
        {
            "status": report["status"],
            "validation_conclusion": report["validation_conclusion"],
            "claim_level": report["claim_level"],
            "development_event_count": report["holdout"][
                "development_event_count"
            ],
            "hidden_holdout_event_count": report["holdout"][
                "hidden_holdout_event_count"
            ],
            "walk_forward_folds": report["walk_forward"]["fold_count"],
            "adversarial_checks_rejected": report["adversarial_checks"][
                "rejected_check_count"
            ],
            "corrected_hypotheses_rejected": report["multiple_testing"][
                "rejected_hypothesis_count"
            ],
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
