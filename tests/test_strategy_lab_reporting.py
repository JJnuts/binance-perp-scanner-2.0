import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from perpscanner.strategy_lab.reporting import (
    DEFAULT_INPUTS,
    EXPECTED_EXPORT_FILES,
    ExportGateError,
    build_export_bundle,
    sha256_file,
    validate_metric_traceability,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def copy_export_inputs(destination: Path) -> None:
    for _, relative_path in DEFAULT_INPUTS:
        source = REPO_ROOT / relative_path
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


class StrategyLabReportingTests(unittest.TestCase):
    def test_real_bundle_is_complete_and_holdout_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            report = build_export_bundle(REPO_ROOT, output)
            self.assertEqual(report["status"], "passed")
            self.assertFalse(report["holdout_opened"])
            self.assertEqual(report["validation_conclusion"], "negative")
            self.assertEqual(report["claim_level"], "exploratory")
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {*EXPECTED_EXPORT_FILES, "export-manifest.json"},
            )

    def test_manifest_reproduction_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            build_export_bundle(REPO_ROOT, first)
            report = build_export_bundle(
                REPO_ROOT,
                second,
                reproduce_manifest=first / "export-manifest.json",
            )
            self.assertTrue(report["byte_identical_reproduction"])
            for filename in (*EXPECTED_EXPORT_FILES, "export-manifest.json"):
                self.assertEqual(
                    (first / filename).read_bytes(),
                    (second / filename).read_bytes(),
                )

    def test_metric_csv_has_complete_trace_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build_export_bundle(REPO_ROOT, output)
            with (output / "metrics.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            traceability = json.loads(
                (output / "traceability.json").read_text(encoding="utf-8")
            )
            self.assertGreater(len(rows), 40)
            self.assertEqual(traceability["metric_count"], len(rows))
            self.assertTrue(traceability["all_values_pointer_verified"])
            for row in rows:
                self.assertTrue(row["sample"])
                self.assertTrue(row["split"])
                self.assertTrue(row["cost_basis"])
                self.assertEqual(len(row["source_sha256"]), 64)

    def test_html_report_keeps_required_labels_and_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build_export_bundle(REPO_ROOT, output)
            report_html = (output / "report.html").read_text(encoding="utf-8")
            for phrase in (
                "training and validation development sample only",
                "net after declared fees, slippage, and historical funding",
                "Historical empirical",
                "not future probabilities",
                "Deployment promotion:</strong> not authorized",
                "Final-holdout policy: locked outcomes absent",
            ):
                self.assertIn(phrase, report_html)

    def test_tampered_input_is_rejected_by_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            copy_export_inputs(repo)
            bundle = root / "bundle"
            build_export_bundle(repo, bundle)
            report_path = repo / dict(DEFAULT_INPUTS)["phase5_report"]
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["validation_conclusion"] = "positive"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ExportGateError, "checksum changed"):
                build_export_bundle(
                    repo,
                    root / "reproduced",
                    reproduce_manifest=bundle / "export-manifest.json",
                )

    def test_opened_holdout_is_rejected_even_with_updated_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            copy_export_inputs(repo)
            report_path = repo / dict(DEFAULT_INPUTS)["phase5_report"]
            manifest_path = repo / dict(DEFAULT_INPUTS)["phase5_manifest"]
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["holdout"]["holdout_opened"] = True
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["holdout_opened"] = True
            manifest["artifacts"]["validation-report.json"] = sha256_file(report_path)
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ExportGateError, "holdout"):
                build_export_bundle(repo, root / "bundle")

    def test_manifest_output_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            build_export_bundle(REPO_ROOT, bundle)
            manifest_path = bundle / "export-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["outputs"][0]["sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ExportGateError, "byte-identical"):
                build_export_bundle(
                    REPO_ROOT,
                    root / "reproduced",
                    reproduce_manifest=manifest_path,
                )

    def test_untraceable_metric_is_rejected(self):
        metric = {
            "metric_id": "fake",
            "label": "Fake",
            "value": 1,
            "unit": "events",
            "section": "sample",
            "sample": "development",
            "split": "development",
            "cost_basis": "not_applicable",
            "source_artifact": "undeclared.json",
            "source_json_pointer": "/value",
            "source_sha256": "0" * 64,
            "contract_refs": ["/validation"],
        }
        contract_path = dict(DEFAULT_INPUTS)["contract"]
        records = [
            {
                "role": "contract",
                "path": contract_path,
                "sha256": sha256_file(REPO_ROOT / contract_path),
            }
        ]
        with self.assertRaisesRegex(ExportGateError, "undeclared source"):
            validate_metric_traceability(REPO_ROOT, [metric], records)


if __name__ == "__main__":
    unittest.main()
