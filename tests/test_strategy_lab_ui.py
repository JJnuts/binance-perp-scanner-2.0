import ast
import json
import os
import tempfile
import unittest
from pathlib import Path

from perpscanner.strategy_lab.prompt_engine import (
    confirm_prompt_candidate,
    interpret_research_prompt,
)
from perpscanner.strategy_lab.ui_jobs import (
    EXPECTED_EXPORT_FILES,
    StrategyLabUIJobError,
    StrategyLabUIStore,
    _worker_environment,
)
from perpscanner.strategy_lab.ui_model import build_results_view, job_state_label
from perpscanner.ui_strategy_lab import _initialize_lab_state


REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PROMPT = (
    "Study the long BTCUSDT 5m EMA 9 bounce from 2024-07-01 to "
    "2026-07-01 using all remaining frozen reference settings."
)


def persist_confirmation(store: StrategyLabUIStore) -> tuple[dict, dict, dict]:
    draft = interpret_research_prompt(REFERENCE_PROMPT)
    draft_record = store.save_draft(draft["prompt"], draft)
    confirmation = confirm_prompt_candidate(
        draft,
        approved_contract_sha256=draft["candidate_contract_sha256"],
    )
    confirmation_record = store.save_confirmation(
        draft_record["draft_id"], confirmation
    )
    return draft_record, confirmation_record, confirmation


class _FakeStreamlit:
    def __init__(self):
        self.session_state = {}


class StrategyLabUIJobTests(unittest.TestCase):
    def test_draft_and_confirmation_recover_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ui"
            first = StrategyLabUIStore(root)
            draft, confirmation, _ = persist_confirmation(first)

            recovered = StrategyLabUIStore(root)
            self.assertEqual(recovered.list_drafts()[0]["draft_id"], draft["draft_id"])
            self.assertEqual(
                recovered.list_confirmations()[0]["confirmation_id"],
                confirmation["confirmation_id"],
            )
            self.assertEqual(
                recovered.load_confirmation(confirmation["confirmation_id"])["state"],
                "confirmed",
            )

    def test_duplicate_submission_and_worker_claim_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui")
            _, confirmation, _ = persist_confirmation(store)
            first, first_created = store.prepare_job(confirmation["confirmation_id"])
            second, second_created = store.prepare_job(confirmation["confirmation_id"])

            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first["job_id"], second["job_id"])
            self.assertTrue(store.claim_job(first["job_id"]))
            self.assertFalse(store.claim_job(first["job_id"]))
            self.assertEqual(
                StrategyLabUIStore(store.root).read_job(first["job_id"])["state"],
                "running",
            )

    def test_failed_job_is_recoverable_and_retry_gets_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui")
            _, confirmation, _ = persist_confirmation(store)
            first, _ = store.prepare_job(confirmation["confirmation_id"])
            self.assertTrue(store.claim_job(first["job_id"]))
            failed = store.fail_job(
                first["job_id"], error_type="ProofFailure", message="safe failure"
            )
            retry, created = store.prepare_job(
                confirmation["confirmation_id"], retry_failed=True
            )

            self.assertEqual(failed["state"], "failed")
            self.assertTrue(created)
            self.assertEqual(retry["attempt"], 2)
            self.assertNotEqual(retry["job_id"], first["job_id"])
            self.assertEqual(
                StrategyLabUIStore(store.root).read_job(first["job_id"])["state"],
                "failed",
            )

    def test_running_job_cancels_cooperatively_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui")
            _, confirmation, _ = persist_confirmation(store)
            first, _ = store.prepare_job(confirmation["confirmation_id"])
            self.assertTrue(store.claim_job(first["job_id"]))

            requested = store.request_cancel(
                first["job_id"], reason="unit-test cancellation"
            )
            self.assertEqual(requested["state"], "cancellation_requested")
            self.assertTrue(store.is_cancel_requested(first["job_id"]))
            cancelled = store.cancel_job(
                first["job_id"], reason="worker acknowledged cancellation"
            )
            retry, created = store.prepare_job(
                confirmation["confirmation_id"], retry_cancelled=True
            )

            self.assertEqual(cancelled["state"], "cancelled")
            self.assertTrue(created)
            self.assertEqual(retry["attempt"], 2)

    def test_queued_job_cancels_without_being_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui")
            _, confirmation, _ = persist_confirmation(store)
            queued, _ = store.prepare_job(confirmation["confirmation_id"])

            cancelled = store.request_cancel(queued["job_id"])

            self.assertEqual(cancelled["state"], "cancelled")
            self.assertFalse(store.claim_job(queued["job_id"]))

    def test_store_quota_blocks_new_jobs_without_deleting_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui", max_jobs=1)
            _, confirmation, _ = persist_confirmation(store)
            first, _ = store.prepare_job(confirmation["confirmation_id"])
            self.assertTrue(store.claim_job(first["job_id"]))
            store.fail_job(first["job_id"], error_type="Proof", message="failed")

            with self.assertRaisesRegex(StrategyLabUIJobError, "quota"):
                store.prepare_job(
                    confirmation["confirmation_id"], retry_failed=True
                )
            self.assertEqual(store.read_job(first["job_id"])["state"], "failed")

    def test_completion_requires_every_export_and_persists_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui")
            _, confirmation, _ = persist_confirmation(store)
            status, _ = store.prepare_job(confirmation["confirmation_id"])
            job_id = status["job_id"]
            self.assertTrue(store.claim_job(job_id))
            store.write_job_result(job_id, {"status": "passed"})
            with self.assertRaisesRegex(StrategyLabUIJobError, "missing"):
                store.complete_job(job_id)

            artifacts = store.job_directory(job_id) / "artifacts"
            artifacts.mkdir()
            for filename in EXPECTED_EXPORT_FILES:
                (artifacts / filename).write_text(filename, encoding="utf-8")
            completed = store.complete_job(job_id)

            self.assertEqual(completed["state"], "completed")
            self.assertEqual(
                set(completed["artifact_hashes"]), set(EXPECTED_EXPORT_FILES)
            )
            self.assertEqual(
                StrategyLabUIStore(store.root).read_job(job_id)["state"], "completed"
            )
            store.verify_completed_job(job_id)
            (artifacts / EXPECTED_EXPORT_FILES[0]).write_text(
                "tampered", encoding="utf-8"
            )
            with self.assertRaisesRegex(StrategyLabUIJobError, "checksum"):
                store.verify_completed_job(job_id)

    def test_initial_page_state_recovers_one_linked_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StrategyLabUIStore(Path(directory) / "ui")
            draft, confirmation, _ = persist_confirmation(store)
            job, _ = store.prepare_job(confirmation["confirmation_id"])
            fake = _FakeStreamlit()

            _initialize_lab_state(fake, StrategyLabUIStore(store.root))

            self.assertEqual(fake.session_state["lab_draft_id"], draft["draft_id"])
            self.assertEqual(
                fake.session_state["lab_confirmation_id"],
                confirmation["confirmation_id"],
            )
            self.assertEqual(fake.session_state["lab_job_id"], job["job_id"])
            self.assertEqual(fake.session_state["lab_prompt"], REFERENCE_PROMPT)

    def test_worker_environment_is_offline_and_drops_credentials(self):
        old_key = os.environ.get("BINANCE_API_KEY")
        os.environ["BINANCE_API_KEY"] = "must-not-leak"
        try:
            environment = _worker_environment(REPO_ROOT)
        finally:
            if old_key is None:
                os.environ.pop("BINANCE_API_KEY", None)
            else:
                os.environ["BINANCE_API_KEY"] = old_key
        self.assertNotIn("BINANCE_API_KEY", environment)
        self.assertEqual(environment["STRATEGY_LAB_OFFLINE"], "1")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")


