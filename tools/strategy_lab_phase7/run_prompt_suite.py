"""Execute the deterministic Phase 7 reviewed and adversarial prompt suite."""

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
    _atomic_write,
    _pretty_json_bytes,
)
from perpscanner.strategy_lab.prompt_engine import (  # noqa: E402
    PROMPT_ENGINE_VERSION,
    confirm_prompt_candidate,
    interpret_research_prompt,
)
from perpscanner.strategy_lab.reporting import sha256_file  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    _atomic_write(path, _pretty_json_bytes(payload))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    suite_path = args.suite.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    suite = json.loads(suite_path.read_text(encoding="utf-8"))

    reviewed_results = []
    ambiguous_draft = None
    revised_candidate = None
    for case in suite["reviewed_cases"]:
        result = interpret_research_prompt(case["prompt"])
        if result["status"] != case["expected_status"]:
            raise AssertionError(
                f"{case['case_id']} status {result['status']} "
                f"!= {case['expected_status']}"
            )
        if "expected_question_ids" in case:
            actual_questions = [item["question_id"] for item in result["questions"]]
            if actual_questions != case["expected_question_ids"]:
                raise AssertionError(f"{case['case_id']} question set differs")
            ambiguous_draft = result
        if "expected_diff_paths" in case:
            actual_paths = [item["path"] for item in result["contract_diff"]]
            if actual_paths != case["expected_diff_paths"]:
                raise AssertionError(f"{case['case_id']} diff differs")
        if "expected_rejection_type" in case:
            if result["rejection_type"] != case["expected_rejection_type"]:
                raise AssertionError(f"{case['case_id']} rejection type differs")
        if case["case_id"] == "reviewed-fee-revision":
            revised_candidate = result
        reviewed_results.append(
            {
                "case_id": case["case_id"],
                "status": result["status"],
                "question_count": len(result.get("questions", [])),
                "diff_count": len(result.get("contract_diff", [])),
                "rejection_type": result.get("rejection_type"),
            }
        )

    if ambiguous_draft is None or revised_candidate is None:
        raise AssertionError("Prompt suite lacks required proof cases")
    confirmation = confirm_prompt_candidate(
        revised_candidate,
        approved_contract_sha256=revised_candidate["candidate_contract_sha256"],
    )
    if confirmation["execution_authorized"]:
        raise AssertionError("Prompt confirmation authorized execution")
    if confirmation["holdout_open_authorized"]:
        raise AssertionError("Prompt confirmation authorized holdout access")

    adversarial_results = []
    for case in suite["adversarial_cases"]:
        result = interpret_research_prompt(case["prompt"])
        if result["status"] != "rejected" or result["candidate_contract"] is not None:
            raise AssertionError(f"Adversarial case passed: {case['case_id']}")
        adversarial_results.append(
            {
                "case_id": case["case_id"],
                "status": result["status"],
                "rejection_type": result["rejection_type"],
                "finding_ids": [item["finding_id"] for item in result["findings"]],
            }
        )

    paths = {
        "ambiguous-draft.json": output / "ambiguous-draft.json",
        "reviewed-candidate.json": output / "reviewed-candidate.json",
        "confirmed-contract.json": output / "confirmed-contract.json",
        "contract-diff.json": output / "contract-diff.json",
        "adversarial-results.json": output / "adversarial-results.json",
        "prompt-suite-report.json": output / "prompt-suite-report.json",
    }
    _write_json(paths["ambiguous-draft.json"], ambiguous_draft)
    _write_json(paths["reviewed-candidate.json"], revised_candidate)
    _write_json(paths["confirmed-contract.json"], confirmation)
    _write_json(
        paths["contract-diff.json"],
        {
            "status": "passed",
            "changes": revised_candidate["contract_diff"],
        },
    )
    _write_json(
        paths["adversarial-results.json"],
        {
            "status": "passed",
            "rejected_count": len(adversarial_results),
            "cases": adversarial_results,
        },
    )
    report = {
        "status": "passed",
        "engine_version": PROMPT_ENGINE_VERSION,
        "reviewed_case_count": len(reviewed_results),
        "reviewed_cases": reviewed_results,
        "ambiguity_question_count": len(ambiguous_draft["questions"]),
        "adversarial_case_count": len(adversarial_results),
        "adversarial_cases_rejected": len(adversarial_results),
        "confirmed_contract_sha256": confirmation["confirmed_contract_sha256"],
        "confirmed_diff_count": len(confirmation["contract_diff"]),
        "execution_authorized": confirmation["execution_authorized"],
        "holdout_open_authorized": confirmation["holdout_open_authorized"],
    }
    _write_json(paths["prompt-suite-report.json"], report)

    contract_path = (
        repo
        / "docs"
        / "strategy_lab"
        / "examples"
        / "btcusdt_ema9_bounce.event-study.json"
    )
    schema_path = repo / "docs" / "strategy_lab" / "research_contract.schema.json"
    engine_path = repo / "perpscanner" / "strategy_lab" / "prompt_engine.py"
    runner_path = repo / "tools" / "strategy_lab_phase7" / "run_prompt_suite.py"
    source_hashes = {
        "prompt_engine_sha256": sha256_file(engine_path),
        "runner_sha256": sha256_file(runner_path),
    }
    manifest = {
        "status": "passed",
        "phase": 7,
        "engine_version": PROMPT_ENGINE_VERSION,
        "runtime_image": "strategy-lab-vectorbt:0.28.5-phase1",
        "suite_file": "tools/strategy_lab_phase7/prompt-suite.json",
        "suite_sha256": sha256_file(suite_path),
        "reference_contract_sha256": sha256_file(contract_path),
        "contract_schema_sha256": sha256_file(schema_path),
        **source_hashes,
        "artifacts": {name: sha256_file(path) for name, path in sorted(paths.items())},
        "deterministic_run_id": hashlib.sha256(
            (
                sha256_file(suite_path)
                + sha256_file(contract_path)
                + sha256_file(schema_path)
                + source_hashes["prompt_engine_sha256"]
                + source_hashes["runner_sha256"]
                + PROMPT_ENGINE_VERSION
            ).encode("ascii")
        ).hexdigest(),
    }
    _write_json(output / "run-manifest.json", manifest)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
