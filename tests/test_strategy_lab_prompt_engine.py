import copy
import json
import re
import unittest
from pathlib import Path

from perpscanner.strategy_lab.prompt_engine import (
    PromptCapabilityError,
    PromptEngineError,
    PromptIntegrityError,
    confirm_prompt_candidate,
    contract_diff,
    interpret_research_prompt,
    load_reference_prompt_contract,
    review_prompt_draft,
    validate_contract_schema,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = REPO_ROOT / "tools" / "strategy_lab_phase7" / "prompt-suite.json"
REFERENCE_PROMPT = (
    "Study the long BTCUSDT 5m EMA 9 bounce from 2024-07-01 to "
    "2026-07-01 using all remaining frozen reference settings."
)


class StrategyLabPromptEngineTests(unittest.TestCase):
    def test_offline_schema_validator_accepts_reference_contract(self):
        validate_contract_schema(load_reference_prompt_contract())

    def test_offline_schema_validator_rejects_invalid_contracts(self):
        cases = (
            ("missing required", lambda contract: contract.pop("market"), "/market"),
            (
                "unknown property",
                lambda contract: contract.update({"live_trading": True}),
                "/live_trading",
            ),
            (
                "timezone-free date",
                lambda contract: contract["data"].update(
                    {"start": "2024-07-01T00:00:00"}
                ),
                "/data/start",
            ),
            (
                "duplicate horizons",
                lambda contract: contract["outcome"].update(
                    {"forward_horizons_bars": [1, 3, 3]}
                ),
                "/outcome/forward_horizons_bars",
            ),
            (
                "invalid target",
                lambda contract: contract["outcome"]["barrier"].update(
                    {"target_pct": 0}
                ),
                "/outcome/barrier/target_pct",
            ),
            (
                "conditional engine mismatch",
                lambda contract: contract["research"].update({"engine": "freqtrade"}),
                "/research/engine",
            ),
            (
                "non-finite fee",
                lambda contract: contract["execution"].update(
                    {"fee_bps_per_side": float("nan")}
                ),
                "/execution/fee_bps_per_side",
            ),
        )
        for name, mutate, expected_path in cases:
            with self.subTest(name=name):
                contract = load_reference_prompt_contract()
                mutate(contract)
                with self.assertRaisesRegex(
                    PromptCapabilityError, re.escape(expected_path)
                ):
                    validate_contract_schema(contract)

    def test_short_question_requires_grouped_review(self):
        draft = interpret_research_prompt(
            "How often did BTCUSDT bounce from the 9 EMA on 5m over two years?"
        )
        self.assertEqual(draft["status"], "needs_review")
        self.assertEqual(
            {question["question_id"] for question in draft["questions"]},
            {
                "market_data",
                "signal_definition",
                "entry_independence",
                "outcomes",
                "costs_and_sizing",
                "validation",
                "reporting",
            },
        )
        self.assertEqual(draft["candidate_contract"], load_reference_prompt_contract())
        self.assertTrue(
            all(question["proposed_values"] for question in draft["questions"])
        )

    def test_exact_reference_prompt_is_ready_but_not_confirmed(self):
        draft = interpret_research_prompt(REFERENCE_PROMPT)
        self.assertEqual(draft["status"], "ready_for_confirmation")
        self.assertEqual(draft["contract_diff"], [])
        self.assertNotIn("execution_authorized", draft)
        confirmation = confirm_prompt_candidate(
            draft,
            approved_contract_sha256=draft["candidate_contract_sha256"],
        )
        self.assertEqual(confirmation["status"], "confirmed")
        self.assertFalse(confirmation["execution_authorized"])
        self.assertFalse(confirmation["holdout_open_authorized"])

    def test_draft_review_and_confirmation_are_deterministic(self):
        first = interpret_research_prompt(REFERENCE_PROMPT)
        second = interpret_research_prompt(REFERENCE_PROMPT)
        self.assertEqual(first, second)
        first_confirmation = confirm_prompt_candidate(
            first,
            approved_contract_sha256=first["candidate_contract_sha256"],
        )
        second_confirmation = confirm_prompt_candidate(
            second,
            approved_contract_sha256=second["candidate_contract_sha256"],
        )
        self.assertEqual(first_confirmation, second_confirmation)

    def test_structured_answers_resolve_ambiguity(self):
        draft = interpret_research_prompt("Test BTCUSDT EMA9 on 5m.")
        reviewed = review_prompt_draft(
            draft,
            accept_proposed=[
                question["question_id"] for question in draft["questions"]
            ],
            updates={"/execution/fee_bps_per_side": 5.0},
        )
        self.assertEqual(reviewed["status"], "ready_for_confirmation")
        self.assertEqual(
            reviewed["candidate_contract"]["execution"]["fee_bps_per_side"],
            5.0,
        )
        self.assertEqual(reviewed["candidate_contract"]["experiment"]["revision"], 2)

    def test_fee_revision_diff_is_exact(self):
        draft = interpret_research_prompt(
            "Revise the long BTCUSDT 5m EMA9 bounce with fees 5 bps per side "
            "using all remaining frozen reference settings."
        )
        self.assertEqual(draft["status"], "ready_for_confirmation")
        self.assertEqual(
            {change["path"] for change in draft["contract_diff"]},
            {"/execution/fee_bps_per_side", "/experiment/revision"},
        )
        self.assertEqual(draft["candidate_contract"]["experiment"]["revision"], 2)

    def test_wrong_approval_checksum_is_rejected(self):
        draft = interpret_research_prompt(REFERENCE_PROMPT)
        with self.assertRaisesRegex(PromptIntegrityError, "Approval checksum"):
            confirm_prompt_candidate(
                draft,
                approved_contract_sha256="0" * 64,
            )

    def test_tampered_draft_is_rejected(self):
        draft = interpret_research_prompt(REFERENCE_PROMPT)
        tampered = copy.deepcopy(draft)
        tampered["candidate_contract"]["execution"]["fee_bps_per_side"] = 0.0
        with self.assertRaisesRegex(PromptIntegrityError, "draft checksum"):
            confirm_prompt_candidate(
                tampered,
                approved_contract_sha256=tampered["candidate_contract_sha256"],
            )

    def test_unknown_review_paths_and_questions_are_rejected(self):
        draft = interpret_research_prompt("Test BTCUSDT EMA9 on 5m.")
        with self.assertRaisesRegex(PromptEngineError, "cannot modify path"):
            review_prompt_draft(draft, updates={"/live_trading": True})
        with self.assertRaisesRegex(PromptEngineError, "Unknown review"):
            review_prompt_draft(draft, accept_proposed=["skip_all_checks"])

    def test_review_cannot_expand_phase3_capability(self):
        draft = interpret_research_prompt("Test BTCUSDT EMA9 on 5m.")
        with self.assertRaisesRegex(PromptCapabilityError, "implemented capability"):
            review_prompt_draft(
                draft,
                accept_proposed=[
                    question["question_id"] for question in draft["questions"]
                ],
                updates={"/market/direction": "short"},
            )

    def test_review_cannot_unlock_holdout_or_weaken_leakage_controls(self):
        draft = interpret_research_prompt("Test BTCUSDT EMA9 on 5m.")
        accepted = [question["question_id"] for question in draft["questions"]]
        for updates, message in (
            ({"/validation/final_holdout_locked": False}, "holdout"),
            ({"/validation/purge_bars": 0}, "Purge bars"),
            ({"/validation/embargo_bars": 0}, "Embargo bars"),
            ({"/validation/multiple_testing_correction": "none"}, "cannot be disabled"),
        ):
            with self.subTest(updates=updates):
                with self.assertRaisesRegex(PromptCapabilityError, message):
                    review_prompt_draft(
                        draft,
                        accept_proposed=accepted,
                        updates=updates,
                    )

    def test_unsupported_language_is_rejected_before_contract_creation(self):
        for prompt in (
            "Study BTCUSDT 15m EMA9 using all remaining frozen reference settings.",
            "Study BTCUSDT on the 15-minute timeframe with EMA9 using all remaining frozen reference settings.",
            "Study BTCUSDT 5m with a 20-period EMA using all remaining frozen reference settings.",
            "Add RSI 14 to BTCUSDT EMA9.",
            "Grid search every EMA and report the best parameter.",
        ):
            with self.subTest(prompt=prompt):
                result = interpret_research_prompt(prompt)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["rejection_type"], "unsupported_capability")
                self.assertIsNone(result["candidate_contract"])

    def test_adversarial_suite_fails_closed(self):
        suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        for case in suite["adversarial_cases"]:
            with self.subTest(case_id=case["case_id"]):
                result = interpret_research_prompt(case["prompt"])
                self.assertEqual(result["status"], "rejected")
                self.assertIn(
                    result["rejection_type"], {"safety", "unsupported_capability"}
                )
                self.assertIsNone(result["candidate_contract"])
                self.assertTrue(result["findings"])

    def test_reviewed_prompt_suite_matches_expected_contract_states(self):
        suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        for case in suite["reviewed_cases"]:
            with self.subTest(case_id=case["case_id"]):
                result = interpret_research_prompt(case["prompt"])
                self.assertEqual(result["status"], case["expected_status"])
                if "expected_question_ids" in case:
                    self.assertEqual(
                        [item["question_id"] for item in result["questions"]],
                        case["expected_question_ids"],
                    )
                if "expected_diff_paths" in case:
                    self.assertEqual(
                        [item["path"] for item in result["contract_diff"]],
                        case["expected_diff_paths"],
                    )
                if "expected_rejection_type" in case:
                    self.assertEqual(
                        result["rejection_type"],
                        case["expected_rejection_type"],
                    )

    def test_contract_diff_is_stable_and_leaf_scoped(self):
        before = load_reference_prompt_contract()
        after = copy.deepcopy(before)
        after["execution"]["fee_bps_per_side"] = 5.0
        after["outcome"]["forward_horizons_bars"] = [1, 12]
        self.assertEqual(
            contract_diff(before, after),
            [
                {
                    "path": "/execution/fee_bps_per_side",
                    "before": 4.0,
                    "after": 5.0,
                },
                {
                    "path": "/outcome/forward_horizons_bars",
                    "before": [1, 3, 6, 12],
                    "after": [1, 12],
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
