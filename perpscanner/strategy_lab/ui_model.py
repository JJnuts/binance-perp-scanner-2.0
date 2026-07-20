"""Pure presentation model for Strategy Lab Streamlit results."""

from __future__ import annotations

from typing import Any


PRIMARY_METRIC_IDS = (
    "development_events",
    "historical_success_rate",
    "forward_12_mean_net_return",
    "hidden_holdout_events",
)


def format_metric_value(metric: dict[str, Any]) -> str:
    value = metric["value"]
    unit = metric["unit"]
    if unit == "fraction":
        return f"{float(value) * 100:.2f}%"
    if unit in {"events", "folds", "hypotheses", "checks", "samples"}:
        return f"{float(value):,.0f}"
    if unit == "basis_points":
        return f"{float(value):,.2f} bps"
    if unit == "USDT":
        return f"{float(value):,.2f} USDT"
    if unit == "p_value":
        return f"{float(value):.4f}"
    if unit == "boolean":
        return "Yes" if bool(value) else "No"
    if isinstance(value, float):
        return f"{value:,.4f}"
    return str(value)


def build_results_view(results: dict[str, Any]) -> dict[str, Any]:
    if results.get("conclusion", {}).get("framework_status") != "passed":
        raise ValueError("Strategy Lab result did not pass its framework gate")
    metrics = {metric["metric_id"]: metric for metric in results.get("metrics", [])}
    missing = (set(PRIMARY_METRIC_IDS) | {"holdout_opened"}) - set(metrics)
    if missing:
        raise ValueError(
            f"Strategy Lab result lacks required metrics: {sorted(missing)}"
        )
    cards = [
        {
            "metric_id": metric_id,
            "label": metrics[metric_id]["label"],
            "value": format_metric_value(metrics[metric_id]),
            "help": (
                f"Sample: {metrics[metric_id]['sample']}. "
                f"Split: {metrics[metric_id]['split']}. "
                f"Cost basis: {metrics[metric_id]['cost_basis']}."
            ),
        }
        for metric_id in PRIMARY_METRIC_IDS
    ]
    warnings = []
    for warning in results.get("warnings", []):
        if warning not in warnings:
            warnings.append(warning)
    rows = [
        {
            "Metric": metric["label"],
            "Value": format_metric_value(metric),
            "Unit": metric["unit"],
            "Split": metric["split"],
            "Cost basis": metric["cost_basis"],
        }
        for metric in results["metrics"]
    ]
    return {
        "cards": cards,
        "conclusion": results["conclusion"],
        "warnings": warnings,
        "metric_rows": rows,
        "holdout_locked": not bool(metrics["holdout_opened"]["value"]),
    }


def job_state_label(state: str) -> tuple[str, str, str]:
    labels = {
        "queued": ("Queued", "orange", "schedule"),
        "running": ("Running", "blue", "progress_activity"),
        "cancellation_requested": ("Cancelling", "orange", "cancel"),
        "cancelled": ("Cancelled", "grey", "cancel"),
        "completed": ("Completed", "green", "check_circle"),
        "failed": ("Failed", "red", "error"),
    }
    if state not in labels:
        raise ValueError(f"Unknown Strategy Lab job state: {state}")
    return labels[state]
