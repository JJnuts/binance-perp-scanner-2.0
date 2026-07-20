"""Prepare trusted funding and minimal 1m replay windows for Phase 3."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.binance_data import (  # noqa: E402
    BinanceKlineClient,
    DownloadSpec,
    TrustedKlineStore,
)
from perpscanner.strategy_lab.event_study import (  # noqa: E402
    find_ambiguous_windows,
    load_contract,
    load_normalized_bars,
)
from perpscanner.strategy_lab.funding_data import (  # noqa: E402
    BinanceFundingClient,
    TrustedFundingStore,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "strategy_lab")
    args = parser.parse_args()

    contract_path = (
        REPO_ROOT
        / "docs"
        / "strategy_lab"
        / "examples"
        / "btcusdt_ema9_bounce.event-study.json"
    )
    schema_path = REPO_ROOT / "docs" / "strategy_lab" / "research_contract.schema.json"
    contract = load_contract(contract_path, schema_path)
    start = contract["data"]["start"]
    end = contract["data"]["end"]
    five_minute_spec = DownloadSpec.create(
        symbol="BTCUSDT",
        interval="5m",
        start=start,
        end=end,
    )
    five_minute_store = TrustedKlineStore(args.root, five_minute_spec)
    five_minute_manifest = five_minute_store.verify_manifest()
    bars = load_normalized_bars(five_minute_store.normalized_path)

    client = BinanceKlineClient()
    funding_store = TrustedFundingStore(
        args.root,
        symbol="BTCUSDT",
        start=start,
        end=end,
    )
    funding_manifest = funding_store.download(
        BinanceFundingClient(client.session),
        progress=lambda page: print(
            f"funding page {page['index'] + 1}: {page['row_count']} rows"
        ),
    )

    ambiguous = find_ambiguous_windows(bars, contract, backend="pandas")
    windows = []
    for position, open_ms in enumerate(ambiguous, start=1):
        window_start = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
        window_end = window_start + timedelta(minutes=5)
        spec = DownloadSpec.create(
            symbol="BTCUSDT",
            interval="1m",
            start=window_start,
            end=window_end,
        )
        store = TrustedKlineStore(args.root, spec)
        manifest = store.download(client)
        windows.append(
            {
                "bar_open_time_ms": open_ms,
                "bar_open_time_utc": window_start.isoformat().replace("+00:00", "Z"),
                "manifest_file": str(store.manifest_path),
                "manifest_repo_relative": str(
                    store.manifest_path.relative_to(REPO_ROOT)
                ).replace("\\", "/"),
                "manifest_sha256": sha256_file(store.manifest_path),
                "normalized_file": str(store.normalized_path),
                "normalized_repo_relative": str(
                    store.normalized_path.relative_to(REPO_ROOT)
                ).replace("\\", "/"),
                "normalized_sha256": manifest["files"]["normalized"]["sha256"],
                "row_count": manifest["files"]["normalized"]["row_count"],
            }
        )
        print(f"detail window {position}/{len(ambiguous)}: {windows[-1]['bar_open_time_utc']}")

    output_dir = args.root / "experiments" / contract["experiment"]["id"] / "inputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_index_path = output_dir / "detail-index.json"
    detail_index = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract_file": str(contract_path),
        "five_minute_manifest_file": str(five_minute_store.manifest_path),
        "five_minute_manifest_sha256": sha256_file(five_minute_store.manifest_path),
        "funding_manifest_file": str(funding_store.manifest_path),
        "funding_manifest_sha256": sha256_file(funding_store.manifest_path),
        "ambiguous_window_count": len(windows),
        "windows": windows,
    }
    detail_index_path.write_text(
        json.dumps(detail_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Phase 3 inputs prepared: {five_minute_manifest['quality']['normalized_row_count']} "
        f"5m bars, {funding_manifest['quality']['normalized_row_count']} funding rows, "
        f"{len(windows)} detail windows"
    )
    print(f"Detail index: {detail_index_path}")


if __name__ == "__main__":
    main()
