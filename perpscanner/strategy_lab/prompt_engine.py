"""Fail-closed natural-language review for Strategy Lab contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .event_study import ContractCapabilityError, validate_reference_contract


PROMPT_ENGINE_VERSION = "0.1.1"
REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "strategy_lab"
    / "examples"
    / "btcusdt_ema9_bounce.event-study.json"
)
CONTRACT_SCHEMA_PATH = (
    REPO_ROOT / "docs" / "strategy_lab" / "research_contract.schema.json"
)


class PromptEngineError(RuntimeError):
    """Base error for a prompt draft or confirmation gate."""


class PromptIntegrityError(PromptEngineError):
    """Raised when a draft or approval checksum was modified."""


class PromptCapabilityError(PromptEngineError):
    """Raised when a reviewed candidate is outside implemented capability."""


REVIEW_GROUPS = (
    {
        "group_id": "market_data",
        "question": (
            "Confirm symbol, 5-minute timeframe, UTC date range, and long direction?"
        ),
        "risk": "Changing market or period creates a different research sample.",
        "paths": (
            "/market/symbols",
            "/market/direction",
            "/data/timeframe",
            "/data/start",
            "/data/end",
        ),
    },
    {
        "group_id": "signal_definition",
        "question": (
            "Confirm the EMA9 bounce approach, probe, tolerances, and close confirmation?"
        ),
        "risk": "A different touch or bounce definition changes which events qualify.",
        "paths": (
            "/signal/indicator/kind",
            "/signal/indicator/length",
            "/signal/indicator/price_source",
            "/signal/event/approach",
            "/signal/event/precondition_bars",
            "/signal/event/probe_field",
            "/signal/event/touch_tolerance_bps",
            "/signal/event/max_penetration_bps",
            "/signal/event/confirmation",
            "/signal/event/min_rejection_bps",
        ),
    },
    {
        "group_id": "entry_independence",
        "question": "Confirm next-bar-open entry, cooldown, and overlap handling?",
        "risk": "Same-bar entry or overlapping events can introduce bias.",
        "paths": (
            "/entry/timing",
            "/entry/price",
            "/entry/cooldown_bars",
            "/entry/overlapping_positions",
        ),
    },
    {
        "group_id": "outcomes",
        "question": (
            "Confirm forward horizons, target, stop, holding period, and intrabar resolution?"
        ),
        "risk": "Outcome definitions materially change the measured result.",
        "paths": (
            "/outcome/mode",
            "/outcome/forward_horizons_bars",
            "/outcome/barrier/target_pct",
            "/outcome/barrier/stop_pct",
            "/outcome/barrier/max_holding_bars",
            "/outcome/barrier/same_bar_resolution",
            "/outcome/barrier/fallback_resolution",
            "/data/detail_timeframe",
        ),
    },
    {
        "group_id": "costs_and_sizing",
        "question": "Confirm fees, slippage, funding treatment, and fixed notional?",
        "risk": "Costs and sizing determine net rather than gross results.",
        "paths": (
            "/execution/fee_bps_per_side",
            "/execution/slippage_bps_per_side",
            "/execution/funding/include",
            "/execution/funding/source",
            "/execution/position_sizing/model",
            "/execution/position_sizing/value",
        ),
    },
    {
        "group_id": "validation",
        "question": (
            "Confirm chronological splits, locked holdout, uncertainty, correction, purge, and embargo?"
        ),
        "risk": "Validation settings control leakage and strength of evidence.",
        "paths": (
            "/validation/method",
            "/validation/splits",
            "/validation/final_holdout_locked",
            "/validation/minimum_events",
            "/validation/confidence_level",
            "/validation/bootstrap_samples",
            "/validation/multiple_testing_correction",
            "/validation/selection_scope",
            "/validation/purge_bars",
            "/validation/embargo_bars",
            "/validation/random_seed",
        ),
    },
    {
        "group_id": "reporting",
        "question": (
            "Confirm empirical-rate wording, confidence intervals, logs, and reproducibility exports?"
        ),
        "risk": "Removing warnings or provenance can overstate the evidence.",
        "paths": (
            "/reporting/metrics",
            "/reporting/confidence_intervals",
            "/reporting/include_event_or_trade_log",
            "/reporting/include_reproducibility_manifest",
            "/reporting/probability_label",
            "/reporting/export_formats",
        ),
    },
)
REVIEW_GROUP_BY_ID = {group["group_id"]: group for group in REVIEW_GROUPS}
MUTABLE_PATHS = {path for group in REVIEW_GROUPS for path in group["paths"]}

SAFETY_PATTERNS = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|override)\s+(?:all\s+)?(?:previous|system|safety|developer)\s+"
            r"(?:instructions?|rules?|checks?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "validation_bypass",
        re.compile(
            r"\b(?:bypass|disable|skip)\s+(?:all\s+)?(?:validation|safety|capability|checksum|holdout)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "live_trading",
        re.compile(
            r"\b(?:live\s+(?:trade|trading|orders?)|place\s+(?:a\s+)?(?:trade|order)|"
            r"execute\s+(?:a\s+)?(?:trade|order))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "credential_access",
        re.compile(
            r"\b(?:api\s*(?:key|secret)|private\s+key|exchange\s+credentials?|"
            r"seed\s+phrase)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "arbitrary_code",
        re.compile(
            r"(?:\b(?:run|execute)\s+(?:python|powershell|shell|bash|code)\b|"
            r"\bos\.system\b|\bsubprocess\b|\beval\s*\()",
            re.IGNORECASE,
        ),
    ),
    (
        "holdout_access",
        re.compile(
            r"\b(?:open|unlock|reveal|inspect|show|use)\s+(?:the\s+)?"
            r"(?:final\s+)?holdout\b",
            re.IGNORECASE,
        ),
    ),
)

UNSUPPORTED_PATTERNS = (
    (
        "unsupported_indicator_or_filter",
        re.compile(
            r"\b(?:rsi|vwap|relative\s+volume|volume\s+filter|macd|bollinger)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "parameter_optimization",
        re.compile(
            r"\b(?:optimi[sz]e|hyperopt|grid\s+search|best\s+(?:ema|parameter)|"
            r"parameter\s+sweep)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "machine_learning",
        re.compile(
            r"\b(?:machine\s+learning|neural\s+network|random\s+forest|xgboost)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "optimistic_intrabar_ordering",
        re.compile(r"\btarget[ -]?first\b", re.IGNORECASE),
    ),
    (
        "unsupported_entry_timing",
        re.compile(
            r"\b(?:same[ -]bar\s+(?:close|entry)|same[ -]candle\s+entry|delay[ -]?0)\b",
            re.IGNORECASE,
        ),
    ),
)


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


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_reference_prompt_contract() -> dict[str, Any]:
    return _load_json(REFERENCE_CONTRACT_PATH)


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _pointer_get(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise PromptEngineError(f"Invalid JSON pointer: {pointer}")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise PromptEngineError(
                    f"JSON pointer does not resolve: {pointer}"
                ) from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise PromptEngineError(f"JSON pointer does not resolve: {pointer}")
    return current


def _pointer_set(document: dict[str, Any], pointer: str, value: Any) -> None:
    if pointer not in MUTABLE_PATHS:
        raise PromptEngineError(f"Prompt review cannot modify path: {pointer}")
    tokens = [
        token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")
    ]
    current: Any = document
    for token in tokens[:-1]:
        if not isinstance(current, dict) or token not in current:
            raise PromptEngineError(f"JSON pointer does not resolve: {pointer}")
        current = current[token]
    if not isinstance(current, dict) or tokens[-1] not in current:
        raise PromptEngineError(f"JSON pointer does not resolve: {pointer}")
    current[tokens[-1]] = copy.deepcopy(value)


def contract_diff(before: Any, after: Any, pointer: str = "") -> list[dict[str, Any]]:
    """Return a stable RFC-6901-like list of changed leaf values."""

    if type(before) is not type(after):
        return [{"path": pointer, "before": before, "after": after}]
    if isinstance(before, dict):
        changes: list[dict[str, Any]] = []
        keys = sorted(set(before) | set(after))
        for key in keys:
            child = f"{pointer}/{_escape_pointer_token(str(key))}"
            if key not in before:
                changes.append({"path": child, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "before": before[key], "after": None})
            else:
                changes.extend(contract_diff(before[key], after[key], child))
        return changes
    if isinstance(before, list):
        if before == after:
            return []
        return [{"path": pointer, "before": before, "after": after}]
    if before != after:
        return [{"path": pointer, "before": before, "after": after}]
    return []


SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$ref",
        "additionalProperties",
        "allOf",
        "const",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "if",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "then",
        "type",
        "uniqueItems",
    }
)
SCHEMA_METADATA_KEYWORDS = frozenset(
    {"$defs", "$id", "$schema", "description", "title"}
)
DATE_TIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _schema_path(pointer: str, token: str | int) -> str:
    escaped = _escape_pointer_token(str(token))
    return f"{pointer}/{escaped}" if pointer else f"/{escaped}"


def _schema_error(pointer: str, message: str) -> dict[str, str]:
    return {"path": pointer or "/", "message": message}


def _json_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if left is None or right is None:
        return left is right
    return type(left) is type(right) and left == right


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _unsupported_schema_keywords(
    schema: dict[str, Any], pointer: str = ""
) -> list[str]:
    unsupported = []
    for keyword, value in schema.items():
        keyword_path = _schema_path(pointer, keyword)
        if keyword not in SUPPORTED_SCHEMA_KEYWORDS | SCHEMA_METADATA_KEYWORDS:
            unsupported.append(keyword_path)
            continue
        if keyword in {"properties", "$defs"} and isinstance(value, dict):
            for name, subschema in value.items():
                if isinstance(subschema, dict):
                    unsupported.extend(
                        _unsupported_schema_keywords(
                            subschema,
                            _schema_path(keyword_path, name),
                        )
                    )
        elif keyword in {"allOf", "oneOf"} and isinstance(value, list):
            for index, subschema in enumerate(value):
                if isinstance(subschema, dict):
                    unsupported.extend(
                        _unsupported_schema_keywords(
                            subschema,
                            _schema_path(keyword_path, index),
                        )
                    )
        elif keyword in {"if", "then", "items"} and isinstance(value, dict):
            unsupported.extend(_unsupported_schema_keywords(value, keyword_path))
    return unsupported


def _resolve_schema_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise PromptCapabilityError(
            f"Only internal contract schema references are supported: {reference}"
        )
    try:
        resolved = _pointer_get(root_schema, reference[1:])
    except PromptEngineError as exc:
        raise PromptCapabilityError(
            f"Contract schema reference does not resolve: {reference}"
        ) from exc
    if not isinstance(resolved, dict):
        raise PromptCapabilityError(
            f"Contract schema reference is not an object: {reference}"
        )
    return resolved


def _schema_errors(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    pointer: str = "",
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    if "$ref" in schema:
        errors.extend(
            _schema_errors(
                instance,
                _resolve_schema_ref(root_schema, schema["$ref"]),
                root_schema,
                pointer,
            )
        )

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_type(instance, expected_type):
        return errors + [
            _schema_error(pointer, f"expected JSON type {expected_type}")
        ]

    if "const" in schema and not _json_values_equal(instance, schema["const"]):
        errors.append(_schema_error(pointer, f"must equal {schema['const']!r}"))
    if "enum" in schema and not any(
        _json_values_equal(instance, option) for option in schema["enum"]
    ):
        errors.append(_schema_error(pointer, f"must be one of {schema['enum']!r}"))

    if "oneOf" in schema:
        matching_branches = sum(
            not _schema_errors(instance, branch, root_schema, pointer)
            for branch in schema["oneOf"]
        )
        if matching_branches != 1:
            errors.append(
                _schema_error(
                    pointer,
                    f"must match exactly one schema branch; matched {matching_branches}",
                )
            )

    if "if" in schema and "then" in schema:
        condition_matches = not _schema_errors(
            instance, schema["if"], root_schema, pointer
        )
        if condition_matches:
            errors.extend(
                _schema_errors(instance, schema["then"], root_schema, pointer)
            )

    for branch in schema.get("allOf", []):
        errors.extend(_schema_errors(instance, branch, root_schema, pointer))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                errors.append(
                    _schema_error(_schema_path(pointer, name), "required property is missing")
                )
        properties = schema.get("properties", {})
        for name, subschema in properties.items():
            if name in instance:
                errors.extend(
                    _schema_errors(
                        instance[name],
                        subschema,
                        root_schema,
                        _schema_path(pointer, name),
                    )
                )
        if schema.get("additionalProperties") is False:
            for name in sorted(set(instance) - set(properties)):
                errors.append(
                    _schema_error(
                        _schema_path(pointer, name),
                        "additional property is not allowed",
                    )
                )

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(
                _schema_error(pointer, f"must contain at least {schema['minItems']} items")
            )
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(
                _schema_error(pointer, f"must contain at most {schema['maxItems']} items")
            )
        if schema.get("uniqueItems"):
            unique_values = {
                _canonical_json_bytes(item) for item in instance
            }
            if len(unique_values) != len(instance):
                errors.append(_schema_error(pointer, "array items must be unique"))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(
                    _schema_errors(
                        item,
                        item_schema,
                        root_schema,
                        _schema_path(pointer, index),
                    )
                )

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(
                _schema_error(
                    pointer, f"must contain at least {schema['minLength']} characters"
                )
            )
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(
                _schema_error(
                    pointer, f"must contain at most {schema['maxLength']} characters"
                )
            )
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(
                _schema_error(pointer, f"must match pattern {schema['pattern']!r}")
            )
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(instance.replace("Z", "+00:00"))
                valid_datetime = (
                    DATE_TIME_PATTERN.fullmatch(instance) is not None
                    and parsed.tzinfo is not None
                )
            except ValueError:
                valid_datetime = False
            if not valid_datetime:
                errors.append(
                    _schema_error(pointer, "must be an RFC 3339 date-time with timezone")
                )

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if not math.isfinite(instance):
            return errors + [_schema_error(pointer, "must be a finite number")]
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(
                _schema_error(pointer, f"must be at least {schema['minimum']}")
            )
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(
                _schema_error(pointer, f"must be at most {schema['maximum']}")
            )
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(
                _schema_error(pointer, f"must be greater than {schema['exclusiveMinimum']}")
            )
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(
                _schema_error(pointer, f"must be less than {schema['exclusiveMaximum']}")
            )

    return errors


def validate_contract_schema(contract: dict[str, Any]) -> None:
    """Validate a contract using the frozen, offline schema subset used by the lab."""

    schema = _load_json(CONTRACT_SCHEMA_PATH)
    unsupported = _unsupported_schema_keywords(schema)
    if unsupported:
        raise PromptCapabilityError(
            f"Contract schema uses unsupported keywords: {unsupported}"
        )
    errors = _schema_errors(contract, schema, schema)
    if errors:
        errors.sort(key=lambda error: (error["path"], error["message"]))
        raise PromptCapabilityError(f"Contract schema validation failed: {errors}")


def _validate_schema_and_capability(contract: dict[str, Any]) -> None:
    validate_contract_schema(contract)
    validation = contract["validation"]
    execution = contract["execution"]
    outcome = contract["outcome"]
    policy_checks = (
        (
            validation["method"] == "anchored_walk_forward",
            "Prompt Engine requires anchored walk-forward validation",
        ),
        (
            validation["final_holdout_locked"] is True,
            "Final holdout must remain locked",
        ),
        (
            validation["multiple_testing_correction"] == "benjamini_hochberg",
            "Benjamini-Hochberg correction cannot be disabled",
        ),
        (
            execution["position_sizing"]["model"] == "fixed_notional",
            "Prompt Engine supports only fixed-notional sizing",
        ),
        (
            execution["funding"]["source"]
            == (
                "binance_historical_funding"
                if execution["funding"]["include"]
                else "not_applicable"
            ),
            "Funding source is inconsistent with funding inclusion",
        ),
    )
    for passed, message in policy_checks:
        if not passed:
            raise PromptCapabilityError(message)
    max_outcome_bars = max(
        max(int(value) for value in outcome["forward_horizons_bars"]),
        int(outcome["barrier"]["max_holding_bars"]),
    )
    if int(validation["purge_bars"]) < max_outcome_bars:
        raise PromptCapabilityError("Purge bars must cover the maximum outcome horizon")
    if int(validation["embargo_bars"]) < max_outcome_bars:
        raise PromptCapabilityError(
            "Embargo bars must cover the maximum outcome horizon"
        )
    try:
        validate_reference_contract(contract)
    except (ContractCapabilityError, KeyError, TypeError, ValueError) as exc:
        raise PromptCapabilityError(str(exc)) from exc


def _iso_start(value: str) -> str:
    return value if "T" in value else f"{value}T00:00:00Z"


def _normalise_prompt(prompt: str) -> str:
    return " ".join(prompt.strip().split())


def _scan_patterns(
    prompt: str,
    patterns: Iterable[tuple[str, re.Pattern[str]]],
) -> list[dict[str, str]]:
    findings = []
    for finding_id, pattern in patterns:
        match = pattern.search(prompt)
        if match:
            findings.append(
                {
                    "finding_id": finding_id,
                    "evidence": match.group(0),
                }
            )
    return findings


def _extract_prompt_updates(
    prompt: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    updates: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []

    def record(path: str, value: Any, matched: str) -> None:
        updates[path] = value
        evidence.append({"path": path, "value": value, "evidence": matched})

    symbols = list(dict.fromkeys(re.findall(r"\b[A-Z0-9]+USDT\b", prompt.upper())))
    if symbols:
        record("/market/symbols", symbols, ", ".join(symbols))

    timeframe_match = re.search(
        r"\b(?:on|at|timeframe(?:\s+is|\s*=|\s*:)?|[A-Z0-9]+USDT)\s+"
        r"(?:the\s+)?"
        r"(?:(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)|"
        r"(\d+)\s*[- ]?(minute|hour|day)s?)\b",
        prompt,
        re.IGNORECASE,
    )
    if timeframe_match:
        if timeframe_match.group(1):
            timeframe = timeframe_match.group(1).lower()
        else:
            suffix = {"minute": "m", "hour": "h", "day": "d"}[
                timeframe_match.group(3).lower()
            ]
            timeframe = f"{int(timeframe_match.group(2))}{suffix}"
        record(
            "/data/timeframe",
            timeframe,
            timeframe_match.group(0),
        )

    ma_match = re.search(
        r"\b(?:(ema|sma)\s*[- ]?(\d+)|"
        r"(\d+)(?:\s*[- ]?period)?\s*[- ]?(ema|sma)|"
        r"(\d+)(?:\s*[- ]?period)?\s+(exponential|simple)\s+moving\s+average|"
        r"(exponential|simple)\s+moving\s+average\s*[- ]?(\d+))\b",
        prompt,
        re.IGNORECASE,
    )
    if ma_match:
        raw_kind = ma_match.group(1) or ma_match.group(4)
        raw_kind = raw_kind or ma_match.group(6) or ma_match.group(7)
        kind = {
            "exponential": "ema",
            "simple": "sma",
        }.get(raw_kind.lower(), raw_kind.lower())
        length = int(
            ma_match.group(2)
            or ma_match.group(3)
            or ma_match.group(5)
            or ma_match.group(8)
        )
        record("/signal/indicator/kind", kind, ma_match.group(0))
        record("/signal/indicator/length", length, ma_match.group(0))

    date_match = re.search(
        r"\bfrom\s+(\d{4}-\d{2}-\d{2}(?:T[^\s]+)?)\s+"
        r"(?:through|to|until)\s+(\d{4}-\d{2}-\d{2}(?:T[^\s]+)?)",
        prompt,
        re.IGNORECASE,
    )
    if date_match:
        record("/data/start", _iso_start(date_match.group(1)), date_match.group(0))
        record("/data/end", _iso_start(date_match.group(2)), date_match.group(0))

    direction_patterns = (
        ("long", r"\blong(?:-bounce|\s+bounce|\s+direction)?\b"),
        ("short", r"\bshort(?:-bounce|\s+bounce|\s+direction)?\b"),
        ("both", r"\b(?:both\s+directions|long\s+and\s+short)\b"),
    )
    for direction, pattern in direction_patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            record("/market/direction", direction, match.group(0))
            break

    approach = re.search(
        r"\bfrom\s+(above|below)\b|\beither\s+side\b", prompt, re.IGNORECASE
    )
    if approach:
        value = (
            "either"
            if "either" in approach.group(0).lower()
            else f"from_{approach.group(1).lower()}"
        )
        record("/signal/event/approach", value, approach.group(0))

    integer_patterns = (
        ("/signal/event/precondition_bars", r"\bprecondition\s+(\d+)\s+bars?\b"),
        ("/entry/cooldown_bars", r"\bcooldown\s+(\d+)\s+bars?\b"),
        (
            "/outcome/barrier/max_holding_bars",
            r"\b(?:max(?:imum)?\s+)?hold(?:ing)?\s+(\d+)\s+bars?\b",
        ),
        ("/validation/minimum_events", r"\bminimum\s+(\d+)\s+events?\b"),
        ("/validation/bootstrap_samples", r"\bbootstrap\s+(\d+)\s+samples?\b"),
        ("/validation/purge_bars", r"\bpurge\s+(\d+)\s+bars?\b"),
        ("/validation/embargo_bars", r"\bembargo\s+(\d+)\s+bars?\b"),
        ("/validation/random_seed", r"\brandom\s+seed\s+(\d+)\b"),
    )
    for path, pattern in integer_patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            record(path, int(match.group(1)), match.group(0))

    decimal_patterns = (
        (
            "/signal/event/touch_tolerance_bps",
            r"\btouch\s+tolerance\s+(\d+(?:\.\d+)?)\s+bps\b",
        ),
        (
            "/signal/event/max_penetration_bps",
            r"\bmax(?:imum)?\s+penetration\s+(\d+(?:\.\d+)?)\s+bps\b",
        ),
        (
            "/signal/event/min_rejection_bps",
            r"\bmin(?:imum)?\s+rejection\s+(\d+(?:\.\d+)?)\s+bps\b",
        ),
        ("/outcome/barrier/target_pct", r"\btarget\s+(\d+(?:\.\d+)?)\s*%"),
        ("/outcome/barrier/stop_pct", r"\bstop\s+(\d+(?:\.\d+)?)\s*%"),
        (
            "/execution/fee_bps_per_side",
            r"\bfees?\s+(\d+(?:\.\d+)?)\s+bps(?:\s+per\s+side)?\b",
        ),
        (
            "/execution/slippage_bps_per_side",
            r"\bslippage\s+(\d+(?:\.\d+)?)\s+bps(?:\s+per\s+side)?\b",
        ),
        (
            "/execution/position_sizing/value",
            r"\b(?:notional|position)\s+(\d+(?:\.\d+)?)\s+USDT\b",
        ),
    )
    for path, pattern in decimal_patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            record(path, float(match.group(1)), match.group(0))

    confidence_match = re.search(
        r"\bconfidence\s+(\d+(?:\.\d+)?)\s*%",
        prompt,
        re.IGNORECASE,
    )
    if confidence_match:
        record(
            "/validation/confidence_level",
            float(confidence_match.group(1)) / 100.0,
            confidence_match.group(0),
        )

    horizon_match = re.search(
        r"\bforward\s+horizons?\s+([0-9, /and]+?)\s+bars?\b",
        prompt,
        re.IGNORECASE,
    )
    if horizon_match:
        horizons = [int(value) for value in re.findall(r"\d+", horizon_match.group(1))]
        record("/outcome/forward_horizons_bars", horizons, horizon_match.group(0))

    split_match = re.search(
        r"\bsplits?\s+(\d+(?:\.\d+)?)\s*[/,-]\s*(\d+(?:\.\d+)?)\s*"
        r"[/,-]\s*(\d+(?:\.\d+)?)\b",
        prompt,
        re.IGNORECASE,
    )
    if split_match:
        raw = [float(split_match.group(index)) for index in (1, 2, 3)]
        denominator = 100.0 if sum(raw) > 1.0 else 1.0
        record(
            "/validation/splits",
            {
                "training": raw[0] / denominator,
                "validation": raw[1] / denominator,
                "final_holdout": raw[2] / denominator,
            },
            split_match.group(0),
        )

    probe_match = re.search(
        r"\bprobe\s+(?:the\s+)?(low|high|open|close)\b", prompt, re.IGNORECASE
    )
    if probe_match:
        record(
            "/signal/event/probe_field",
            probe_match.group(1).lower(),
            probe_match.group(0),
        )

    price_match = re.search(
        r"\bindicator\s+price\s+(open|high|low|close|hl2|hlc3|ohlc4)\b",
        prompt,
        re.IGNORECASE,
    )
    if price_match:
        record(
            "/signal/indicator/price_source",
            price_match.group(1).lower(),
            price_match.group(0),
        )

    if re.search(
        r"\bclose\s+above(?:\s+the)?\s+(?:average|ema|sma)\b", prompt, re.IGNORECASE
    ):
        record(
            "/signal/event/confirmation", "close_above_average", "close above average"
        )
    elif re.search(
        r"\bclose\s+below(?:\s+the)?\s+(?:average|ema|sma)\b", prompt, re.IGNORECASE
    ):
        record(
            "/signal/event/confirmation", "close_below_average", "close below average"
        )

    if re.search(r"\bnext[- ]bar[- ]open\b", prompt, re.IGNORECASE):
        record("/entry/timing", "next_bar_open", "next-bar-open")
        record("/entry/price", "open", "next-bar-open")

    overlap = re.search(
        r"\b(skip|allow)\s+(?:event\s+)?overlap\b", prompt, re.IGNORECASE
    )
    if overlap:
        record(
            "/entry/overlapping_positions", overlap.group(1).lower(), overlap.group(0)
        )

    if re.search(
        r"\bforward\s+returns?\s+and\s+barrier\b|\bboth\s+outcomes?\b",
        prompt,
        re.IGNORECASE,
    ):
        record("/outcome/mode", "both", "both outcomes")

    if re.search(
        r"\b1m\s+(?:detail\s+)?replay\b|\blower[- ]timeframe\s+replay\b",
        prompt,
        re.IGNORECASE,
    ):
        record(
            "/outcome/barrier/same_bar_resolution",
            "lower_timeframe_replay",
            "lower-timeframe replay",
        )
        record(
            "/outcome/barrier/fallback_resolution",
            "unresolved",
            "lower-timeframe replay",
        )
        record("/data/detail_timeframe", "1m", "1m replay")

    funding_off = re.search(
        r"\b(?:exclude|disable|without|no)\s+(?:historical\s+)?funding\b",
        prompt,
        re.IGNORECASE,
    )
    funding_on = re.search(
        r"\binclude\s+(?:historical\s+)?funding\b", prompt, re.IGNORECASE
    )
    if funding_off:
        record("/execution/funding/include", False, funding_off.group(0))
        record("/execution/funding/source", "not_applicable", funding_off.group(0))
    elif funding_on:
        record("/execution/funding/include", True, funding_on.group(0))
        record(
            "/execution/funding/source",
            "binance_historical_funding",
            funding_on.group(0),
        )

    if re.search(r"\banchored\s+walk[- ]forward\b", prompt, re.IGNORECASE):
        record("/validation/method", "anchored_walk_forward", "anchored walk-forward")
    if re.search(r"\bbenjamini[- ]hochberg\b", prompt, re.IGNORECASE):
        record(
            "/validation/multiple_testing_correction",
            "benjamini_hochberg",
            "Benjamini-Hochberg",
        )

    accept_all = bool(
        re.search(
            r"\b(?:use|using|accept|accepting)(?:\s+all)?(?:\s+remaining)?"
            r"\s+(?:the\s+)?(?:frozen\s+)?reference\s+(?:settings|defaults)\b",
            prompt,
            re.IGNORECASE,
        )
    )
    return updates, evidence, accept_all


def _questions(
    contract: dict[str, Any],
    explicit_paths: set[str],
    accepted_groups: set[str],
) -> list[dict[str, Any]]:
    questions = []
    for group in REVIEW_GROUPS:
        paths = set(group["paths"])
        if group["group_id"] in accepted_groups or paths <= explicit_paths:
            continue
        questions.append(
            {
                "question_id": group["group_id"],
                "question": group["question"],
                "risk": group["risk"],
                "paths": list(group["paths"]),
                "proposed_values": {
                    path: _pointer_get(contract, path) for path in group["paths"]
                },
            }
        )
    return questions


def _apply_revision(base: dict[str, Any], candidate: dict[str, Any]) -> None:
    candidate["experiment"]["revision"] = base["experiment"]["revision"]
    if candidate != base:
        candidate["experiment"]["revision"] = base["experiment"]["revision"] + 1


def _draft_integrity_payload(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        key: draft[key]
        for key in (
            "engine_version",
            "status",
            "prompt",
            "prompt_sha256",
            "base_contract",
            "base_contract_sha256",
            "candidate_contract",
            "candidate_contract_sha256",
            "contract_diff",
            "explicit_paths",
            "accepted_review_groups",
            "questions",
            "extractions",
            "warnings",
        )
    }


def _seal_draft(draft: dict[str, Any]) -> dict[str, Any]:
    draft["draft_sha256"] = _sha256_payload(_draft_integrity_payload(draft))
    return draft


def _verify_draft(draft: dict[str, Any]) -> None:
    expected = draft.get("draft_sha256")
    actual = _sha256_payload(_draft_integrity_payload(draft))
    if expected != actual:
        raise PromptIntegrityError("Prompt draft checksum mismatch")


def _build_draft(
    *,
    prompt: str,
    base: dict[str, Any],
    candidate: dict[str, Any],
    explicit_paths: set[str],
    accepted_groups: set[str],
    extractions: list[dict[str, Any]],
) -> dict[str, Any]:
    _apply_revision(base, candidate)
    _validate_schema_and_capability(candidate)
    questions = _questions(candidate, explicit_paths, accepted_groups)
    status = "needs_review" if questions else "ready_for_confirmation"
    draft = {
        "engine_version": PROMPT_ENGINE_VERSION,
        "status": status,
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "base_contract": copy.deepcopy(base),
        "base_contract_sha256": _sha256_payload(base),
        "candidate_contract": copy.deepcopy(candidate),
        "candidate_contract_sha256": _sha256_payload(candidate),
        "contract_diff": contract_diff(base, candidate),
        "explicit_paths": sorted(explicit_paths),
        "accepted_review_groups": sorted(accepted_groups),
        "questions": questions,
        "extractions": extractions,
        "warnings": [
            "This draft cannot execute research or trading.",
            "The final holdout remains locked and unopened.",
            "Confirmation is bound to the exact candidate contract checksum.",
        ],
    }
    return _seal_draft(draft)


def interpret_research_prompt(
    prompt: str,
    *,
    base_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Interpret supported language into a reviewable, non-executable draft."""

    normalized = _normalise_prompt(prompt)
    prompt_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if len(normalized) < 3 or len(normalized) > 4000:
        return {
            "engine_version": PROMPT_ENGINE_VERSION,
            "status": "rejected",
            "rejection_type": "invalid_prompt_length",
            "prompt_sha256": prompt_sha256,
            "findings": [],
            "candidate_contract": None,
        }
    safety = _scan_patterns(normalized, SAFETY_PATTERNS)
    if safety:
        return {
            "engine_version": PROMPT_ENGINE_VERSION,
            "status": "rejected",
            "rejection_type": "safety",
            "prompt_sha256": prompt_sha256,
            "findings": safety,
            "candidate_contract": None,
        }
    unsupported = _scan_patterns(normalized, UNSUPPORTED_PATTERNS)
    if unsupported:
        return {
            "engine_version": PROMPT_ENGINE_VERSION,
            "status": "rejected",
            "rejection_type": "unsupported_capability",
            "prompt_sha256": prompt_sha256,
            "findings": unsupported,
            "candidate_contract": None,
        }

    base = copy.deepcopy(base_contract or load_reference_prompt_contract())
    try:
        _validate_schema_and_capability(base)
    except PromptCapabilityError as exc:
        raise PromptEngineError("Base contract is not trusted") from exc
    candidate = copy.deepcopy(base)
    updates, extractions, accept_all = _extract_prompt_updates(normalized)
    for path, value in updates.items():
        _pointer_set(candidate, path, value)
    accepted_groups = set(REVIEW_GROUP_BY_ID) if accept_all else set()
    try:
        return _build_draft(
            prompt=normalized,
            base=base,
            candidate=candidate,
            explicit_paths=set(updates),
            accepted_groups=accepted_groups,
            extractions=extractions,
        )
    except PromptCapabilityError as exc:
        return {
            "engine_version": PROMPT_ENGINE_VERSION,
            "status": "rejected",
            "rejection_type": "unsupported_capability",
            "prompt_sha256": prompt_sha256,
            "findings": [{"finding_id": "contract_capability", "evidence": str(exc)}],
            "candidate_contract": None,
        }


