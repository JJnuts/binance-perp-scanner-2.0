"""Run the final offline Strategy Lab lifecycle and reproducibility stress proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from perpscanner.strategy_lab.binance_data import (  # noqa: E402
    DataIntegrityError,
    DownloadSpec,
    TrustedKlineStore,
    _atomic_write,
    _pretty_json_bytes,
)
from perpscanner.strategy_lab.prompt_engine import (  # noqa: E402
    confirm_prompt_candidate,
    interpret_research_prompt,
)
from perpscanner.strategy_lab.reporting import (  # noqa: E402
    EXPECTED_EXPORT_FILES,
    build_export_bundle,
    sha256_file,
)
from perpscanner.strategy_lab.ui_jobs import (  # noqa: E402
    StrategyLabUIJobError,
    StrategyLabUIStore,
    _worker_environment,
    launch_strategy_lab_job,
)
from perpscanner.ui_strategy_lab import DEFAULT_PROMPT  # noqa: E402


INTERVAL_MS = 300_000
LARGE_FIXTURE_ROWS = 6_000


class GeneratedClient:
    """Deterministic public-data stand-in; no network or credentials."""

    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows
        self.calls: list[int] = []

    def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[list[object]]:
        del symbol, interval
        self.calls.append(start_ms)
        return [
            row for row in self.rows if start_ms <= int(row[0]) < end_ms
        ][:limit]


def _kline(open_ms: int, index: int) -> list[object]:
    price = 30_000 + index
    return [
        open_ms,
        str(price),
        str(price + 2),
        str(price - 2),
        str(price + 1),
        "10.5",
        open_ms + INTERVAL_MS - 1,
        str((price + 1) * 10.5),
        42,
        "5.1",
        str((price + 1) * 5.1),
        "0",
    ]


def _await_state(
    store: StrategyLabUIStore,
    job_id: str,
    expected: set[str],
    *,
    timeout_seconds: float = 90.0,
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


def _persist_confirmation(
    store: StrategyLabUIStore,
    prompt: str,
) -> dict[str, Any]:
    draft = interpret_research_prompt(prompt)
    if draft["status"] != "ready_for_confirmation":
        raise AssertionError(f"Stress prompt was not confirmable: {draft['status']}")
    draft_record = store.save_draft(draft["prompt"], draft)
    confirmation = confirm_prompt_candidate(
        draft,
        approved_contract_sha256=draft["candidate_contract_sha256"],
    )
    return store.save_confirmation(draft_record["draft_id"], confirmation)


def _assert_clean_app(app: Any) -> None:
    if app.exception:
        raise AssertionError(f"Streamlit raised exceptions: {list(app.exception)}")


def _run_large_resume(output: Path) -> dict[str, Any]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5 * LARGE_FIXTURE_ROWS)
    spec = DownloadSpec.create(
        symbol="BTCUSDT",
        interval="5m",
        start=start,
        end=end,
    )
    rows = [
        _kline(int((start + timedelta(minutes=5 * index)).timestamp() * 1000), index)
        for index in range(LARGE_FIXTURE_ROWS)
    ]
    page_limit = 137
    interrupted_pages = 7
    store = TrustedKlineStore(
        output / "large-resume-cache", spec, page_limit=page_limit
    )
    paused = store.download(
        GeneratedClient(rows),
        max_pages=interrupted_pages,
        now=end,
    )
    if paused["download_complete"] or len(paused["pages"]) != interrupted_pages:
        raise AssertionError("Large fixture did not stop at its checkpoint")
    resumed_client = GeneratedClient(rows)
    manifest = store.download(resumed_client, now=end)
    expected_cursor = spec.start_ms + interrupted_pages * page_limit * spec.interval_ms
    if resumed_client.calls[0] != expected_cursor:
        raise AssertionError("Large fixture restarted instead of resuming")
    verified = store.verify_manifest()
    if verified["quality"]["normalized_row_count"] != LARGE_FIXTURE_ROWS:
        raise AssertionError("Large resumed fixture lost rows")
    return {
        "status": manifest["status"],
        "row_count": LARGE_FIXTURE_ROWS,
        "page_count": manifest["files"]["raw"]["page_count"],
        "expected_page_count": math.ceil(LARGE_FIXTURE_ROWS / page_limit),
        "interrupted_after_pages": interrupted_pages,
        "resumed_from_expected_cursor": True,
        "normalized_sha256": manifest["files"]["normalized"]["sha256"],
    }


def _run_concurrent_isolation(repo: Path, output: Path) -> dict[str, Any]:
    store = StrategyLabUIStore(
        output / "concurrent-state",
        max_jobs=4,
        max_store_bytes=64 * 1024 * 1024,
    )
    reference = _persist_confirmation(store, DEFAULT_PROMPT)
    revised = _persist_confirmation(
        store,
        "Revise the long BTCUSDT 5m EMA9 bounce with fees 5 bps per side "
        "using all remaining frozen reference settings.",
    )
    reference_job, _ = store.prepare_job(reference["confirmation_id"])
    revised_job, _ = store.prepare_job(revised["confirmation_id"])
    if reference_job["job_id"] == revised_job["job_id"]:
        raise AssertionError("Different confirmed contracts shared a job identifier")

    launch_strategy_lab_job(
        repo, store.root, reference_job["job_id"], delay_seconds=0.75
    )
    launch_strategy_lab_job(
        repo, store.root, revised_job["job_id"], delay_seconds=0.75
    )
    deadline = time.monotonic() + 20
    simultaneously_active = False
    while time.monotonic() < deadline:
        states = {
            store.read_job(reference_job["job_id"])["state"],
            store.read_job(revised_job["job_id"])["state"],
        }
        if states <= {"running", "cancellation_requested"}:
            simultaneously_active = True
            break
        time.sleep(0.05)
    if not simultaneously_active:
        raise AssertionError("Concurrent workers were never observed active together")

    completed = _await_state(store, reference_job["job_id"], {"completed"})
    failed = _await_state(store, revised_job["job_id"], {"failed"})
    store.verify_completed_job(reference_job["job_id"])
    if (store.job_directory(revised_job["job_id"]) / "job-result.json").exists():
        raise AssertionError("Failed concurrent job acquired another job's result")
    usage = store.assert_within_quota()
    return {
        "simultaneously_active": True,
        "reference_job_id": reference_job["job_id"],
        "reference_terminal_state": completed["state"],
        "revised_job_id": revised_job["job_id"],
        "revised_terminal_state": failed["state"],
        "job_directories_isolated": True,
        "store_usage_bytes": usage["bytes"],
        "store_quota_bytes": store.max_store_bytes,
    }


def _run_cancellation(repo: Path, output: Path) -> dict[str, Any]:
    from streamlit.testing.v1 import AppTest

    store = StrategyLabUIStore(output / "cancellation-state")
    confirmation = _persist_confirmation(store, DEFAULT_PROMPT)
    job, _ = store.prepare_job(confirmation["confirmation_id"])
    worker_pid = launch_strategy_lab_job(
        repo, store.root, job["job_id"], delay_seconds=5.0
    )
    _await_state(store, job["job_id"], {"running"})
    requested = store.request_cancel(
        job["job_id"], reason="Phase 9 cooperative cancellation proof"
    )
    if requested["state"] != "cancellation_requested":
        raise AssertionError("Running job did not persist its cancellation request")
    cancelled = _await_state(store, job["job_id"], {"cancelled"})
    if (store.job_directory(job["job_id"]) / "job-result.json").exists():
        raise AssertionError("Cancelled job published a completed result")

    os.environ["STRATEGY_LAB_STORE_ROOT"] = str(store.root)
    app_file = repo / "tools" / "strategy_lab_phase8" / "ui_smoke_app.py"
    restarted = AppTest.from_file(app_file).run(timeout=20)
    _assert_clean_app(restarted)
    labels = [button.label for button in restarted.button]
    if "Retry cancelled job" not in labels:
        raise AssertionError("Cancelled job did not recover in a new UI session")
    return {
        "job_id": job["job_id"],
        "worker_pid": worker_pid,
        "terminal_state": cancelled["state"],
        "result_published": False,
        "recovered_after_app_restart": True,
    }


def _run_engine_failure(repo: Path, output: Path) -> dict[str, Any]:
    store = StrategyLabUIStore(output / "engine-failure-state")
    confirmation = _persist_confirmation(store, DEFAULT_PROMPT)
    job, _ = store.prepare_job(confirmation["confirmation_id"])
    runner = repo / "tools" / "strategy_lab_phase8" / "run_ui_job.py"
    command = [
        sys.executable,
        str(runner),
        "--repo-root",
        str(repo),
        "--store-root",
        str(store.root),
        "--job-id",
        job["job_id"],
        "--fault-stage",
        "before-export",
    ]
    completed_process = subprocess.run(
        command,
        cwd=repo,
        env=_worker_environment(repo),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed_process.returncode == 0:
        raise AssertionError("Injected engine failure unexpectedly exited successfully")
    failed = store.read_job(job["job_id"])
    if failed["state"] != "failed":
        raise AssertionError("Injected engine failure was not persisted as failed")
    if (store.job_directory(job["job_id"]) / "job-result.json").exists():
        raise AssertionError("Engine failure published a completed result")
    return {
        "job_id": job["job_id"],
        "terminal_state": failed["state"],
        "error_type": failed["error_type"],
        "result_published": False,
        "recovered_after_restart": (
            StrategyLabUIStore(store.root).read_job(job["job_id"])["state"]
            == "failed"
        ),
    }


def _run_corruption_detection(repo: Path, output: Path) -> dict[str, Any]:
    from streamlit.testing.v1 import AppTest

    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    row_count = 20
    end = start + timedelta(minutes=5 * row_count)
    spec = DownloadSpec.create(
        symbol="BTCUSDT", interval="5m", start=start, end=end
    )
    rows = [
        _kline(int((start + timedelta(minutes=5 * index)).timestamp() * 1000), index)
        for index in range(row_count)
    ]
    data_store = TrustedKlineStore(
        output / "corrupt-data-cache", spec, page_limit=7
    )
    manifest = data_store.download(GeneratedClient(rows), now=end)
    raw_path = data_store.dataset_dir / manifest["files"]["raw"]["pages"][0]["file"]
    raw_path.write_bytes(raw_path.read_bytes() + b"corrupt")
    data_blocked = False
    try:
        data_store.verify_manifest()
    except DataIntegrityError:
        data_blocked = True
    if not data_blocked:
        raise AssertionError("Corrupt data cache passed its integrity gate")

    ui_store = StrategyLabUIStore(output / "corrupt-ui-state")
    confirmation = _persist_confirmation(ui_store, DEFAULT_PROMPT)
    job, _ = ui_store.prepare_job(confirmation["confirmation_id"])
    status_path = ui_store.job_directory(job["job_id"]) / "status.json"
    status_path.write_text("{not-json", encoding="utf-8")
    status_blocked = False
    try:
        ui_store.read_job(job["job_id"])
    except StrategyLabUIJobError:
        status_blocked = True
    if not status_blocked:
        raise AssertionError("Corrupt UI status passed its integrity gate")

    os.environ["STRATEGY_LAB_STORE_ROOT"] = str(ui_store.root)
    app_file = repo / "tools" / "strategy_lab_phase8" / "ui_smoke_app.py"
    app = AppTest.from_file(app_file).run(timeout=20)
    _assert_clean_app(app)
    errors = [error.value for error in app.error]
    if not any("integrity" in value.lower() for value in errors):
        raise AssertionError("UI did not render a safe corrupt-state message")
    return {
        "raw_cache_corruption_blocked": True,
        "job_state_corruption_blocked": True,
        "streamlit_failed_closed_without_exception": True,
    }


def _run_reviewer_reproduction(repo: Path, output: Path) -> dict[str, Any]:
    baseline = repo / "tools" / "strategy_lab_phase6" / "artifacts" / "bundle"
    if not baseline.is_dir():
        raise AssertionError("Phase 6 baseline bundle is missing")
    first = output / "reviewer-reproduction-a"
    second = output / "reviewer-reproduction-b"
    first_result = build_export_bundle(repo, first)
    second_result = build_export_bundle(
        repo,
        second,
        reproduce_manifest=first / "export-manifest.json",
    )
    hashes: dict[str, str] = {}
    for filename in EXPECTED_EXPORT_FILES:
        first_bytes = (first / filename).read_bytes()
        if first_bytes != (second / filename).read_bytes():
            raise AssertionError(f"Reviewer rerun changed {filename}")
        if first_bytes != (baseline / filename).read_bytes():
            raise AssertionError(f"Reviewer rerun differs from Phase 6: {filename}")
        hashes[filename] = hashlib.sha256(first_bytes).hexdigest()
    return {
        "bundle_id": first_result["bundle_id"],
        "file_count": len(hashes),
        "byte_identical_to_second_run": second_result[
            "byte_identical_reproduction"
        ],
        "byte_identical_to_phase6_baseline": True,
        "output_sha256": hashes,
    }


def _read_resource_limits() -> dict[str, Any]:
    memory_path = Path("/sys/fs/cgroup/memory.max")
    cpu_path = Path("/sys/fs/cgroup/cpu.max")
    pids_path = Path("/sys/fs/cgroup/pids.max")
    if (
        not memory_path.is_file()
        or not cpu_path.is_file()
        or not pids_path.is_file()
    ):
        raise AssertionError("Container cgroup v2 resource controls are unavailable")
    memory_text = memory_path.read_text(encoding="utf-8").strip()
    pids_text = pids_path.read_text(encoding="utf-8").strip()
    if memory_text == "max" or pids_text == "max":
        raise AssertionError("Container memory or process quota is unlimited")
    memory_limit = int(memory_text)
    pids_limit = int(pids_text)
    quota_text, period_text = cpu_path.read_text(encoding="utf-8").split()
    if quota_text == "max":
        raise AssertionError("Container CPU quota is unlimited")
    cpu_limit = int(quota_text) / int(period_text)
    if (
        memory_limit > 1024 * 1024 * 1024
        or cpu_limit > 1.0
        or pids_limit > 128
    ):
        raise AssertionError("Container exceeds the Phase 9 resource budget")
    try:
        import resource

        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        peak_rss_mb = None
    return {
        "cpu_limit": cpu_limit,
        "memory_limit_bytes": memory_limit,
        "pids_limit": pids_limit,
        "peak_runner_rss_mb": peak_rss_mb,
        "network_mode": "none",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "passed",
        "phase": 9,
        "offline_runtime": True,
        "large_download_resume": _run_large_resume(output),
        "concurrent_job_isolation": _run_concurrent_isolation(repo, output),
        "cooperative_cancellation": _run_cancellation(repo, output),
        "engine_failure": _run_engine_failure(repo, output),
        "corruption_detection": _run_corruption_detection(repo, output),
        "reviewer_reproduction": _run_reviewer_reproduction(repo, output),
        "resource_limits": _read_resource_limits(),
        "holdout_opened": False,
        "live_trading_authorized": False,
        "claim_level": "exploratory",
    }
    report_path = output / "phase9-proof-report.json"
    _atomic_write(report_path, _pretty_json_bytes(report))

    sources = (
        repo / "perpscanner" / "strategy_lab" / "ui_jobs.py",
        repo / "perpscanner" / "strategy_lab" / "ui_model.py",
        repo / "perpscanner" / "ui_strategy_lab.py",
        repo / "tools" / "strategy_lab_phase8" / "run_ui_job.py",
        repo / "tools" / "strategy_lab_phase9" / "run_stress_suite.py",
    )
    manifest = {
        "status": "passed",
        "phase": 9,
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
