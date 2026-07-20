"""Deterministic Strategy Lab services, isolated from scanner runtime code."""

from .binance_data import (
    BinanceKlineClient,
    DataIntegrityError,
    DownloadSpec,
    TrustedKlineStore,
)
from .funding_data import BinanceFundingClient, TrustedFundingStore
from .freqtrade_adapter import (
    AdapterCapabilityError,
    WorkerStateStore,
    compile_strategy_contract,
    reconcile_trade_costs,
)
from .validation import (
    ValidationGateError,
    benjamini_hochberg,
    run_validation_pipeline,
    validate_holdout_lock,
)
from .reporting import (
    ExportGateError,
    build_export_bundle,
    validate_metric_traceability,
)
from .prompt_engine import (
    PromptCapabilityError,
    PromptEngineError,
    PromptIntegrityError,
    confirm_prompt_candidate,
    contract_diff,
    interpret_research_prompt,
    review_prompt_draft,
    validate_contract_schema,
)
from .ui_jobs import (
    StrategyLabUIJobError,
    StrategyLabUIStore,
    launch_strategy_lab_job,
)

__all__ = [
    "BinanceKlineClient",
    "DataIntegrityError",
    "DownloadSpec",
    "TrustedKlineStore",
    "BinanceFundingClient",
    "TrustedFundingStore",
    "AdapterCapabilityError",
    "WorkerStateStore",
    "compile_strategy_contract",
    "reconcile_trade_costs",
    "ValidationGateError",
    "benjamini_hochberg",
    "run_validation_pipeline",
    "validate_holdout_lock",
    "ExportGateError",
    "build_export_bundle",
    "validate_metric_traceability",
    "PromptCapabilityError",
    "PromptEngineError",
    "PromptIntegrityError",
    "confirm_prompt_candidate",
    "contract_diff",
    "interpret_research_prompt",
    "review_prompt_draft",
    "validate_contract_schema",
    "StrategyLabUIJobError",
    "StrategyLabUIStore",
    "launch_strategy_lab_job",
]