def review_prompt_draft(
    draft: dict[str, Any],
    *,
    accept_proposed: Iterable[str] = (),
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply explicit structured answers without trusting free-form code."""

    _verify_draft(draft)
    if draft["status"] not in {"needs_review", "ready_for_confirmation"}:
        raise PromptEngineError(f"Draft cannot be reviewed from {draft['status']}")
    accepted = set(draft["accepted_review_groups"])
    requested_groups = set(accept_proposed)
    unknown_groups = requested_groups - set(REVIEW_GROUP_BY_ID)
    if unknown_groups:
        raise PromptEngineError(
            f"Unknown review question identifiers: {sorted(unknown_groups)}"
        )
    accepted.update(requested_groups)
    candidate = copy.deepcopy(draft["candidate_contract"])
    explicit_paths = set(draft["explicit_paths"])
    review_extractions: list[dict[str, Any]] = []
    for path, value in (updates or {}).items():
        _pointer_set(candidate, path, value)
        explicit_paths.add(path)
        review_extractions.append(
            {"path": path, "value": value, "evidence": "structured_review_answer"}
        )
    try:
        return _build_draft(
            prompt=draft["prompt"],
            base=copy.deepcopy(draft["base_contract"]),
            candidate=candidate,
            explicit_paths=explicit_paths,
            accepted_groups=accepted,
            extractions=[*draft["extractions"], *review_extractions],
        )
    except PromptCapabilityError as exc:
        raise PromptCapabilityError(
            f"Reviewed answers exceed implemented capability: {exc}"
        ) from exc


def confirm_prompt_candidate(
    draft: dict[str, Any],
    *,
    approved_contract_sha256: str,
) -> dict[str, Any]:
    """Confirm only the exact, fully reviewed candidate contract."""

    _verify_draft(draft)
    if draft["status"] != "ready_for_confirmation":
        raise PromptEngineError("All ambiguity questions must be resolved first")
    actual_sha256 = _sha256_payload(draft["candidate_contract"])
    if draft["candidate_contract_sha256"] != actual_sha256:
        raise PromptIntegrityError("Candidate contract checksum mismatch")
    if approved_contract_sha256 != actual_sha256:
        raise PromptIntegrityError(
            "Approval checksum does not match the reviewed candidate contract"
        )
    _validate_schema_and_capability(draft["candidate_contract"])
    confirmation_payload = {
        "prompt_sha256": draft["prompt_sha256"],
        "base_contract_sha256": draft["base_contract_sha256"],
        "confirmed_contract_sha256": actual_sha256,
        "contract_diff": draft["contract_diff"],
    }
    return {
        "engine_version": PROMPT_ENGINE_VERSION,
        "status": "confirmed",
        "confirmation_id": _sha256_payload(confirmation_payload),
        "prompt_sha256": draft["prompt_sha256"],
        "base_contract_sha256": draft["base_contract_sha256"],
        "confirmed_contract_sha256": actual_sha256,
        "contract_diff": copy.deepcopy(draft["contract_diff"]),
        "confirmed_contract": copy.deepcopy(draft["candidate_contract"]),
        "execution_authorized": False,
        "holdout_open_authorized": False,
    }
