"""Build and independently reproduce the Phase 6 results bundle."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.binance_data import (  # noqa: E402
    _atomic_write,
    _pretty_json_bytes,
)
from perpscanner.strategy_lab.reporting import (  # noqa: E402
    EXPECTED_EXPORT_FILES,
    build_export_bundle,
    sha256_file,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--verify-reproduction", action="store_true")
    parser.add_argument("--verification-output", type=Path)
    args = parser.parse_args()

    output = args.output.resolve()
    result = build_export_bundle(
        args.repo_root.resolve(),
        output,
        reproduce_manifest=args.manifest,
    )
    if args.verify_reproduction:
        reproduction = output.parent / ".phase6-reproduction"
        if reproduction.exists():
            shutil.rmtree(reproduction)
        try:
            reproduced = build_export_bundle(
                args.repo_root.resolve(),
                reproduction,
                reproduce_manifest=output / "export-manifest.json",
            )
            files = (*EXPECTED_EXPORT_FILES, "export-manifest.json")
            original_hashes = {
                filename: sha256_file(output / filename) for filename in files
            }
            reproduced_hashes = {
                filename: sha256_file(reproduction / filename) for filename in files
            }
            if original_hashes != reproduced_hashes:
                raise AssertionError("Reproduced export file checksums differ")
            verification = {
                "status": "passed",
                "bundle_id": result["bundle_id"],
                "manifest_sha256": sha256_file(output / "export-manifest.json"),
                "files_verified": original_hashes,
                "byte_identical": True,
                "holdout_opened": False,
                "validation_conclusion": result["validation_conclusion"],
                "claim_level": result["claim_level"],
            }
            if not reproduced["byte_identical_reproduction"]:
                raise AssertionError("Reproduction mode was not activated")
            if args.verification_output is None:
                raise ValueError(
                    "--verification-output is required with --verify-reproduction"
                )
            _atomic_write(
                args.verification_output.resolve(),
                _pretty_json_bytes(verification),
            )
            result["byte_identical_reproduction"] = True
        finally:
            if reproduction.exists():
                shutil.rmtree(reproduction)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
