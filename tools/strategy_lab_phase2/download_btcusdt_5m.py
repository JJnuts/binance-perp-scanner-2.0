"""Download or resume the Phase 2 BTCUSDT two-year 5-minute dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.binance_data import (
    BinanceKlineClient,
    DownloadSpec,
    TrustedKlineStore,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-07-01T00:00:00Z")
    parser.add_argument("--end", default="2026-07-01T00:00:00Z")
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "strategy_lab")
    parser.add_argument("--page-limit", type=int, default=1500)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-manifest", action="store_true")
    args = parser.parse_args()

    spec = DownloadSpec.create(
        symbol="BTCUSDT",
        interval="5m",
        start=args.start,
        end=args.end,
    )
    store = TrustedKlineStore(args.root, spec, page_limit=args.page_limit)
    if args.verify_only:
        result = store.verify_manifest()
    else:
        result = store.download(
            BinanceKlineClient(),
            max_pages=args.max_pages,
            progress=lambda page: print(
                f"page {page['index'] + 1}: {page['row_count']} rows "
                f"through {page['last_open_time_ms']}"
            ),
        )
    if args.print_manifest:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result.get("download_complete") is False:
        print(
            f"Paused after {len(result['pages'])} pages; "
            f"resume cursor: {result['next_start_ms']}"
        )
    else:
        quality = result["quality"]
        print(
            f"Phase 2 data {result['status']}: "
            f"{quality['normalized_row_count']} rows, "
            f"{quality['gap_count']} gaps, "
            f"{quality['duplicate_count']} duplicates"
        )
        print(f"Manifest: {store.manifest_path}")


if __name__ == "__main__":
    main()