class StrategyLabUIPresentationTests(unittest.TestCase):
    def test_phase6_results_render_as_traceable_cards_and_locked_holdout(self):
        results = json.loads(
            (
                REPO_ROOT
                / "tools"
                / "strategy_lab_phase6"
                / "artifacts"
                / "bundle"
                / "results.json"
            ).read_text(encoding="utf-8")
        )
        view = build_results_view(results)

        self.assertEqual(len(view["cards"]), 4)
        self.assertTrue(view["holdout_locked"])
        self.assertEqual(view["conclusion"]["validation_conclusion"], "negative")
        self.assertTrue(all(card["help"] for card in view["cards"]))
        self.assertGreater(len(view["metric_rows"]), 40)

    def test_missing_holdout_gate_is_rejected(self):
        results = json.loads(
            (
                REPO_ROOT
                / "tools"
                / "strategy_lab_phase6"
                / "artifacts"
                / "bundle"
                / "results.json"
            ).read_text(encoding="utf-8")
        )
        results["metrics"] = [
            metric
            for metric in results["metrics"]
            if metric["metric_id"] != "holdout_opened"
        ]
        with self.assertRaisesRegex(ValueError, "holdout_opened"):
            build_results_view(results)

    def test_job_state_labels_are_complete(self):
        for state in (
            "queued",
            "running",
            "cancellation_requested",
            "cancelled",
            "completed",
            "failed",
        ):
            label, color, icon = job_state_label(state)
            self.assertTrue(label)
            self.assertTrue(color)
            self.assertTrue(icon)

    def test_scanner_navigation_keeps_lab_lazy_and_before_market_fetch(self):
        app_path = REPO_ROOT / "perpscanner" / "app.py"
        source = app_path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        eager_lab_imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "ui_strategy_lab"
        ]
        self.assertEqual(eager_lab_imports, [])
        self.assertLess(
            source.index("from .ui_strategy_lab import render_strategy_lab"),
            source.index("get_usdt_perpetuals()"),
        )
        self.assertIn("log_scan_snapshot", source)

    def test_page_uses_native_responsive_layout_without_custom_html(self):
        source = (REPO_ROOT / "perpscanner" / "ui_strategy_lab.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("st.container(horizontal=True)", source)
        self.assertIn('width="stretch"', source)
        self.assertNotIn("unsafe_allow_html", source)


if __name__ == "__main__":
    unittest.main()
