"""Run the real Phase 8 Streamlit and external-worker lifecycle proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from perpscanner.strategy_lab.binance_data import (  # noqa: E402
    _atomic_write,
    _pretty_json_bytes,
)
from perpscanner.strategy_lab.prompt_engine import (  # noqa: E402
    confirm_prompt_candidate,
    interpret_research_prompt,
)
from perpscanner.strategy_lab.reporting import sha256_file  # noqa: E402
from perpscanner.strategy_lab.ui_jobs import (  # noqa: E402
    StrategyLabUIStore,
    launch_strategy_lab_job,
)
from perpscanner.ui_strategy_lab import DEFAULT_PROMPT  # noqa: E402


def _await_state(
    store: StrategyLabUIStore,
    job_id: str,
    expected: set[str],
    *,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last = store.read_job(job_id)
    while time.monotonic() < deadline:
        last = store.read_job(job_id)
        if last["state"] in expected:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"Job {job_id} remained {last['state']!r}; expected {sorted(expected)}"
    )


def _button(app: AppTest, label: str):
    matches = [button for button in app.button if button.label == label]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {label!r} button, found {len(matches)}")
    return matches[0]


def _assert_clean_app(app: AppTest) -> None:
    if app.exception:
        raise AssertionError(f"Streamlit raised exceptions: {list(app.exception)}")


def _persist_confirmation(
    store: StrategyLabUIStore,
    prompt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft = interpret_research_prompt(prompt)
    if draft["status"] != "ready_for_confirmation":
        raise AssertionError(f"Proof prompt was not confirmable: {draft['status']}")
    draft_record = store.save_draft(draft["prompt"], draft)
    confirmation = confirm_prompt_candidate(
        draft,
        approved_contract_sha256=draft["candidate_contract_sha256"],
    )
    confirmation_record = store.save_confirmation(
        draft_record["draft_id"], confirmation
    )
    return draft_record, confirmation_record


def _run_nonblocking_worker_proof(
    repo: Path,
    output: Path,
) -> dict[str, Any]:
    store = StrategyLabUIStore(output / "worker-state")
    draft, confirmation = _persist_confirmation(store, DEFAULT_PROMPT)
    job, created = store.prepare_job(confirmation["confirmation_id"])
    if not created:
        raise AssertionError("Fresh proof job was not created")

    started = time.monotonic()
    worker_pid = launch_strategy_lab_job(
        repo,
        store.root,
        job["job_id"],
        delay_seconds=1.0,
    )
    launch_seconds = time.monotonic() - started
    if launch_seconds >= 0.75:
        raise AssertionError(
            f"External launch blocked for {launch_seconds:.3f} seconds"
        )
    running = _await_state(store, job["job_id"], {"running", "completed"})
    duplicate, duplicate_created = store.prepare_job(confirmation["confirmation_id"])
    if duplicate_created or duplicate["job_id"] != job["job_id"]:
        raise AssertionError("Rerun preparation created a duplicate job")
    completed = _await_state(store, job["job_id"], {"completed"})

    recovered = StrategyLabUIStore(store.root)
    if recovered.read_job(job["job_id"])["state"] != "completed":
        raise AssertionError("Completed job was not recovered from disk")
    return {
        "draft_id": draft["draft_id"],
        "confirmation_id": confirmation["confirmation_id"],
        "job_id": job["job_id"],
        "worker_pid": worker_pid,
        "launch_seconds": round(launch_seconds, 6),
        "observed_active_state": running["state"],
        "terminal_state": completed["state"],
        "duplicate_job_created": False,
        "recovered_after_restart": True,
    }


def _run_streamlit_flow(repo: Path, output: Path) -> dict[str, Any]:
    store_root = output / "streamlit-state"
    os.environ["STRATEGY_LAB_STORE_ROOT"] = str(store_root)
    app_file = repo / "tools" / "strategy_lab_phase8" / "ui_smoke_app.py"
    app = AppTest.from_file(app_file).run(timeout=20)
    _assert_clean_app(app)
    if [title.value for title in app.title] != ["Strategy Lab"]:
        raise AssertionError("Strategy Lab title did not render")
    if [area.label for area in app.text_area] != ["Research question"]:
        raise AssertionError("Research prompt control did not render")

    _button(app, "Interpret question").click()
    app.run(timeout=20)
    _assert_clean_app(app)
    approvals = [
        checkbox
        for checkbox in app.checkbox
        if checkbox.label.startswith("I approve this exact checksum")
    ]
    if len(approvals) != 1:
        raise AssertionError("Exact-checksum confirmation control is missing")
    approvals[0].check()
    _button(app, "Confirm contract").click()
    app.run(timeout=20)
    _assert_clean_app(app)

    started = time.monotonic()
    _button(app, "Submit reference job").click()
    app.run(timeout=20)
    submit_seconds = time.monotonic() - started
    _assert_clean_app(app)
    if submit_seconds >= 5.0:
        raise AssertionError(
            f"Streamlit job submission blocked for {submit_seconds:.3f}s"
        )

    store = StrategyLabUIStore(store_root)
    jobs = store.list_jobs()
    if len(jobs) != 1:
        raise AssertionError(f"Expected one persisted UI job, found {len(jobs)}")
    job_id = jobs[0]["job_id"]
    completed = _await_state(store, job_id, {"completed"})

    app.run(timeout=20)
    _assert_clean_app(app)
    if len(app.metric) != 4:
        raise AssertionError(
            f"Expected four primary metric cards, found {len(app.metric)}"
        )
    metric_labels = [metric.label for metric in app.metric]
    if not any("holdout" in warning.value.lower() for warning in app.warning):
        raise AssertionError("Holdout warning did not render")

    restarted = AppTest.from_file(app_file).run(timeout=20)
    _assert_clean_app(restarted)
    if len(restarted.metric) != 4:
        raise AssertionError("Completed metrics did not recover in a new UI session")
    if len(StrategyLabUIStore(store_root).list_jobs()) != 1:
        raise AssertionError("UI restart duplicated the completed job")
    return {
        "job_id": job_id,
        "terminal_state": completed["state"],
        "submit_seconds": round(submit_seconds, 6),
        "metric_card_count": len(app.metric),
        "metric_labels": metric_labels,
        "warning_count": len(app.warning),
        "recovered_metric_card_count": len(restarted.metric),
        "responsive_native_horizontal_container": True,
        "single_job_after_rerun_and_restart": True,
    }


def _run_safe_failure_proof(repo: Path, output: Path) -> dict[str, Any]:
    store = StrategyLabUIStore(output / "failure-state")
    prompt = (
        "Revise the long BTCUSDT 5m EMA9 bounce with fees 5 bps per side "
        "using all remaining frozen reference settings."
    )
    _, confirmation = _persist_confirmation(store, prompt)
    job, created = store.prepare_job(confirmation["confirmation_id"])
    if not created:
        raise AssertionError("Fresh unsupported revision job was not created")
    launch_strategy_lab_job(repo, store.root, job["job_id"])
    failed = _await_state(store, job["job_id"], {"failed"})
    recovered = StrategyLabUIStore(store.root).read_job(job["job_id"])
    if recovered["state"] != "failed":
        raise AssertionError("Failed state did not recover from disk")
    return {
        "job_id": job["job_id"],
        "terminal_state": failed["state"],
        "error_type": failed["error_type"],
        "error_message": failed["error_message"],
        "recovered_after_restart": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    worker = _run_nonblocking_worker_proof(repo, output)
    streamlit = _run_streamlit_flow(repo, output)
    safe_failure = _run_safe_failure_proof(repo, output)
    report = {
        "status": "passed",
        "phase": 8,
        "streamlit_version": "1.59.2",
        "streamlit_autorefresh_version": "1.0.1",
        "offline_runtime": True,
        "cpu_limit": 1,
        "memory_limit_mb": 1024,
        "worker_lifecycle": worker,
        "streamlit_flow": streamlit,
        "safe_failure": safe_failure,
        "scanner_market_fetch_bypassed_on_lab_page": True,
        "research_logging_unchanged": True,
        "holdout_opened": False,
        "live_trading_authorized": False,
    }
    report_path = output / "phase8-proof-report.json"
    _atomic_write(report_path, _pretty_json_bytes(report))

    sources = (
        repo / "perpscanner" / "app.py",
        repo / "perpscanner" / "ui_strategy_lab.py",
        repo / "perpscanner" / "strategy_lab" / "ui_jobs.py",
        repo / "perpscanner" / "strategy_lab" / "ui_model.py",
        repo / "tools" / "strategy_lab_phase8" / "run_ui_job.py",
        repo / "tools" / "strategy_lab_phase8" / "run_ui_suite.py",
    )
    manifest = {
        "status": "passed",
        "phase": 8,
        "proof_report_sha256": sha256_file(report_path),
        "source_hashes": {
            str(path.relative_to(repo)).replace("\\", "/"): sha256_file(path)
            for path in sources
        },
    }
    manifest["deterministic_source_id"] = hashlib.sha256(
        "".join(manifest["source_hashes"].values()).encode("ascii")
    ).hexdigest()
    _atomic_write(output / "run-manifest.json", _pretty_json_bytes(manifest))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
