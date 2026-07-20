"""Deterministic, traceable Strategy Lab result exports."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .binance_data import _atomic_write, _pretty_json_bytes


REPORT_VERSION = "0.1.0"
EXPECTED_EXPORT_FILES = (
    "metrics.csv",
    "report.html",
    "results.json",
    "traceability.json",
)
DEFAULT_INPUTS = (
    (
        "contract",
        "docs/strategy_lab/examples/btcusdt_ema9_bounce.event-study.json",
    ),
    (
        "phase3_summary",
        "tools/strategy_lab_phase3/artifacts/pandas/summary.json",
    ),
    (
        "phase3_manifest",
        "tools/strategy_lab_phase3/artifacts/pandas/run-manifest.json",
    ),
    (
        "phase5_report",
        "tools/strategy_lab_phase5/artifacts/validation-report.json",
    ),
    (
        "phase5_manifest",
        "tools/strategy_lab_phase5/artifacts/run-manifest.json",
    ),
)


class ExportGateError(RuntimeError):
    """Raised when a result bundle cannot satisfy an export gate."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _safe_repo_path(repo_root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ExportGateError(f"Unsafe repository-relative path: {relative_path}")
    root = repo_root.resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ExportGateError(f"Path escapes repository root: {relative_path}")
    return resolved


def _json_pointer_get(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ExportGateError(f"Invalid JSON pointer: {pointer}")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ExportGateError(
                    f"JSON pointer does not resolve: {pointer}"
                ) from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ExportGateError(f"JSON pointer does not resolve: {pointer}")
    return current


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=0.0)
    return left == right


