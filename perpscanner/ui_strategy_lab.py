"""Streamlit glue for the isolated Strategy Lab workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = (
    "Study the long BTCUSDT 5m EMA 9 bounce from 2024-07-01 to "
    "2026-07-01 using all remaining frozen reference settings."
)


def _store_root() -> Path:
    override = os.environ.get("STRATEGY_LAB_STORE_ROOT")
    if override:
        return Path(override).resolve()
    return REPO_ROOT / "data" / "strategy_lab" / "ui"


def _initialize_lab_state(st: Any, store: Any) -> None:
    st.session_state.setdefault("lab_prompt", DEFAULT_PROMPT)
    st.session_state.setdefault("lab_draft_id", None)
    st.session_state.setdefault("lab_confirmation_id", None)
    st.session_state.setdefault("lab_job_id", None)
    if st.session_state.get("lab_state_initialized"):
        return
    drafts = store.list_drafts()
    confirmations = store.list_confirmations()
    jobs = store.list_jobs()
    if jobs:
        job = jobs[0]
        confirmation = store.load_confirmation(job["confirmation_id"])
        draft = store.load_draft(confirmation["draft_id"])
        st.session_state["lab_job_id"] = job["job_id"]
        st.session_state["lab_confirmation_id"] = confirmation["confirmation_id"]
        st.session_state["lab_draft_id"] = draft["draft_id"]
        st.session_state["lab_prompt"] = draft["prompt"]
    elif confirmations:
        confirmation = confirmations[0]
        draft = store.load_draft(confirmation["draft_id"])
        st.session_state["lab_confirmation_id"] = confirmation["confirmation_id"]
        st.session_state["lab_draft_id"] = draft["draft_id"]
        st.session_state["lab_prompt"] = draft["prompt"]
    elif drafts:
        draft = drafts[0]
        st.session_state["lab_draft_id"] = draft["draft_id"]
        st.session_state["lab_prompt"] = draft["prompt"]
    st.session_state["lab_state_initialized"] = True


def _reset_lab_state(st: Any) -> None:
    st.session_state["lab_prompt"] = DEFAULT_PROMPT
    st.session_state["lab_draft_id"] = None
    st.session_state["lab_confirmation_id"] = None
    st.session_state["lab_job_id"] = None
    st.session_state["lab_state_initialized"] = True


def _render_draft(st: Any, store: Any, draft_record: dict[str, Any]) -> None:
    from .strategy_lab.prompt_engine import (
        confirm_prompt_candidate,
        review_prompt_draft,
    )

    draft = draft_record["draft"]
    label = "Needs review" if draft["status"] == "needs_review" else "Ready"
    color = "orange" if draft["status"] == "needs_review" else "green"
    st.badge(label, color=color)
    st.caption(f"Draft {draft_record['draft_id']} · engine {draft['engine_version']}")
    for warning in draft["warnings"]:
        st.caption(f":material/shield: {warning}")

    if draft["status"] == "needs_review":
        st.subheader("Review material assumptions")
        with st.form(f"lab_review_{draft_record['draft_id']}", border=True):
            accepted = []
            for question in draft["questions"]:
                st.markdown(f"**{question['question']}**")
                st.caption(question["risk"])
                st.json(question["proposed_values"], expanded=False)
                if st.checkbox(
                    "Approve these proposed values",
                    key=(
                        f"lab_accept_{draft_record['draft_id']}_"
                        f"{question['question_id']}"
                    ),
                ):
                    accepted.append(question["question_id"])
            submitted = st.form_submit_button(
                "Apply reviewed answers",
                icon=":material/fact_check:",
                type="primary",
            )
        if submitted:
            if not accepted:
                st.warning(
                    "Approve at least one question group before applying answers."
                )
            else:
                reviewed = review_prompt_draft(draft, accept_proposed=accepted)
                saved = store.save_draft(reviewed["prompt"], reviewed)
                st.session_state["lab_draft_id"] = saved["draft_id"]
                st.rerun()
        return

    st.subheader("Confirm the exact contract")
    if draft["contract_diff"]:
        st.dataframe(draft["contract_diff"], hide_index=True, width="stretch")
    else:
        st.caption("No values differ from the frozen reference contract.")
    st.code(draft["candidate_contract_sha256"], language=None)
    with st.expander("Reviewed contract", icon=":material/description:"):
        st.json(draft["candidate_contract"], expanded=False)
    with st.form(f"lab_confirm_{draft_record['draft_id']}", border=True):
        approved = st.checkbox(
            "I approve this exact checksum and understand this does not authorize live trading."
        )
        submitted = st.form_submit_button(
            "Confirm contract",
            icon=":material/verified:",
            type="primary",
        )
    if submitted:
        if not approved:
            st.warning("Check the approval box before confirming the contract.")
        else:
            confirmation = confirm_prompt_candidate(
                draft,
                approved_contract_sha256=draft["candidate_contract_sha256"],
            )
            record = store.save_confirmation(draft_record["draft_id"], confirmation)
            st.session_state["lab_confirmation_id"] = record["confirmation_id"]
            st.rerun()


def _launch_job(
    st: Any,
    store: Any,
    confirmation_id: str,
    *,
    retry: bool,
    retry_cancelled: bool = False,
) -> None:
    from .strategy_lab.ui_jobs import launch_strategy_lab_job

    status, created = store.prepare_job(
        confirmation_id,
        retry_failed=retry,
        retry_cancelled=retry_cancelled,
    )
    st.session_state["lab_job_id"] = status["job_id"]
    if created:
        try:
            launch_strategy_lab_job(REPO_ROOT, store.root, status["job_id"])
        except Exception as exc:
            store.fail_job(
                status["job_id"],
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise
    st.rerun()


def _render_completed_results(st: Any, store: Any, status: dict[str, Any]) -> None:
    from .strategy_lab.ui_model import build_results_view

    store.verify_completed_job(status["job_id"])
    job_directory = store.job_directory(status["job_id"])
    artifacts = job_directory / "artifacts"
    results = json.loads((artifacts / "results.json").read_text(encoding="utf-8"))
    view = build_results_view(results)
    conclusion = view["conclusion"]
    if conclusion["validation_conclusion"] == "negative":
        st.warning(conclusion["plain_language"], icon=":material/warning:")
    else:
        st.info(conclusion["plain_language"], icon=":material/info:")
    with st.container(horizontal=True):
        for card in view["cards"]:
            st.metric(
                card["label"],
                card["value"],
                help=card["help"],
                border=True,
            )
    if view["warnings"]:
        st.warning("\n\n".join(f"- {warning}" for warning in view["warnings"]))
    if st.toggle("Show all traceable metrics", key=f"lab_metrics_{status['job_id']}"):
        st.dataframe(view["metric_rows"], hide_index=True, width="stretch")
    with st.container(horizontal=True):
        for filename, label, mime in (
            ("results.json", "Results JSON", "application/json"),
            ("metrics.csv", "Metrics CSV", "text/csv"),
            ("report.html", "HTML report", "text/html"),
            ("export-manifest.json", "Manifest", "application/json"),
        ):
            st.download_button(
                label,
                data=(artifacts / filename).read_bytes(),
                file_name=filename,
                mime=mime,
                icon=":material/download:",
            )


def _render_job(st: Any, store: Any, job_id: str) -> str:
    from .strategy_lab.ui_jobs import StrategyLabUIJobError
    from .strategy_lab.ui_model import job_state_label

    status = store.read_job(job_id)
    label, color, icon = job_state_label(status["state"])
    st.badge(label, color=color, icon=f":material/{icon}:")
    st.caption(
        f"Job {job_id} · attempt {status['attempt']} · "
        f"updated {status['updated_at_utc']}"
    )
    if status["state"] == "queued":
        st.info("The external worker is queued. Streamlit remains responsive.")
    elif status["state"] == "running":
        st.info("The external worker is validating and reproducing the result bundle.")
    elif status["state"] == "cancellation_requested":
        st.warning(
            "Cancellation requested. The worker will stop at its next safe checkpoint."
        )
    elif status["state"] == "failed":
        st.error(
            f"{status.get('error_type', 'WorkerError')}: "
            f"{status.get('error_message', 'The worker failed safely.')}"
        )
        if st.button(
            "Retry failed job",
            icon=":material/refresh:",
            key=f"lab_retry_{job_id}",
        ):
            _launch_job(
                st,
                store,
                status["confirmation_id"],
                retry=True,
            )
    elif status["state"] == "cancelled":
        st.warning(status.get("cancel_reason", "The job was cancelled safely."))
        if st.button(
            "Retry cancelled job",
            icon=":material/refresh:",
            key=f"lab_retry_cancelled_{job_id}",
        ):
            _launch_job(
                st,
                store,
                status["confirmation_id"],
                retry=False,
                retry_cancelled=True,
            )
    else:
        try:
            _render_completed_results(st, store, status)
        except (OSError, ValueError, StrategyLabUIJobError) as exc:
            st.error(
                "Completed Strategy Lab artifacts failed their integrity checks. "
                "Results were not rendered."
            )
            st.caption(str(exc))
    if status["state"] in {"queued", "running"} and st.button(
        "Cancel job",
        icon=":material/cancel:",
        key=f"lab_cancel_{job_id}",
    ):
        store.request_cancel(job_id, reason="Cancelled from the Strategy Lab UI")
        st.rerun()
    return status["state"]


def render_strategy_lab() -> None:
    """Render the Lab without importing it on normal scanner pages."""

    import streamlit as st

    from .strategy_lab.prompt_engine import interpret_research_prompt
    from .strategy_lab.ui_jobs import StrategyLabUIJobError, StrategyLabUIStore

    store = StrategyLabUIStore(_store_root())
    try:
        _initialize_lab_state(st, store)
    except StrategyLabUIJobError as exc:
        st.title("Strategy Lab")
        st.error(
            "Saved Strategy Lab state failed its integrity checks. "
            "No research job was started."
        )
        st.caption(str(exc))
        return

    with st.container(horizontal=True, horizontal_alignment="distribute"):
        st.title("Strategy Lab")
        if st.button(
            "New question",
            icon=":material/add:",
            key="lab_new_question",
        ):
            _reset_lab_state(st)
            st.rerun()
    st.caption(
        "Offline research workflow · public historical data · no credentials · "
        "no live trading"
    )
    st.info(
        "The current UI can reproduce the frozen BTCUSDT EMA9 reference study. "
        "Revised contracts can be reviewed and confirmed, but are not executed until "
        "their full pipeline capability is approved.",
        icon=":material/science:",
    )

    with st.form("lab_prompt_form", border=True):
        st.text_area(
            "Research question",
            key="lab_prompt",
            height=120,
            help="Describe the market, timeframe, signal, period, and assumptions.",
        )
        interpreted = st.form_submit_button(
            "Interpret question",
            icon=":material/search_check:",
            type="primary",
        )
    if interpreted:
        result = interpret_research_prompt(st.session_state["lab_prompt"])
        if result["status"] == "rejected":
            st.error(
                f"Prompt rejected: {result['rejection_type']}. "
                "No contract or job was created."
            )
            if result.get("findings"):
                st.json(result["findings"], expanded=False)
        else:
            record = store.save_draft(result["prompt"], result)
            st.session_state["lab_draft_id"] = record["draft_id"]
            st.session_state["lab_confirmation_id"] = None
            st.session_state["lab_job_id"] = None
            st.rerun()

    draft_id = st.session_state.get("lab_draft_id")
    if draft_id:
        with st.container(border=True):
            _render_draft(st, store, store.load_draft(draft_id))

    confirmation_id = st.session_state.get("lab_confirmation_id")
    if confirmation_id:
        confirmation_record = store.load_confirmation(confirmation_id)
        confirmation = confirmation_record["confirmation"]
        with st.container(border=True):
            st.subheader("Confirmed contract")
            st.success(
                "The reviewed contract is persisted and recoverable after an app restart."
            )
            st.code(confirmation["confirmed_contract_sha256"], language=None)
            if confirmation["contract_diff"]:
                st.warning(
                    "This revision is confirmed but not runnable in Phase 8 because the "
                    "current validated artifacts belong to the exact reference contract."
                )
            elif st.session_state.get("lab_job_id") is None:
                if st.button(
                    "Submit reference job",
                    icon=":material/play_arrow:",
                    type="primary",
                    key=f"lab_submit_{confirmation_id}",
                ):
                    _launch_job(st, store, confirmation_id, retry=False)

    try:
        jobs = store.list_jobs()
    except StrategyLabUIJobError as exc:
        st.error(
            "Saved Strategy Lab job history failed its integrity checks. "
            "No research job was started."
        )
        st.caption(str(exc))
        return
    if jobs:
        job_ids = [job["job_id"] for job in jobs]
        current_job = st.session_state.get("lab_job_id")
        selected_index = job_ids.index(current_job) if current_job in job_ids else 0
        selected_job = st.selectbox(
            "Recovered job history",
            job_ids,
            index=selected_index,
            key="lab_job_history",
        )
        st.session_state["lab_job_id"] = selected_job
        current_status = store.read_job(selected_job)
        st.subheader("Job status and results")
        if current_status["state"] in {
            "queued",
            "running",
            "cancellation_requested",
        }:

            @st.fragment(run_every="2s")
            def live_job_status() -> None:
                state = _render_job(st, store, selected_job)
                if state in {"completed", "failed", "cancelled"}:
                    st.rerun()

            live_job_status()
        else:
            _render_job(st, store, selected_job)
