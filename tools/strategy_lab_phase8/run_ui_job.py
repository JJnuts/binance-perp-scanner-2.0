"""Execute one persisted Strategy Lab UI job outside the Streamlit rerun."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.prompt_engine import (  # noqa: E402
    load_reference_prompt_contract,
    validate_contract_schema,
)
from perpscanner.strategy_lab.reporting import build_export_bundle  # noqa: E402
from perpscanner.strategy_lab.ui_jobs import StrategyLabUIStore  # noqa: E402


class Phase8JobCapabilityError(RuntimeError):
    """Raised when the Phase 8 worker cannot safely execute a contract."""


class StrategyLabInjectedEngineError(RuntimeError):
    """Test-only failure used by the final lifecycle stress proof."""


def _cancel_if_requested(
    store: StrategyLabUIStore,
    job_id: str,
    *,
    reason: str,
) -> bool:
    if not store.is_cancel_requested(job_id):
        return False
    store.cancel_job(job_id, reason=reason)
    return True


def execute_job(
    repo_root: Path,
    store_root: Path,
    job_id: str,
    *,
    delay_seconds: float = 0.0,
    fault_stage: str = "none",
) -> bool:
    if fault_stage not in {"none", "before-export", "after-export"}:
        raise ValueError(f"Unsupported fault stage: {fault_stage}")
    store = StrategyLabUIStore(store_root)
    if not store.claim_job(job_id):
        return True
    try:
        if delay_seconds > 0:
            deadline = time.monotonic() + delay_seconds
            while time.monotonic() < deadline:
                if _cancel_if_requested(
                    store,
                    job_id,
                    reason="Cancellation acknowledged during worker delay",
                ):
                    return True
                store.write_heartbeat(job_id, stage="delayed-start")
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        if _cancel_if_requested(
            store,
            job_id,
            reason="Cancellation acknowledged before validation",
        ):
            return True
        store.write_heartbeat(job_id, stage="validating-contract")
        request = store.read_job_request(job_id)
        contract = store.read_job_contract(job_id)
        if request.get("execution_authorized") is not False:
            raise Phase8JobCapabilityError(
                "Job request attempted to authorize execution"
            )
        if request.get("holdout_open_authorized") is not False:
            raise Phase8JobCapabilityError(
                "Job request attempted to authorize holdout access"
            )
        validate_contract_schema(contract)
        if contract != load_reference_prompt_contract():
            raise Phase8JobCapabilityError(
                "Phase 8 executes only the frozen reference contract; "
                "confirmed revisions require a later pipeline capability"
            )
        if fault_stage == "before-export":
            raise StrategyLabInjectedEngineError(
                "Injected engine failure before export"
            )
        store.write_heartbeat(job_id, stage="building-export")
        job_directory = store.job_directory(job_id)
        export_result = build_export_bundle(repo_root, job_directory / "artifacts")
        if fault_stage == "after-export":
            raise StrategyLabInjectedEngineError(
                "Injected engine failure after export"
            )
        if _cancel_if_requested(
            store,
            job_id,
            reason="Cancellation acknowledged before completion",
        ):
            return True
        store.write_heartbeat(job_id, stage="verifying-export")
        results = json.loads(
            (job_directory / "artifacts" / "results.json").read_text(encoding="utf-8")
        )
        conclusion = results["conclusion"]
        if conclusion["promotion_authorized"] is not False:
            raise Phase8JobCapabilityError("Result unexpectedly authorized promotion")
        if conclusion["framework_status"] != "passed":
            raise Phase8JobCapabilityError("Result framework gate did not pass")
        summary = {
            "status": "passed",
            "job_id": job_id,
            "confirmed_contract_sha256": request["confirmed_contract_sha256"],
            "bundle_id": export_result["bundle_id"],
            "validation_conclusion": conclusion["validation_conclusion"],
            "claim_level": conclusion["claim_level"],
            "promotion_authorized": False,
            "holdout_opened": False,
            "network_used": False,
            "credentials_inherited": False,
        }
        store.write_job_result(job_id, summary)
        store.complete_job(job_id)
        return True
    except Exception as exc:
        if store.is_cancel_requested(job_id):
            if store.read_job(job_id)["state"] != "cancelled":
                store.cancel_job(
                    job_id,
                    reason="Cancellation acknowledged after an interrupted stage",
                )
            return True
        store.write_worker_error(job_id, traceback.format_exc())
        store.fail_job(
            job_id,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument(
        "--fault-stage",
        choices=("none", "before-export", "after-export"),
        default="none",
        help="Test-only failure injection used by the Phase 9 stress proof.",
    )
    args = parser.parse_args()
    passed = execute_job(
        args.repo_root.resolve(),
        args.store_root.resolve(),
        args.job_id,
        delay_seconds=max(0.0, args.delay_seconds),
        fault_stage=args.fault_stage,
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
