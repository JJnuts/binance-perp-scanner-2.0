"""Persisted, idempotent job lifecycle for the Strategy Lab UI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RECORD_VERSION = "0.1.0"
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,95})$")
EXPECTED_EXPORT_FILES = (
    "results.json",
    "metrics.csv",
    "report.html",
    "traceability.json",
    "export-manifest.json",
)
ACTIVE_JOB_STATES = frozenset({"queued", "running", "cancellation_requested"})
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled"})
JOB_STATES = ACTIVE_JOB_STATES | TERMINAL_JOB_STATES
DEFAULT_MAX_JOBS = 100
DEFAULT_MAX_STORE_BYTES = 512 * 1024 * 1024


class StrategyLabUIJobError(RuntimeError):
    """Raised when persisted UI workflow state is invalid or unsafe."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyLabUIJobError(
            f"Invalid persisted Strategy Lab file: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise StrategyLabUIJobError(
            f"Persisted Strategy Lab file is not an object: {path}"
        )
    return payload


class StrategyLabUIStore:
    """Filesystem-backed draft, confirmation, and job state."""

    def __init__(
        self,
        root: Path | str,
        *,
        max_jobs: int = DEFAULT_MAX_JOBS,
        max_store_bytes: int = DEFAULT_MAX_STORE_BYTES,
    ):
        if max_jobs < 1:
            raise ValueError("Strategy Lab max_jobs must be positive")
        if max_store_bytes < 1:
            raise ValueError("Strategy Lab max_store_bytes must be positive")
        self.root = Path(root).resolve()
        self.max_jobs = int(max_jobs)
        self.max_store_bytes = int(max_store_bytes)
        self.drafts_root = self.root / "drafts"
        self.confirmations_root = self.root / "confirmations"
        self.jobs_root = self.root / "jobs"
        for directory in (self.drafts_root, self.confirmations_root, self.jobs_root):
            directory.mkdir(parents=True, exist_ok=True)

    def store_usage(self) -> dict[str, int]:
        """Return bounded-store usage without following symbolic links."""

        byte_count = 0
        file_count = 0
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise StrategyLabUIJobError(
                    f"Symbolic links are not allowed in Strategy Lab state: {path}"
                )
            if path.is_file():
                try:
                    byte_count += path.stat().st_size
                except OSError as exc:
                    raise StrategyLabUIJobError(
                        f"Unable to inspect Strategy Lab state: {path}"
                    ) from exc
                file_count += 1
        return {
            "bytes": byte_count,
            "files": file_count,
            "jobs": sum(1 for _ in self.jobs_root.glob("job-*/status.json")),
        }

    def assert_within_quota(self, *, adding_job: bool = False) -> dict[str, int]:
        """Fail closed before state can exceed its configured disk/job quota."""

        usage = self.store_usage()
        projected_jobs = usage["jobs"] + int(adding_job)
        if projected_jobs > self.max_jobs:
            raise StrategyLabUIJobError(
                f"Strategy Lab job quota exceeded: {projected_jobs} > {self.max_jobs}"
            )
        if usage["bytes"] > self.max_store_bytes:
            raise StrategyLabUIJobError(
                "Strategy Lab state exceeds its disk quota: "
                f"{usage['bytes']} > {self.max_store_bytes} bytes"
            )
        return usage

    @staticmethod
    def _validate_identifier(value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise StrategyLabUIJobError(f"Invalid Strategy Lab identifier: {value!r}")
        return value

    def _record_directory(self, collection: Path, identifier: str) -> Path:
        return collection / self._validate_identifier(identifier)

    def job_directory(self, job_id: str) -> Path:
        return self._record_directory(self.jobs_root, job_id)

    def save_draft(self, prompt: str, draft: dict[str, Any]) -> dict[str, Any]:
        if draft.get("status") not in {"needs_review", "ready_for_confirmation"}:
            raise StrategyLabUIJobError(
                "Only reviewable prompt drafts can be persisted"
            )
        checksum = draft.get("draft_sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise StrategyLabUIJobError("Prompt draft lacks a valid integrity checksum")
        draft_id = f"draft-{checksum[:20]}"
        directory = self._record_directory(self.drafts_root, draft_id)
        record_path = directory / "record.json"
        if directory.exists():
            existing = _read_json(record_path)
            if existing.get("draft", {}).get("draft_sha256") != checksum:
                raise StrategyLabUIJobError(
                    "Existing draft record has an integrity mismatch"
                )
            return existing
        try:
            directory.mkdir()
        except FileExistsError:
            return self.load_draft(draft_id)
        created_at = _utc_now()
        record = {
            "record_version": RECORD_VERSION,
            "record_type": "prompt_draft",
            "state": "draft",
            "draft_id": draft_id,
            "prompt": prompt,
            "prompt_status": draft["status"],
            "created_at_utc": created_at,
            "updated_at_utc": created_at,
            "draft": draft,
        }
        _atomic_write(record_path, _pretty_json_bytes(record))
        return record

    def load_draft(self, draft_id: str) -> dict[str, Any]:
        path = self._record_directory(self.drafts_root, draft_id) / "record.json"
        return _read_json(path)

    def list_drafts(self) -> list[dict[str, Any]]:
        records = []
        for path in self.drafts_root.glob("draft-*/record.json"):
            records.append(_read_json(path))
        return sorted(
            records,
            key=lambda item: (item.get("updated_at_utc", ""), item["draft_id"]),
            reverse=True,
        )

    def save_confirmation(
        self,
        draft_id: str,
        confirmation: dict[str, Any],
    ) -> dict[str, Any]:
        draft_record = self.load_draft(draft_id)
        if confirmation.get("status") != "confirmed":
            raise StrategyLabUIJobError("Only confirmed contracts can be persisted")
        if confirmation.get("execution_authorized") is not False:
            raise StrategyLabUIJobError("UI confirmation must not authorize execution")
        if confirmation.get("holdout_open_authorized") is not False:
            raise StrategyLabUIJobError(
                "UI confirmation must not authorize holdout access"
            )
        checksum = confirmation.get("confirmed_contract_sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise StrategyLabUIJobError("Confirmation lacks a valid contract checksum")
        if draft_record["draft"]["candidate_contract_sha256"] != checksum:
            raise StrategyLabUIJobError(
                "Confirmation does not match its persisted draft"
            )
        engine_confirmation_id = confirmation.get("confirmation_id")
        if not isinstance(engine_confirmation_id, str) or not re.fullmatch(
            r"[0-9a-f]{64}", engine_confirmation_id
        ):
            raise StrategyLabUIJobError("Confirmation lacks its engine checksum")
        confirmation_id = f"confirmed-{engine_confirmation_id[:20]}"
        directory = self._record_directory(self.confirmations_root, confirmation_id)
        record_path = directory / "record.json"
        if directory.exists():
            existing = _read_json(record_path)
            if existing.get("confirmation") != confirmation:
                raise StrategyLabUIJobError("Existing confirmation record differs")
            return existing
        try:
            directory.mkdir()
        except FileExistsError:
            return self.load_confirmation(confirmation_id)
        created_at = _utc_now()
        record = {
            "record_version": RECORD_VERSION,
            "record_type": "confirmed_contract",
            "state": "confirmed",
            "confirmation_id": confirmation_id,
            "draft_id": draft_id,
            "created_at_utc": created_at,
            "updated_at_utc": created_at,
            "confirmation": confirmation,
        }
        _atomic_write(record_path, _pretty_json_bytes(record))
        return record

    def load_confirmation(self, confirmation_id: str) -> dict[str, Any]:
        path = (
            self._record_directory(self.confirmations_root, confirmation_id)
            / "record.json"
        )
        return _read_json(path)

    def list_confirmations(self) -> list[dict[str, Any]]:
        records = []
        for path in self.confirmations_root.glob("confirmed-*/record.json"):
            records.append(_read_json(path))
        return sorted(
            records,
            key=lambda item: (
                item.get("updated_at_utc", ""),
                item["confirmation_id"],
            ),
            reverse=True,
        )

    def _job_attempts(self, contract_checksum: str) -> list[dict[str, Any]]:
        prefix = f"job-{contract_checksum[:20]}-a"
        records = []
        for path in self.jobs_root.glob(f"{prefix}*/status.json"):
            records.append(self.read_job(path.parent.name))
        return sorted(records, key=lambda item: int(item["attempt"]))

    def prepare_job(
        self,
        confirmation_id: str,
        *,
        retry_failed: bool = False,
        retry_cancelled: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        if retry_failed and retry_cancelled:
            raise StrategyLabUIJobError(
                "A job retry cannot target two terminal states"
            )
        confirmation_record = self.load_confirmation(confirmation_id)
        confirmation = confirmation_record["confirmation"]
        contract = confirmation["confirmed_contract"]
        checksum = _sha256_payload(contract)
        if checksum != confirmation["confirmed_contract_sha256"]:
            raise StrategyLabUIJobError("Confirmed contract checksum changed on disk")
        attempts = self._job_attempts(checksum)
        if retry_failed or retry_cancelled:
            expected_state = "failed" if retry_failed else "cancelled"
            if attempts and attempts[-1]["state"] != expected_state:
                raise StrategyLabUIJobError(
                    f"Only a {expected_state} job can be retried"
                )
            attempt = int(attempts[-1]["attempt"]) + 1 if attempts else 1
        else:
            attempt = 1
        job_id = f"job-{checksum[:20]}-a{attempt}"
        directory = self.job_directory(job_id)
        if directory.exists():
            return self.read_job(job_id), False
        self.assert_within_quota(adding_job=True)
        try:
            directory.mkdir()
        except FileExistsError:
            return self.read_job(job_id), False
        created_at = _utc_now()
        request = {
            "record_version": RECORD_VERSION,
            "job_id": job_id,
            "attempt": attempt,
            "confirmation_id": confirmation_id,
            "confirmation_checksum": confirmation["confirmation_id"],
            "confirmed_contract_sha256": checksum,
            "reference_pipeline_only": True,
            "execution_authorized": False,
            "holdout_open_authorized": False,
            "created_at_utc": created_at,
        }
        status = {
            "record_version": RECORD_VERSION,
            "record_type": "ui_job",
            "job_id": job_id,
            "attempt": attempt,
            "confirmation_id": confirmation_id,
            "confirmed_contract_sha256": checksum,
            "state": "queued",
            "created_at_utc": created_at,
            "updated_at_utc": created_at,
        }
        _atomic_write(directory / "request.json", _pretty_json_bytes(request))
        _atomic_write(directory / "contract.json", _pretty_json_bytes(contract))
        _atomic_write(directory / "status.json", _pretty_json_bytes(status))
        return status, True

    def read_job(self, job_id: str) -> dict[str, Any]:
        status = _read_json(self.job_directory(job_id) / "status.json")
        if status.get("job_id") != job_id:
            raise StrategyLabUIJobError(
                "Persisted job identifier does not match its path"
            )
        if status.get("state") not in JOB_STATES:
            raise StrategyLabUIJobError(
                f"Persisted job has an invalid state: {status.get('state')!r}"
            )
        attempt = status.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise StrategyLabUIJobError("Persisted job has an invalid attempt number")
        checksum = status.get("confirmed_contract_sha256")
        if not isinstance(checksum, str) or not re.fullmatch(
            r"[0-9a-f]{64}", checksum
        ):
            raise StrategyLabUIJobError(
                "Persisted job has an invalid contract checksum"
            )
        return status

    def read_job_request(self, job_id: str) -> dict[str, Any]:
        return _read_json(self.job_directory(job_id) / "request.json")

    def read_job_contract(self, job_id: str) -> dict[str, Any]:
        return _read_json(self.job_directory(job_id) / "contract.json")

    def list_jobs(self) -> list[dict[str, Any]]:
        records = []
        for path in self.jobs_root.glob("job-*/status.json"):
            records.append(self.read_job(path.parent.name))
        return sorted(
            records,
            key=lambda item: (item.get("updated_at_utc", ""), item["job_id"]),
            reverse=True,
        )

    def claim_job(self, job_id: str) -> bool:
        directory = self.job_directory(job_id)
        if self.read_job(job_id)["state"] != "queued":
            return False
        claim_path = directory / "run.claim"
        try:
            descriptor = os.open(
                claim_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\nclaimed_at_utc={_utc_now()}\n")
        status = self.read_job(job_id)
        if status["state"] != "queued":
            return False
        status.update(
            {
                "state": "running",
                "worker_pid": os.getpid(),
                "started_at_utc": _utc_now(),
                "updated_at_utc": _utc_now(),
            }
        )
        _atomic_write(directory / "status.json", _pretty_json_bytes(status))
        self.write_heartbeat(job_id, stage="claimed")
        return True

    def write_heartbeat(self, job_id: str, *, stage: str) -> Path:
        """Persist worker liveness without relying on Streamlit session state."""

        status = self.read_job(job_id)
        if status["state"] not in {"running", "cancellation_requested"}:
            raise StrategyLabUIJobError(
                "Only an active worker can write a heartbeat"
            )
        path = self.job_directory(job_id) / "heartbeat.json"
        payload = {
            "record_version": RECORD_VERSION,
            "job_id": job_id,
            "worker_pid": status.get("worker_pid"),
            "stage": str(stage)[:120],
            "updated_at_utc": _utc_now(),
        }
        _atomic_write(path, _pretty_json_bytes(payload))
        return path

    def request_cancel(
        self,
        job_id: str,
        *,
        reason: str = "User requested",
    ) -> dict[str, Any]:
        """Request cooperative cancellation and stop queued work immediately."""

        directory = self.job_directory(job_id)
        status = self.read_job(job_id)
        if status["state"] in {"completed", "failed"}:
            raise StrategyLabUIJobError(
                f"A {status['state']} job cannot be cancelled"
            )
        if status["state"] == "cancelled":
            return status
        requested_at = status.get("cancel_requested_at_utc") or _utc_now()
        request = {
            "record_version": RECORD_VERSION,
            "job_id": job_id,
            "reason": str(reason)[:500],
            "requested_at_utc": requested_at,
        }
        if status["state"] == "queued":
            cancelled = self.cancel_job(job_id, reason=reason)
            _atomic_write(
                directory / "cancel-request.json", _pretty_json_bytes(request)
            )
            return cancelled
        status.update(
            {
                "state": "cancellation_requested",
                "cancel_requested_at_utc": requested_at,
                "cancel_reason": str(reason)[:500],
                "updated_at_utc": _utc_now(),
            }
        )
        _atomic_write(directory / "status.json", _pretty_json_bytes(status))
        _atomic_write(directory / "cancel-request.json", _pretty_json_bytes(request))
        return status

    def is_cancel_requested(self, job_id: str) -> bool:
        status = self.read_job(job_id)
        return status["state"] in {"cancellation_requested", "cancelled"} or (
            self.job_directory(job_id) / "cancel-request.json"
        ).is_file()

    def cancel_job(self, job_id: str, *, reason: str) -> dict[str, Any]:
        """Persist a terminal cancellation; completion is never implied."""

        directory = self.job_directory(job_id)
        status = self.read_job(job_id)
        if status["state"] == "cancelled":
            return status
        if status["state"] not in {
            "queued",
            "running",
            "cancellation_requested",
        }:
            raise StrategyLabUIJobError(
                f"A {status['state']} job cannot transition to cancelled"
            )
        status.update(
            {
                "state": "cancelled",
                "cancelled_at_utc": _utc_now(),
                "updated_at_utc": _utc_now(),
                "cancel_reason": str(reason)[:500],
            }
        )
        _atomic_write(directory / "status.json", _pretty_json_bytes(status))
        return status

    def write_job_result(self, job_id: str, result: dict[str, Any]) -> Path:
        if self.read_job(job_id)["state"] != "running":
            raise StrategyLabUIJobError("Job result can only be written while running")
        path = self.job_directory(job_id) / "job-result.json"
        _atomic_write(path, _pretty_json_bytes(result))
        return path

    def complete_job(self, job_id: str) -> dict[str, Any]:
        directory = self.job_directory(job_id)
        status = self.read_job(job_id)
        if status["state"] != "running":
            raise StrategyLabUIJobError("Only a running job can complete")
        result_path = directory / "job-result.json"
        result = _read_json(result_path)
        if result.get("status") != "passed":
            raise StrategyLabUIJobError("Job result did not pass its completion gate")
        artifacts = directory / "artifacts"
        artifact_hashes = {}
        for filename in EXPECTED_EXPORT_FILES:
            path = artifacts / filename
            if not path.is_file():
                raise StrategyLabUIJobError(f"Completed job is missing {filename}")
            artifact_hashes[filename] = _sha256_file(path)
        self.assert_within_quota()
        status.update(
            {
                "state": "completed",
                "completed_at_utc": _utc_now(),
                "updated_at_utc": _utc_now(),
                "result_file": "job-result.json",
                "result_sha256": _sha256_file(result_path),
                "artifact_hashes": artifact_hashes,
            }
        )
        _atomic_write(directory / "status.json", _pretty_json_bytes(status))
        return status

    def verify_completed_job(self, job_id: str) -> dict[str, Any]:
        """Recheck every persisted completion hash before rendering results."""

        directory = self.job_directory(job_id)
        status = self.read_job(job_id)
        if status["state"] != "completed":
            raise StrategyLabUIJobError("Only a completed job can be verified")
        result_path = directory / status.get("result_file", "")
        if not result_path.is_file():
            raise StrategyLabUIJobError("Completed job result file is missing")
        if _sha256_file(result_path) != status.get("result_sha256"):
            raise StrategyLabUIJobError("Completed job result checksum changed")
        recorded_hashes = status.get("artifact_hashes")
        if not isinstance(recorded_hashes, dict) or set(recorded_hashes) != set(
            EXPECTED_EXPORT_FILES
        ):
            raise StrategyLabUIJobError("Completed job artifact inventory changed")
        for filename in EXPECTED_EXPORT_FILES:
            path = directory / "artifacts" / filename
            if not path.is_file() or _sha256_file(path) != recorded_hashes[filename]:
                raise StrategyLabUIJobError(
                    f"Completed job artifact checksum changed: {filename}"
                )
        return status

    def fail_job(
        self,
        job_id: str,
        *,
        error_type: str,
        message: str,
    ) -> dict[str, Any]:
        directory = self.job_directory(job_id)
        status = self.read_job(job_id)
        if status["state"] in {"completed", "cancelled"}:
            raise StrategyLabUIJobError(
                f"A {status['state']} job cannot be marked failed"
            )
        if status["state"] == "failed":
            return status
        status.update(
            {
                "state": "failed",
                "failed_at_utc": _utc_now(),
                "updated_at_utc": _utc_now(),
                "error_type": str(error_type)[:120],
                "error_message": str(message)[:500],
            }
        )
        _atomic_write(directory / "status.json", _pretty_json_bytes(status))
        return status

    def write_worker_error(self, job_id: str, traceback_text: str) -> Path:
        path = self.job_directory(job_id) / "worker-error.log"
        _atomic_write(path, traceback_text[-20000:].encode("utf-8", errors="replace"))
        return path


def _worker_environment(repo_root: Path) -> dict[str, str]:
    allowed_names = (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
    )
    environment = {
        name: os.environ[name] for name in allowed_names if name in os.environ
    }
    dependency_paths = []
    for item in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if not item:
            continue
        path = Path(item).resolve()
        if path.is_dir() and path != repo_root and str(path) not in dependency_paths:
            dependency_paths.append(str(path))
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(repo_root), *dependency_paths)),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "STRATEGY_LAB_OFFLINE": "1",
        }
    )
    return environment


def launch_strategy_lab_job(
    repo_root: Path | str,
    store_root: Path | str,
    job_id: str,
    *,
    delay_seconds: float = 0.0,
) -> int:
    """Start a job worker without running research in the Streamlit rerun."""

    repo = Path(repo_root).resolve()
    store = StrategyLabUIStore(store_root)
    if store.read_job(job_id)["state"] != "queued":
        raise StrategyLabUIJobError("Only a queued job can be launched")
    runner = repo / "tools" / "strategy_lab_phase8" / "run_ui_job.py"
    if not runner.is_file():
        raise StrategyLabUIJobError(f"Strategy Lab worker is missing: {runner}")
    log_path = store.job_directory(job_id) / "worker.log"
    command = [
        sys.executable,
        str(runner),
        "--repo-root",
        str(repo),
        "--store-root",
        str(store.root),
        "--job-id",
        job_id,
        "--delay-seconds",
        str(max(0.0, float(delay_seconds))),
    ]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(repo),
        "env": _worker_environment(repo),
        "stdin": subprocess.DEVNULL,
        "stdout": None,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_kwargs["start_new_session"] = True
    with log_path.open("ab") as log_handle:
        popen_kwargs["stdout"] = log_handle
        process = subprocess.Popen(command, **popen_kwargs)
    threading.Thread(
        target=process.wait,
        name=f"strategy-lab-worker-reaper-{process.pid}",
        daemon=True,
    ).start()
    return int(process.pid)
