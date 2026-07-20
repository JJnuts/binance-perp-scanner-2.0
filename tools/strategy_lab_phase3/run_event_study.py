"""Execute the frozen Phase 3 study and write reproducibility artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


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
    DetailReplayResolver,
    load_contract,
    load_normalized_bars,
    run_event_study,
    run_manifest,
)
from perpscanner.strategy_lab.funding_data import TrustedFundingStore  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("pandas", "vectorbt"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    data_root = repo / "data" / "strategy_lab"
    contract_path = (
        repo
        / "docs"
        / "strategy_lab"
        / "examples"
        / "btcusdt_ema9_bounce.event-study.json"
    )
    schema_path = repo / "docs" / "strategy_lab" / "research_contract.schema.json"
    contract = load_contract(contract_path)
    spec = DownloadSpec.create(
        symbol="BTCUSDT",
        interval="5m",
        start=contract["data"]["start"],
        end=contract["data"]["end"],
    )
    price_store = TrustedKlineStore(data_root, spec)
    data_manifest = price_store.verify_manifest()
    bars = load_normalized_bars(price_store.normalized_path)
    funding_store = TrustedFundingStore(
        data_root,
        symbol="BTCUSDT",
        start=contract["data"]["start"],
        end=contract["data"]["end"],
    )
    funding_manifest = funding_store.verify_manifest()
    funding = funding_store.load_frame()
    detail_index = (
        data_root
        / "experiments"
        / contract["experiment"]["id"]
        / "inputs"
        / "detail-index.json"
    )
    resolver = DetailReplayResolver(detail_index, repo_root=repo)
    events, summary = run_event_study(
        bars,
        contract,
        backend=args.backend,
        funding=funding,
        detail_resolver=resolver,
    )
    summary["warnings"] = [
        "Success rate is a historical empirical rate, not a future probability.",
        "Final-holdout outcomes remain hidden under the frozen contract.",
        "Two-sided hits unresolved within the same 1m candle remain excluded from resolved rates.",
        "No parameter comparison or strategy optimization was performed.",
    ]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    events_path = output / "events.csv"
    summary_path = output / "summary.json"
    manifest_path = output / "run-manifest.json"
    events.to_csv(events_path, index=False, lineterminator="\n")
    _atomic_write(summary_path, _pretty_json_bytes(summary))
    manifest = run_manifest(
        contract_path=contract_path,
        data_manifest_path=price_store.manifest_path,
        funding_manifest_path=funding_store.manifest_path,
        backend=args.backend,
        summary=summary,
    )
    manifest.update(
        {
            "schema_file": str(schema_path),
            "schema_sha256": sha256_file(schema_path),
            "detail_index_file": str(detail_index),
            "detail_index_sha256": sha256_file(detail_index),
            "events_file": str(events_path),
            "events_sha256": sha256_file(events_path),
            "summary_file": str(summary_path),
            "summary_sha256": sha256_file(summary_path),
            "data_quality_status": data_manifest["quality"]["status"],
            "funding_quality_status": funding_manifest["quality"]["status"],
        }
    )
    _atomic_write(manifest_path, _pretty_json_bytes(manifest))
    print(
        f"{args.backend} Phase 3 passed: {summary['development_event_count']} "
        f"development events, {summary['resolved_event_count']} resolved"
    )
    print(json.dumps(summary["barrier_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
