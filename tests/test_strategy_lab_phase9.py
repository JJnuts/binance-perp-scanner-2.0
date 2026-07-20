import tempfile
import unittest
from pathlib import Path

from tools.strategy_lab_phase9.run_stress_suite import (
    _run_cancellation,
    _run_concurrent_isolation,
    _run_corruption_detection,
    _run_engine_failure,
    _run_large_resume,
    _run_reviewer_reproduction,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class StrategyLabPhase9StressTests(unittest.TestCase):
    def test_final_stress_scenarios_pass_without_container_limit_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            resume = _run_large_resume(output)
            concurrency = _run_concurrent_isolation(REPO_ROOT, output)
            cancellation = _run_cancellation(REPO_ROOT, output)
            failure = _run_engine_failure(REPO_ROOT, output)
            corruption = _run_corruption_detection(REPO_ROOT, output)
            reproduction = _run_reviewer_reproduction(REPO_ROOT, output)

            self.assertEqual(resume["status"], "passed")
            self.assertTrue(concurrency["simultaneously_active"])
            self.assertEqual(cancellation["terminal_state"], "cancelled")
            self.assertEqual(failure["terminal_state"], "failed")
            self.assertTrue(corruption["raw_cache_corruption_blocked"])
            self.assertTrue(reproduction["byte_identical_to_phase6_baseline"])


if __name__ == "__main__":
    unittest.main()