def _load_inputs(
    repo_root: Path,
    records: Iterable[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if records is None:
        source_records = [{"role": role, "path": path} for role, path in DEFAULT_INPUTS]
    else:
        source_records = [dict(record) for record in records]
    expected_roles = [role for role, _ in DEFAULT_INPUTS]
    roles = [record.get("role") for record in source_records]
    if roles != expected_roles:
        raise ExportGateError(
            f"Export input roles must be exactly {expected_roles}; received {roles}"
        )

    documents: dict[str, Any] = {}
    verified_records: list[dict[str, str]] = []
    for record in source_records:
        if set(record) - {"role", "path", "sha256"}:
            raise ExportGateError(f"Unknown input record fields: {sorted(record)}")
        role = str(record["role"])
        relative_path = str(record["path"]).replace("\\", "/")
        path = _safe_repo_path(repo_root, relative_path)
        if not path.is_file():
            raise ExportGateError(f"Required export input is missing: {relative_path}")
        actual_sha256 = sha256_file(path)
        expected_sha256 = record.get("sha256")
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ExportGateError(
                f"Input checksum changed for {relative_path}: "
                f"{actual_sha256} != {expected_sha256}"
            )
        try:
            documents[role] = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportGateError(
                f"Input is not valid UTF-8 JSON: {relative_path}"
            ) from exc
        verified_records.append(
            {"role": role, "path": relative_path, "sha256": actual_sha256}
        )
    return documents, verified_records


def _validate_source_chain(
    documents: dict[str, Any],
    records: list[dict[str, str]],
) -> None:
    by_role = {record["role"]: record for record in records}
    contract_sha256 = by_role["contract"]["sha256"]
    phase3_summary_sha256 = by_role["phase3_summary"]["sha256"]
    phase3_manifest_sha256 = by_role["phase3_manifest"]["sha256"]
    phase5_report_sha256 = by_role["phase5_report"]["sha256"]
    phase3_manifest = documents["phase3_manifest"]
    phase5_manifest = documents["phase5_manifest"]
    phase3_summary = documents["phase3_summary"]
    phase5_report = documents["phase5_report"]
    contract = documents["contract"]

    checks = (
        (
            phase3_manifest.get("contract_sha256") == contract_sha256,
            "Phase 3 contract checksum does not match the export contract",
        ),
        (
            phase5_manifest.get("contract_sha256") == contract_sha256,
            "Phase 5 contract checksum does not match the export contract",
        ),
        (
            phase3_manifest.get("summary_sha256") == phase3_summary_sha256,
            "Phase 3 summary checksum does not match its run manifest",
        ),
        (
            phase5_manifest.get("phase3_manifest_sha256") == phase3_manifest_sha256,
            "Phase 5 does not reference the supplied Phase 3 manifest",
        ),
        (
            phase5_manifest.get("artifacts", {}).get("validation-report.json")
            == phase5_report_sha256,
            "Phase 5 validation report checksum does not match its run manifest",
        ),
        (
            phase3_summary.get("status") == "passed",
            "Phase 3 summary did not pass",
        ),
        (
            phase5_report.get("status") == "passed"
            and phase5_manifest.get("status") == "passed",
            "Phase 5 validation did not pass",
        ),
        (
            phase5_report.get("holdout", {}).get("holdout_opened") is False
            and phase5_manifest.get("holdout_opened") is False,
            "Final-holdout outcomes were opened; development export is blocked",
        ),
        (
            phase3_summary.get("holdout_policy") == "final holdout outcomes hidden",
            "Phase 3 summary does not preserve the holdout lock",
        ),
        (
            contract.get("reporting", {}).get("probability_label")
            == "historical_empirical_rate",
            "Contract probability terminology is not export-safe",
        ),
        (
            set(contract.get("reporting", {}).get("export_formats", []))
            == {"json", "csv", "html"},
            "Contract export formats must be JSON, CSV, and HTML",
        ),
    )
    for passed, message in checks:
        if not passed:
            raise ExportGateError(message)


def _metric(
    *,
    metric_id: str,
    label: str,
    value: Any,
    unit: str,
    section: str,
    sample: str,
    split: str,
    cost_basis: str,
    source_role: str,
    source_pointer: str,
    source_records: dict[str, dict[str, str]],
    contract_refs: Iterable[str],
) -> dict[str, Any]:
    source = source_records[source_role]
    return {
        "metric_id": metric_id,
        "label": label,
        "value": value,
        "unit": unit,
        "section": section,
        "sample": sample,
        "split": split,
        "cost_basis": cost_basis,
        "source_artifact": source["path"],
        "source_json_pointer": source_pointer,
        "source_sha256": source["sha256"],
        "contract_refs": list(contract_refs),
    }


def _build_metrics(
    documents: dict[str, Any],
    records: list[dict[str, str]],
) -> list[dict[str, Any]]:
    summary = documents["phase3_summary"]
    report = documents["phase5_report"]
    contract = documents["contract"]
    sources = {record["role"]: record for record in records}
    metrics: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        metrics.append(_metric(source_records=sources, **kwargs))

    summary_specs = (
        (
            "accepted_events",
            "Accepted event-log rows",
            "accepted_event_count",
            "events",
        ),
        (
            "development_events",
            "Development events evaluated",
            "development_event_count",
            "events",
        ),
        (
            "hidden_holdout_events",
            "Final-holdout events kept hidden",
            "final_holdout_event_count_hidden",
            "events",
        ),
        (
            "resolved_barrier_events",
            "Resolved development barrier outcomes",
            "resolved_event_count",
            "events",
        ),
        (
            "target_events",
            "Target-first development outcomes",
            "target_count",
            "events",
        ),
        (
            "historical_success_rate",
            "Historical empirical target-first rate",
            "historical_empirical_success_rate",
            "fraction",
        ),
        (
            "ambiguous_events",
            "Ambiguous higher-timeframe events",
            "ambiguous_event_count",
            "events",
        ),
        (
            "overlap_skipped_events",
            "Candidate events excluded by overlap policy",
            "overlap_skipped_count",
            "events",
        ),
        (
            "cooldown_skipped_events",
            "Candidate events excluded by cooldown",
            "cooldown_skipped_count",
            "events",
        ),
        (
            "incomplete_outcome_events",
            "Events excluded for incomplete outcomes",
            "incomplete_outcome_skipped_count",
            "events",
        ),
    )
    exclusion_ids = {
        "ambiguous_events",
        "overlap_skipped_events",
        "cooldown_skipped_events",
        "incomplete_outcome_events",
    }
    for metric_id, label, key, unit in summary_specs:
        add(
            metric_id=metric_id,
            label=label,
            value=summary[key],
            unit=unit,
            section="exclusions" if metric_id in exclusion_ids else "sample",
            sample="BTCUSDT 5m accepted event log",
            split="development"
            if metric_id != "hidden_holdout_events"
            else "final_holdout_locked",
            cost_basis="not_applicable"
            if unit == "events"
            else "net_after_declared_costs",
            source_role="phase3_summary",
            source_pointer=f"/{key}",
            contract_refs=("/entry", "/outcome", "/validation"),
        )

    validation_specs = (
        (
            "walk_forward_fold_count",
            "Anchored walk-forward folds",
            "/walk_forward/fold_count",
            report["walk_forward"]["fold_count"],
            "folds",
        ),
        (
            "corrected_hypothesis_count",
            "Declared corrected hypotheses",
            "/multiple_testing/hypothesis_count",
            report["multiple_testing"]["hypothesis_count"],
            "hypotheses",
        ),
        (
            "corrected_positive_hypotheses",
            "Positive hypotheses after correction",
            "/multiple_testing/rejected_hypothesis_count",
            report["multiple_testing"]["rejected_hypothesis_count"],
            "hypotheses",
        ),
        (
            "adversarial_checks_rejected",
            "Deliberately bad inputs rejected",
            "/adversarial_checks/rejected_check_count",
            report["adversarial_checks"]["rejected_check_count"],
            "checks",
        ),
        (
            "holdout_opened",
            "Final holdout opened",
            "/holdout/holdout_opened",
            report["holdout"]["holdout_opened"],
            "boolean",
        ),
    )
    for metric_id, label, pointer, value, unit in validation_specs:
        add(
            metric_id=metric_id,
            label=label,
            value=value,
            unit=unit,
            section="validation",
            sample="development validation process",
            split="training_and_validation_only",
            cost_basis="not_applicable",
            source_role="phase5_report",
            source_pointer=pointer,
            contract_refs=("/validation",),
        )

    for horizon in contract["outcome"]["forward_horizons_bars"]:
        horizon_key = str(horizon)
        values = report["uncertainty_by_horizon"][horizon_key]
        base = f"/uncertainty_by_horizon/{horizon_key}"
        horizon_specs = (
            (
                "mean_net_return",
                "Mean net return",
                values["mean_net_return"],
                "fraction",
                f"{base}/mean_net_return",
            ),
            (
                "median_net_return",
                "Median net return",
                values["median_net_return"],
                "fraction",
                f"{base}/median_net_return",
            ),
            (
                "mean_ci_low",
                "Newey-West mean CI low",
                values["newey_west"]["confidence_interval"][0],
                "fraction",
                f"{base}/newey_west/confidence_interval/0",
            ),
            (
                "mean_ci_high",
                "Newey-West mean CI high",
                values["newey_west"]["confidence_interval"][1],
                "fraction",
                f"{base}/newey_west/confidence_interval/1",
            ),
            (
                "one_sided_p_value",
                "One-sided p-value for positive mean",
                values["newey_west"]["p_value_one_sided_greater"],
                "p_value",
                f"{base}/newey_west/p_value_one_sided_greater",
            ),
            (
                "effective_sample_size",
                "Effective sample size",
                values["newey_west"]["effective_sample_size"],
                "events",
                f"{base}/newey_west/effective_sample_size",
            ),
        )
        for suffix, label, value, unit, pointer in horizon_specs:
            add(
                metric_id=f"forward_{horizon}_{suffix}",
                label=f"{horizon}-bar {label}",
                value=value,
                unit=unit,
                section="forward_returns",
                sample="development events with available outcomes",
                split="training_and_validation_only",
                cost_basis="net_after_fee_slippage_and_funding",
                source_role="phase5_report",
                source_pointer=pointer,
                contract_refs=(
                    "/outcome/forward_horizons_bars",
                    "/execution",
                    "/validation",
                ),
            )

    for fold_index, fold in enumerate(report["walk_forward"]["folds"]):
        fold_id = fold["fold_id"]
        fold_specs = (
            (
                "training_event_count",
                "Training event count",
                fold["training"]["event_count"],
                "events",
                f"/walk_forward/folds/{fold_index}/training/event_count",
                "training",
            ),
            (
                "validation_event_count",
                "Validation event count",
                fold["validation"]["event_count"],
                "events",
                f"/walk_forward/folds/{fold_index}/validation/event_count",
                "validation",
            ),
            (
                "training_mean_net_return",
                "Training mean net return",
                fold["training"]["mean_net_return"],
                "fraction",
                f"/walk_forward/folds/{fold_index}/training/mean_net_return",
                "training",
            ),
            (
                "validation_mean_net_return",
                "Validation mean net return",
                fold["validation"]["mean_net_return"],
                "fraction",
                f"/walk_forward/folds/{fold_index}/validation/mean_net_return",
                "validation",
            ),
            (
                "purged_training_events",
                "Purged training events",
                fold["purged_training_events"],
                "events",
                f"/walk_forward/folds/{fold_index}/purged_training_events",
                "training",
            ),
            (
                "embargoed_validation_events",
                "Embargoed validation events",
                fold["embargoed_validation_events"],
                "events",
                f"/walk_forward/folds/{fold_index}/embargoed_validation_events",
                "validation",
            ),
        )
        for suffix, label, value, unit, pointer, split in fold_specs:
            add(
                metric_id=f"{fold_id}_{suffix}",
                label=f"{fold_id}: {label}",
                value=value,
                unit=unit,
                section="walk_forward",
                sample=f"anchored walk-forward {fold_id}",
                split=split,
                cost_basis=(
                    "net_after_fee_slippage_and_funding"
                    if unit == "fraction"
                    else "not_applicable"
                ),
                source_role="phase5_report",
                source_pointer=pointer,
                contract_refs=("/validation", "/execution"),
            )

    contract_specs = (
        (
            "fee_bps_per_side",
            "Fee assumption per side",
            contract["execution"]["fee_bps_per_side"],
            "basis_points",
            "/execution/fee_bps_per_side",
        ),
        (
            "slippage_bps_per_side",
            "Slippage assumption per side",
            contract["execution"]["slippage_bps_per_side"],
            "basis_points",
            "/execution/slippage_bps_per_side",
        ),
        (
            "fixed_notional",
            "Fixed position notional",
            contract["execution"]["position_sizing"]["value"],
            "USDT",
            "/execution/position_sizing/value",
        ),
        (
            "minimum_events",
            "Minimum event requirement",
            contract["validation"]["minimum_events"],
            "events",
            "/validation/minimum_events",
        ),
        (
            "bootstrap_samples",
            "Bootstrap samples",
            contract["validation"]["bootstrap_samples"],
            "samples",
            "/validation/bootstrap_samples",
        ),
    )
    for metric_id, label, value, unit, pointer in contract_specs:
        add(
            metric_id=metric_id,
            label=label,
            value=value,
            unit=unit,
            section="assumptions",
            sample="confirmed research contract",
            split="all_declared_splits",
            cost_basis="declared_input",
            source_role="contract",
            source_pointer=pointer,
            contract_refs=(pointer,),
        )
    return metrics


def validate_metric_traceability(
    repo_root: Path,
    metrics: Iterable[dict[str, Any]],
    source_records: Iterable[dict[str, str]],
) -> dict[str, Any]:
    record_list = list(source_records)
    records = {record["path"]: record for record in record_list}
    contract_records = [
        record for record in record_list if record.get("role") == "contract"
    ]
    if len(contract_records) != 1:
        raise ExportGateError("Exactly one contract source must be declared")
    contract_record = contract_records[0]
    contract_path = _safe_repo_path(repo_root, contract_record["path"])
    if sha256_file(contract_path) != contract_record["sha256"]:
        raise ExportGateError("Contract checksum changed")
    contract_document = json.loads(contract_path.read_text(encoding="utf-8"))
    source_documents: dict[str, Any] = {}
    metric_list = list(metrics)
    metric_ids = [metric.get("metric_id") for metric in metric_list]
    if len(metric_ids) != len(set(metric_ids)):
        raise ExportGateError("Metric identifiers are not unique")
    required = {
        "metric_id",
        "label",
        "value",
        "unit",
        "section",
        "sample",
        "split",
        "cost_basis",
        "source_artifact",
        "source_json_pointer",
        "source_sha256",
        "contract_refs",
    }
    for metric in metric_list:
        if set(metric) != required:
            raise ExportGateError(
                f"Metric {metric.get('metric_id')} has incomplete trace fields"
            )
        if not metric["sample"] or not metric["split"] or not metric["cost_basis"]:
            raise ExportGateError(
                f"Metric {metric['metric_id']} lacks sample, split, or cost labels"
            )
        if not metric["contract_refs"]:
            raise ExportGateError(
                f"Metric {metric['metric_id']} lacks contract references"
            )
        for contract_pointer in metric["contract_refs"]:
            _json_pointer_get(contract_document, contract_pointer)
        source_path = metric["source_artifact"]
        if source_path not in records:
            raise ExportGateError(
                f"Metric {metric['metric_id']} references an undeclared source"
            )
        record = records[source_path]
        if metric["source_sha256"] != record["sha256"]:
            raise ExportGateError(
                f"Metric {metric['metric_id']} has the wrong source checksum"
            )
        if source_path not in source_documents:
            path = _safe_repo_path(repo_root, source_path)
            if sha256_file(path) != record["sha256"]:
                raise ExportGateError(f"Source checksum changed: {source_path}")
            source_documents[source_path] = json.loads(path.read_text(encoding="utf-8"))
        source_value = _json_pointer_get(
            source_documents[source_path],
            metric["source_json_pointer"],
        )
        if not _values_equal(metric["value"], source_value):
            raise ExportGateError(
                f"Metric {metric['metric_id']} does not match its source value"
            )
    return {
        "status": "passed",
        "metric_count": len(metric_list),
        "unique_metric_count": len(set(metric_ids)),
        "all_sources_checksum_verified": True,
        "all_values_pointer_verified": True,
        "all_contract_refs_verified": True,
        "all_metrics_labeled": True,
    }


def _build_results(
    documents: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    contract = documents["contract"]
    report = documents["phase5_report"]
    summary = documents["phase3_summary"]
    warnings = list(
        dict.fromkeys(
            [
                *summary.get("warnings", []),
                *report.get("warnings", []),
                "This export does not authorize deployment or live trading.",
            ]
        )
    )
    groups: dict[str, list[str]] = {}
    for metric in metrics:
        groups.setdefault(metric["section"], []).append(metric["metric_id"])
    return {
        "report_version": REPORT_VERSION,
        "experiment": {
            "id": contract["experiment"]["id"],
            "revision": contract["experiment"]["revision"],
            "name": contract["experiment"]["name"],
            "hypothesis": contract["experiment"]["hypothesis"],
            "market": contract["market"],
            "timeframe": contract["data"]["timeframe"],
            "period": {
                "start": contract["data"]["start"],
                "end": contract["data"]["end"],
                "timezone": contract["data"]["timezone"],
            },
        },
        "conclusion": {
            "framework_status": report["status"],
            "validation_conclusion": report["validation_conclusion"],
            "claim_level": report["claim_level"],
            "promotion_authorized": False,
            "plain_language": (
                "The validation framework passed, but the frozen EMA9 "
                "hypothesis had negative development evidence after declared "
                "costs. The final holdout remains unopened."
            ),
        },
        "sample": {
            "label": "training and validation development sample only",
            "holdout_policy": "locked outcomes absent",
            "metric_ids": groups["sample"],
        },
        "costs": {
            "basis": "net after declared fees, slippage, and historical funding",
            "funding_included": contract["execution"]["funding"]["include"],
            "funding_source": contract["execution"]["funding"]["source"],
            "metric_ids": groups["assumptions"],
        },
        "validation": {
            "method": contract["validation"]["method"],
            "multiple_testing_correction": contract["validation"][
                "multiple_testing_correction"
            ],
            "metric_ids": groups["validation"],
            "walk_forward_metric_ids": groups["walk_forward"],
        },
        "outcomes": {
            "probability_label": contract["reporting"]["probability_label"],
            "metric_ids": groups["forward_returns"],
        },
        "exclusions": {
            "label": "explicitly excluded or separately counted observations",
            "metric_ids": groups["exclusions"],
        },
        "warnings": warnings,
        "metrics": metrics,
    }


def _metrics_csv_bytes(metrics: Iterable[dict[str, Any]]) -> bytes:
    columns = (
        "metric_id",
        "label",
        "value",
        "unit",
        "section",
        "sample",
        "split",
        "cost_basis",
        "source_artifact",
        "source_json_pointer",
        "source_sha256",
        "contract_refs",
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for metric in metrics:
        row = dict(metric)
        row["value"] = json.dumps(metric["value"], ensure_ascii=False, allow_nan=False)
        row["contract_refs"] = json.dumps(
            metric["contract_refs"], separators=(",", ":"), ensure_ascii=False
        )
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _format_value(metric: dict[str, Any]) -> str:
    value = metric["value"]
    unit = metric["unit"]
    if isinstance(value, bool):
        return "yes" if value else "no"
    if unit == "fraction":
        return f"{float(value) * 100:.4f}%"
    if unit == "p_value":
        return f"{float(value):.6g}"
    if unit in {"events", "folds", "hypotheses", "checks", "samples"}:
        return (
            f"{float(value):,.2f}"
            if not float(value).is_integer()
            else f"{int(value):,}"
        )
    if unit == "basis_points":
        return f"{float(value):g} bps"
    if unit == "USDT":
        return f"{float(value):,.2f} USDT"
    return str(value)


def _report_html_bytes(
    results: dict[str, Any],
    metrics: list[dict[str, Any]],
    records: list[dict[str, str]],
) -> bytes:
    by_id = {metric["metric_id"]: metric for metric in metrics}

    def metric_rows(metric_ids: Iterable[str]) -> str:
        rows = []
        for metric_id in metric_ids:
            metric = by_id[metric_id]
            rows.append(
                "<tr>"
                f"<td>{html.escape(metric['label'])}</td>"
                f"<td>{html.escape(_format_value(metric))}</td>"
                f"<td>{html.escape(metric['sample'])}</td>"
                f"<td>{html.escape(metric['split'])}</td>"
                f"<td>{html.escape(metric['cost_basis'])}</td>"
                f"<td><code>{html.escape(metric['source_artifact'])}"
                f"{html.escape(metric['source_json_pointer'])}</code></td>"
                "</tr>"
            )
        return "".join(rows)

    sample_ids = results["sample"]["metric_ids"]
    forward_ids = results["outcomes"]["metric_ids"]
    validation_ids = [
        *results["validation"]["metric_ids"],
        *results["validation"]["walk_forward_metric_ids"],
    ]
    assumption_ids = results["costs"]["metric_ids"]
    exclusion_ids = results["exclusions"]["metric_ids"]
    warnings = "".join(
        f"<li>{html.escape(warning)}</li>" for warning in results["warnings"]
    )
    sources = "".join(
        "<tr>"
        f"<td>{html.escape(record['role'])}</td>"
        f"<td><code>{html.escape(record['path'])}</code></td>"
        f"<td><code>{html.escape(record['sha256'])}</code></td>"
        "</tr>"
        for record in records
    )
    conclusion = results["conclusion"]
    experiment = results["experiment"]
    content = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(experiment["name"])} - Strategy Lab report</title>
<style>
:root {{ color-scheme: light; font-family: Inter, system-ui, sans-serif; }}
body {{ margin: 0 auto; max-width: 1180px; padding: 32px; color: #15202b; background: #f5f7fa; }}
h1, h2 {{ color: #102a43; }}
.card {{ background: white; border: 1px solid #d9e2ec; border-radius: 10px; padding: 20px; margin: 18px 0; }}
.negative {{ color: #8b1e1e; background: #fff1f1; border-left: 5px solid #c53030; }}
.meta {{ color: #52616b; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
th, td {{ border-bottom: 1px solid #d9e2ec; padding: 9px; text-align: left; vertical-align: top; }}
th {{ background: #eaf0f6; }}
code {{ overflow-wrap: anywhere; font-size: 0.82rem; }}
.scroll {{ overflow-x: auto; }}
</style>
</head>
<body>
<h1>{html.escape(experiment["name"])}</h1>
<p class="meta">Experiment {html.escape(experiment["id"])}, revision {experiment["revision"]} · {html.escape(experiment["period"]["start"])} to {html.escape(experiment["period"]["end"])} · {html.escape(experiment["period"]["timezone"])}</p>
<section class="card negative">
<h2>Conclusion: {html.escape(conclusion["validation_conclusion"])}</h2>
<p>{html.escape(conclusion["plain_language"])}</p>
<p><strong>Claim level:</strong> {html.escape(conclusion["claim_level"])}. <strong>Deployment promotion:</strong> not authorized.</p>
</section>
<section class="card"><h2>Sample and holdout</h2><p>{html.escape(results["sample"]["label"])}. Final-holdout policy: {html.escape(results["sample"]["holdout_policy"])}.</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Sample</th><th>Split</th><th>Cost basis</th><th>Trace</th></tr></thead><tbody>{metric_rows(sample_ids)}</tbody></table></div></section>
<section class="card"><h2>Costs and declared assumptions</h2><p>{html.escape(results["costs"]["basis"])}. Funding source: {html.escape(results["costs"]["funding_source"])}.</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Sample</th><th>Split</th><th>Cost basis</th><th>Trace</th></tr></thead><tbody>{metric_rows(assumption_ids)}</tbody></table></div></section>
<section class="card"><h2>Forward-return evidence</h2><p>Rates are labeled as historical empirical results, not future probabilities.</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Sample</th><th>Split</th><th>Cost basis</th><th>Trace</th></tr></thead><tbody>{metric_rows(forward_ids)}</tbody></table></div></section>
<section class="card"><h2>Walk-forward and validation</h2><p>Method: {html.escape(results["validation"]["method"])}. Multiple-testing correction: {html.escape(results["validation"]["multiple_testing_correction"])}.</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Sample</th><th>Split</th><th>Cost basis</th><th>Trace</th></tr></thead><tbody>{metric_rows(validation_ids)}</tbody></table></div></section>
<section class="card"><h2>Exclusions</h2><p>{html.escape(results["exclusions"]["label"])}.</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Sample</th><th>Split</th><th>Cost basis</th><th>Trace</th></tr></thead><tbody>{metric_rows(exclusion_ids)}</tbody></table></div></section>
<section class="card"><h2>Warnings</h2><ul>{warnings}</ul></section>
<section class="card"><h2>Source artifacts</h2><div class="scroll"><table><thead><tr><th>Role</th><th>Repository path</th><th>SHA-256</th></tr></thead><tbody>{sources}</tbody></table></div></section>
</body>
</html>
"""
    return content.encode("utf-8")


def _validate_results_presentation(results: dict[str, Any]) -> None:
    if results["conclusion"]["promotion_authorized"] is not False:
        raise ExportGateError("A development report cannot authorize promotion")
    if results["sample"]["holdout_policy"] != "locked outcomes absent":
        raise ExportGateError("Final-holdout labeling is missing")
    if results["outcomes"]["probability_label"] != "historical_empirical_rate":
        raise ExportGateError("Historical-rate terminology is missing")
    if not results["costs"]["basis"] or not results["exclusions"]["label"]:
        raise ExportGateError("Cost or exclusion labels are missing")
    if not results["warnings"]:
        raise ExportGateError("Warnings cannot be omitted from an export")
    if not any("not a future probability" in item for item in results["warnings"]):
        raise ExportGateError("Required probability warning is missing")


def _build_manifest(
    documents: dict[str, Any],
    records: list[dict[str, str]],
    output_hashes: dict[str, str],
) -> dict[str, Any]:
    contract = documents["contract"]
    report = documents["phase5_report"]
    identity_payload = {
        "report_version": REPORT_VERSION,
        "inputs": records,
        "export_formats": contract["reporting"]["export_formats"],
    }
    bundle_id = _sha256_bytes(_canonical_json_bytes(identity_payload))
    return {
        "manifest_version": REPORT_VERSION,
        "status": "passed",
        "bundle_id": bundle_id,
        "experiment_id": contract["experiment"]["id"],
        "experiment_revision": contract["experiment"]["revision"],
        "claim_level": report["claim_level"],
        "validation_conclusion": report["validation_conclusion"],
        "holdout_opened": report["holdout"]["holdout_opened"],
        "inputs": records,
        "outputs": [
            {"path": path, "sha256": output_hashes[path]}
            for path in sorted(output_hashes)
        ],
        "reproducibility": {
            "mode": "byte_identical",
            "hash_algorithm": "sha256",
            "numeric_tolerance": 0.0,
            "generated_timestamp_in_outputs": False,
            "command_template": (
                "python tools/strategy_lab_phase6/run_export.py "
                "--repo-root <repo> --output <output> "
                "--manifest <export-manifest.json>"
            ),
        },
    }


def _validate_expected_manifest(manifest: dict[str, Any]) -> None:
    required = {
        "manifest_version",
        "status",
        "bundle_id",
        "experiment_id",
        "experiment_revision",
        "claim_level",
        "validation_conclusion",
        "holdout_opened",
        "inputs",
        "outputs",
        "reproducibility",
    }
    if set(manifest) != required:
        raise ExportGateError("Export manifest fields are incomplete or unknown")
    if manifest["manifest_version"] != REPORT_VERSION:
        raise ExportGateError("Unsupported export manifest version")
    output_paths = [item.get("path") for item in manifest["outputs"]]
    if output_paths != sorted(EXPECTED_EXPORT_FILES):
        raise ExportGateError(
            f"Manifest outputs must be exactly {sorted(EXPECTED_EXPORT_FILES)}"
        )
    if manifest["holdout_opened"] is not False:
        raise ExportGateError("Manifest indicates that final holdout was opened")


def build_export_bundle(
    repo_root: Path,
    output_dir: Path,
    *,
    reproduce_manifest: Path | None = None,
) -> dict[str, Any]:
    """Build a byte-stable export bundle, optionally verifying a prior manifest."""

    root = repo_root.resolve()
    destination = output_dir.resolve()
    expected_manifest: dict[str, Any] | None = None
    expected_manifest_bytes: bytes | None = None
    input_records: Iterable[dict[str, str]] | None = None
    if reproduce_manifest is not None:
        expected_manifest_bytes = reproduce_manifest.read_bytes()
        try:
            expected_manifest = json.loads(expected_manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportGateError("Reproduction manifest is not valid JSON") from exc
        _validate_expected_manifest(expected_manifest)
        input_records = expected_manifest["inputs"]

    documents, records = _load_inputs(root, input_records)
    _validate_source_chain(documents, records)
    metrics = _build_metrics(documents, records)
    trace_status = validate_metric_traceability(root, metrics, records)
    results = _build_results(documents, metrics)
    _validate_results_presentation(results)
    traceability = {
        **trace_status,
        "report_version": REPORT_VERSION,
        "source_artifacts": records,
        "metrics": [
            {
                "metric_id": metric["metric_id"],
                "source_artifact": metric["source_artifact"],
                "source_json_pointer": metric["source_json_pointer"],
                "source_sha256": metric["source_sha256"],
                "contract_refs": metric["contract_refs"],
            }
            for metric in metrics
        ],
    }

    destination.mkdir(parents=True, exist_ok=True)
    payloads = {
        "metrics.csv": _metrics_csv_bytes(metrics),
        "report.html": _report_html_bytes(results, metrics, records),
        "results.json": _pretty_json_bytes(results),
        "traceability.json": _pretty_json_bytes(traceability),
    }
    output_hashes: dict[str, str] = {}
    for filename, payload in payloads.items():
        path = destination / filename
        _atomic_write(path, payload)
        output_hashes[filename] = _sha256_bytes(payload)
    manifest = _build_manifest(documents, records, output_hashes)
    manifest_bytes = _pretty_json_bytes(manifest)

    if expected_manifest is not None:
        if manifest != expected_manifest or manifest_bytes != expected_manifest_bytes:
            raise ExportGateError(
                "Rebuilt export manifest is not byte-identical to the supplied manifest"
            )
    _atomic_write(destination / "export-manifest.json", manifest_bytes)
    return {
        "status": "passed",
        "bundle_id": manifest["bundle_id"],
        "metric_count": len(metrics),
        "source_artifact_count": len(records),
        "export_file_count": len(payloads) + 1,
        "byte_identical_reproduction": reproduce_manifest is not None,
        "holdout_opened": False,
        "validation_conclusion": manifest["validation_conclusion"],
        "claim_level": manifest["claim_level"],
    }
