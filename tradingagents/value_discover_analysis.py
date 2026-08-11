"""Explicit analysis backends for the Value Discover research pipeline."""

from __future__ import annotations

import os
import json
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tradingagents.value_discover import ValueCandidate


ANALYSIS_MODES = ("disabled", "embedded", "codex")
INPUT_CONTRACT_VERSION = "value_discover.candidate_analysis_input.v1"
OUTPUT_CONTRACT_VERSION = "value_discover.candidate_analysis_output.v1"
OUTPUT_STATUSES = ("ok", "error", "timeout", "invalid_output")
FINANCIAL_FIELDS = (
    "price",
    "market_cap",
    "trailing_pe",
    "forward_pe",
    "price_to_book",
    "ev_to_ebitda",
    "profit_margin",
    "return_on_equity",
    "debt_to_equity",
    "target_upside_pct",
    "rsi_14",
    "avg_volume_20d",
    "score",
)
EXTERNAL_MODEL_CREDENTIAL_ENV_VARS = (
    "ALPHA_VANTAGE_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "CODEX_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "NVIDIA_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
)
DEFAULT_RESEARCH_REUSE_DAYS = 7

_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Value Discover candidate analysis output",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "contract_version": {"type": "string", "const": OUTPUT_CONTRACT_VERSION},
        "analysis_id": {"type": "string"},
        "symbol": {"type": "string"},
        "mode": {"type": "string", "enum": ["embedded", "codex"]},
        "status": {"type": "string", "enum": list(OUTPUT_STATUSES)},
        "classification": {
            "type": "string",
            "enum": [
                "research_candidate",
                "watchlist",
                "deprioritized",
                "unknown",
            ],
        },
        "summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_type": {
                        "type": "string",
                        "enum": ["Fact", "Estimate", "Inference", "PM judgment"],
                    },
                    "text": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim_type", "text", "source_ids"],
            },
        },
        "data_gaps": {"type": "array", "items": {"type": "string"}},
        "provenance": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "executor": {"type": "string"},
                "runtime": {"type": "string"},
                "source_ids": {"type": "array", "items": {"type": "string"}},
                "generated_at": {"type": "string"},
            },
            "required": ["executor", "runtime", "source_ids", "generated_at"],
        },
        "error": {"type": ["string", "null"]},
    },
    "required": [
        "contract_version",
        "analysis_id",
        "symbol",
        "mode",
        "status",
        "classification",
        "summary",
        "claims",
        "data_gaps",
        "provenance",
        "error",
    ],
}


def resolve_analysis_mode(environ: Mapping[str, str] | None = None) -> str:
    """Resolve one observable backend without ever selecting a silent fallback."""
    values = os.environ if environ is None else environ
    explicit = values.get("TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE", "").strip().lower()
    if explicit:
        if explicit not in ANALYSIS_MODES:
            raise ValueError(
                "Value Discover analysis mode must be disabled, embedded, or codex"
            )
        return explicit
    if "TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED" in values:
        legacy_enabled = values["TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED"].strip().lower()
        return (
            "embedded"
            if legacy_enabled in {"1", "true", "yes", "on"}
            else "disabled"
        )
    return "codex"


def resolve_analysis_timeout(environ: Mapping[str, str] | None = None) -> int:
    """Resolve the per-candidate hard timeout for either active backend."""
    values = os.environ if environ is None else environ
    raw = values.get(
        "TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_TIMEOUT_SECONDS", "180"
    ).strip()
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError("Value Discover analysis timeout must be a positive integer") from exc
    if timeout <= 0:
        raise ValueError("Value Discover analysis timeout must be a positive integer")
    return timeout


def resolve_research_reuse_days(environ: Mapping[str, str] | None = None) -> int:
    """Resolve the age limit for reusing a successful prior candidate analysis."""
    values = os.environ if environ is None else environ
    raw = values.get(
        "TRADINGAGENTS_VALUE_DISCOVER_RESEARCH_REUSE_DAYS",
        str(DEFAULT_RESEARCH_REUSE_DAYS),
    ).strip()
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError(
            "Value Discover research reuse window must be a non-negative integer"
        ) from exc
    if days < 0:
        raise ValueError(
            "Value Discover research reuse window must be a non-negative integer"
        )
    return days


def load_recent_analysis_reuse(
    output_dir: Path | str,
    *,
    analysis_date: str,
    mode: str,
    max_age_days: int,
) -> dict[str, dict[str, Any]]:
    """Find the newest reusable successful result for each symbol.

    Reuse is deliberately conservative: only older daily ledgers, the same
    explicit backend mode, a successful result, and the configured age window
    qualify. Invalid or incomplete historical ledgers are ignored.
    """
    if mode == "disabled" or max_age_days <= 0:
        return {}
    current_day = datetime.strptime(analysis_date, "%Y-%m-%d").date()
    root = Path(output_dir)
    dated_dirs: list[tuple[Any, Path]] = []
    if not root.exists():
        return {}
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            day = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (current_day - day).days
        if 1 <= age_days <= max_age_days:
            dated_dirs.append((day, child))

    reusable: dict[str, dict[str, Any]] = {}
    for day, run_dir in sorted(dated_dirs, reverse=True):
        ledger_path = run_dir / "candidate_analysis" / "analysis_results.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(ledger, Mapping) or ledger.get("mode") != mode:
            continue
        results = ledger.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, Mapping):
                continue
            symbol = str(result.get("symbol", "")).strip().upper()
            if not symbol or symbol in reusable:
                continue
            try:
                _validate_reusable_output(result, symbol=symbol, expected_mode=mode)
            except (ValueError, TypeError):
                continue
            reusable[symbol] = {
                "result": deepcopy(dict(result)),
                "analysis_date": day.isoformat(),
                "ledger_path": str(ledger_path),
            }
    return reusable


def analysis_batch_status(mode: str, outputs: Sequence[Mapping[str, Any]]) -> str:
    """Reduce per-candidate statuses without masking skipped or degraded work."""
    if mode == "disabled":
        return "skipped"
    statuses = [str(output.get("status")) for output in outputs]
    if not statuses:
        return "error"
    if all(status == "ok" for status in statuses):
        return "ok"
    if "ok" in statuses:
        return "partial"
    if len(set(statuses)) == 1:
        return statuses[0]
    return "error"


def build_candidate_analysis_input(
    candidate: ValueCandidate,
    *,
    rank: int,
    analysis_date: str,
    sources: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the immutable envelope consumed by every analysis backend."""
    candidate_payload = asdict(candidate)
    source_payload = [dict(source) for source in sources]
    source_ids = [str(source["source_id"]) for source in source_payload]
    primary_source_ids = source_ids[:1]
    source_as_of = next(
        (
            str(source["as_of"])
            for source in source_payload
            if source.get("as_of") not in (None, "")
        ),
        analysis_date,
    )
    return {
        "contract_version": INPUT_CONTRACT_VERSION,
        "analysis_id": f"{analysis_date}:{rank:02d}:{candidate.symbol}",
        "analysis_date": analysis_date,
        "rank": rank,
        "candidate": candidate_payload,
        "financial_facts": {
            field: {
                "value": candidate_payload[field],
                "source_ids": primary_source_ids if candidate_payload[field] is not None else [],
                "as_of": source_as_of if candidate_payload[field] is not None else None,
                "unknown_reason": (
                    None
                    if candidate_payload[field] is not None
                    else "Value unavailable in the supplied candidate sources."
                ),
            }
            for field in FINANCIAL_FIELDS
        },
        "sources": source_payload,
        "constraints": {
            "research_only": True,
            "recommendation_labels": [
                "research_candidate",
                "watchlist",
                "deprioritized",
            ],
            "no_orders_or_transactions": True,
        },
    }


def analysis_output_schema() -> dict[str, Any]:
    """Return a copy of the stable output contract used by both adapters."""
    return deepcopy(_OUTPUT_SCHEMA)


def analysis_provider_provenance(
    mode: str,
    *,
    embedded_config: Mapping[str, Any] | None = None,
    codex_runtime: str = "unavailable",
) -> dict[str, Any]:
    """Describe the selected execution provider without reading credential values."""
    if mode not in ANALYSIS_MODES:
        raise ValueError("Value Discover analysis mode must be disabled, embedded, or codex")
    config = embedded_config or {}
    if mode == "codex":
        return {
            "execution_provider": "codex_cli",
            "runtime": codex_runtime,
            "external_model_api_invoked": False,
            "embedded_provider": None,
            "embedded_models": [],
        }
    if mode == "embedded":
        models = [
            str(config.get("quick_think_llm") or ""),
            str(config.get("deep_think_llm") or ""),
        ]
        return {
            "execution_provider": "embedded_model",
            "runtime": "TradingAgentsGraph",
            "external_model_api_invoked": True,
            "embedded_provider": config.get("llm_provider"),
            "embedded_models": list(dict.fromkeys(model for model in models if model)),
        }
    return {
        "execution_provider": "none",
        "runtime": "disabled",
        "external_model_api_invoked": False,
        "embedded_provider": None,
        "embedded_models": [],
    }


def normalize_embedded_results(
    inputs: Sequence[Mapping[str, Any]],
    legacy_results: Sequence[Any],
    *,
    runtime: str,
) -> list[dict[str, Any]]:
    """Adapt the existing TradingAgentsGraph result without replacing its path."""
    if len(inputs) != len(legacy_results):
        raise ValueError("embedded result count must match candidate input count")
    outputs: list[dict[str, Any]] = []
    for payload, result in zip(inputs, legacy_results, strict=True):
        status = "ok" if result.status == "ok" else "error"
        source_ids = [source["source_id"] for source in payload.get("sources", [])]
        decision = str(result.decision or "")
        output = {
            "contract_version": OUTPUT_CONTRACT_VERSION,
            "analysis_id": payload["analysis_id"],
            "symbol": payload["candidate"]["symbol"],
            "mode": "embedded",
            "status": status,
            "classification": "unknown",
            "summary": decision,
            "claims": (
                [
                    {
                        "claim_type": "Inference",
                        "text": decision,
                        "source_ids": source_ids,
                    }
                ]
                if decision
                else []
            ),
            "data_gaps": [
                "Legacy TradingAgentsGraph output does not emit the new research-priority classification."
            ],
            "provenance": {
                "executor": "TradingAgentsGraph",
                "runtime": runtime,
                "source_ids": source_ids,
                "generated_at": _now_iso(),
            },
            "error": result.error,
        }
        _validate_output(output, payload, expected_mode="embedded")
        outputs.append(output)
    return outputs


def run_analysis_batch(
    inputs: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    output_dir: Path | str,
    timeout_seconds: int,
    project_dir: Path | str,
    command_runner: Any = subprocess.run,
    embedded_runner: Any = None,
    embedded_config: Mapping[str, Any] | None = None,
    codex_runtime: str = "unavailable",
    reuse_by_symbol: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], Path, Path]:
    """Run exactly one selected backend and materialize normalized artifacts.

    The function deliberately has no fallback chain. A failed Codex invocation
    remains a Codex failure; it never spends direct-model tokens implicitly.
    """
    if mode not in ANALYSIS_MODES:
        raise ValueError("Value Discover analysis mode must be disabled, embedded, or codex")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    schema_path = root / "analysis_output.schema.json"
    schema_path.write_text(json.dumps(analysis_output_schema(), indent=2), encoding="utf-8")

    reuse_records = reuse_by_symbol or {}
    candidate_actions: list[dict[str, Any]] = []
    attempted_count = 0
    reused_count = 0

    if mode == "disabled":
        outputs: list[dict[str, Any]] = []
    elif mode == "embedded":
        if embedded_runner is None:
            raise ValueError("embedded mode requires an explicit embedded_runner")
        fresh_inputs = [
            payload
            for payload in inputs
            if str(payload["candidate"]["symbol"]).upper() not in reuse_records
        ]
        fresh_outputs = list(embedded_runner(fresh_inputs)) if fresh_inputs else []
        for item, payload in zip(fresh_inputs, fresh_outputs, strict=True):
            _validate_output(payload, item, expected_mode="embedded")
        fresh_iter = iter(fresh_outputs)
        outputs = []
        for payload in inputs:
            symbol = str(payload["candidate"]["symbol"]).upper()
            reuse = reuse_records.get(symbol)
            if reuse is not None:
                output = _validated_reused_result(reuse, symbol=symbol, mode=mode)
                reused_count += 1
                action = _reuse_action(symbol, output, reuse)
            else:
                output = next(fresh_iter)
                attempted_count += 1
                action = _researched_action(payload)
            outputs.append(output)
            candidate_actions.append(action)
    else:
        outputs = []
        for payload in inputs:
            symbol = str(payload["candidate"]["symbol"]).upper()
            reuse = reuse_records.get(symbol)
            if reuse is not None:
                output = _validated_reused_result(reuse, symbol=symbol, mode=mode)
                reused_count += 1
                action = _reuse_action(symbol, output, reuse)
            else:
                output = _run_codex_candidate(
                    payload,
                    root=root,
                    schema_path=schema_path,
                    timeout_seconds=timeout_seconds,
                    project_dir=Path(project_dir),
                    command_runner=command_runner,
                    codex_runtime=codex_runtime,
                )
                attempted_count += 1
                action = _researched_action(payload)
            outputs.append(output)
            candidate_actions.append(action)
            if output["status"] != "ok":
                break

    generated_at = _now_iso()
    batch_status = analysis_batch_status(mode, outputs)
    provider_provenance = analysis_provider_provenance(
        mode,
        embedded_config=embedded_config,
        codex_runtime=codex_runtime,
    )
    source_registry: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for candidate_input in inputs:
        for source in candidate_input.get("sources", []):
            source_id = str(source["source_id"])
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            source_registry.append(dict(source))
    ledger_path = root / "analysis_results.json"
    ledger_path.write_text(
        json.dumps(
            {
                "contract_version": OUTPUT_CONTRACT_VERSION,
                "mode": mode,
                "batch_status": batch_status,
                "provider_provenance": provider_provenance,
                "expected_count": len(inputs),
                "attempted_count": attempted_count,
                "researched_count": attempted_count,
                "reused_count": reused_count,
                "candidate_actions": candidate_actions,
                "source_registry": source_registry,
                "generated_at": generated_at,
                "result_count": len(outputs),
                "results": outputs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_path = root / "summary.md"
    summary_path.write_text(
        _render_summary(
            mode,
            outputs,
            generated_at,
            batch_status,
            candidate_actions,
        ),
        encoding="utf-8",
    )
    return outputs, summary_path, ledger_path


def _run_codex_candidate(
    payload: Mapping[str, Any],
    *,
    root: Path,
    schema_path: Path,
    timeout_seconds: int,
    project_dir: Path,
    command_runner: Any,
    codex_runtime: str,
) -> dict[str, Any]:
    analysis_id = str(payload["analysis_id"])
    candidate = payload["candidate"]
    symbol = str(candidate["symbol"])
    rank = int(payload["rank"])
    input_dir = root / "inputs"
    output_root = root / "outputs"
    input_dir.mkdir(exist_ok=True)
    output_root.mkdir(exist_ok=True)
    input_path = input_dir / f"{rank:02d}_{symbol}.json"
    output_path = output_root / f"{rank:02d}_{symbol}.json"
    input_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    instruction = {
        "task": (
            "Analyze only the supplied Value Discover candidate envelope. Do not run commands, "
            "browse, read credentials, access financial accounts, or prepare any order. Preserve "
            "source IDs exactly. Separate Fact, Estimate, Inference, and PM judgment. Use only "
            "research_candidate, watchlist, or deprioritized; this is not a buy/sell recommendation. "
            "Return exactly one JSON object conforming to the supplied output schema."
        ),
        "required_identity": {
            "contract_version": OUTPUT_CONTRACT_VERSION,
            "analysis_id": analysis_id,
            "symbol": symbol,
            "mode": "codex",
            "status": "ok",
        },
        "candidate_input": payload,
    }
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--json",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema_path),
        "-o",
        str(output_path),
        "-C",
        str(project_dir),
        "-",
    ]
    started_at = _now_iso()
    try:
        completed = command_runner(
            command,
            input=json.dumps(instruction),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_codex_subprocess_environment(),
        )
    except subprocess.TimeoutExpired:
        _write_execution_receipt(
            output_root,
            rank,
            symbol,
            runtime=codex_runtime,
            started_at=started_at,
            returncode=None,
            timed_out=True,
            events="",
        )
        return _error_output(
            payload,
            status="timeout",
            error=f"{symbol} Codex analysis exceeded {timeout_seconds} seconds",
            runtime=codex_runtime,
        )
    _write_execution_receipt(
        output_root,
        rank,
        symbol,
        runtime=codex_runtime,
        started_at=started_at,
        returncode=completed.returncode,
        timed_out=False,
        events=completed.stdout or "",
        failure_message=_codex_failure_message(
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        ),
    )
    if completed.returncode != 0:
        error = _codex_failure_message(
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        return _error_output(
            payload,
            status="error",
            error=error,
            runtime=codex_runtime,
        )
    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
        output["provenance"]["executor"] = "Codex CLI"
        output["provenance"]["runtime"] = codex_runtime
        output["provenance"]["generated_at"] = _now_iso()
        _validate_output(output, payload, expected_mode="codex")
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return _error_output(
            payload,
            status="invalid_output",
            error=str(exc),
            runtime=codex_runtime,
        )
    return output


def _validate_output(
    output: Mapping[str, Any],
    candidate_input: Mapping[str, Any],
    *,
    expected_mode: str,
) -> None:
    required = set(_OUTPUT_SCHEMA["required"])
    keys = set(output)
    if keys != required:
        raise ValueError(
            f"output keys must exactly match schema; missing={sorted(required - keys)} "
            f"extra={sorted(keys - required)}"
        )
    expected = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "analysis_id": candidate_input["analysis_id"],
        "symbol": candidate_input["candidate"]["symbol"],
        "mode": expected_mode,
    }
    for key, value in expected.items():
        if output.get(key) != value:
            raise ValueError(f"output {key} must equal {value!r}")
    if output.get("status") not in OUTPUT_STATUSES:
        raise ValueError("output status is invalid")
    if output.get("status") == "ok" and output.get("error") is not None:
        raise ValueError("ok output must have error=null")
    if output.get("status") != "ok" and not output.get("error"):
        raise ValueError("non-ok output must include an error")
    if output.get("classification") not in _OUTPUT_SCHEMA["properties"]["classification"]["enum"]:
        raise ValueError("output classification is invalid")
    if not isinstance(output.get("claims"), list) or not isinstance(output.get("data_gaps"), list):
        raise ValueError("output claims and data_gaps must be arrays")
    available_sources = {
        source["source_id"] for source in candidate_input.get("sources", [])
    }
    for claim in output["claims"]:
        if set(claim.get("source_ids", [])) - available_sources:
            raise ValueError("output claim references an unknown source_id")
    provenance = output.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("output provenance must be an object")
    if set(provenance.get("source_ids", [])) - available_sources:
        raise ValueError("output provenance references an unknown source_id")


def _validate_reusable_output(
    output: Mapping[str, Any],
    *,
    symbol: str,
    expected_mode: str,
) -> None:
    if output.get("status") != "ok":
        raise ValueError("only successful prior analysis can be reused")
    source_ids = {
        str(source_id)
        for claim in output.get("claims", [])
        if isinstance(claim, Mapping)
        for source_id in claim.get("source_ids", [])
    }
    provenance = output.get("provenance")
    if isinstance(provenance, Mapping):
        source_ids.update(str(value) for value in provenance.get("source_ids", []))
    candidate_input = {
        "analysis_id": output.get("analysis_id"),
        "candidate": {"symbol": symbol},
        "sources": [{"source_id": source_id} for source_id in source_ids],
    }
    _validate_output(output, candidate_input, expected_mode=expected_mode)


def _validated_reused_result(
    reuse: Mapping[str, Any],
    *,
    symbol: str,
    mode: str,
) -> dict[str, Any]:
    result = reuse.get("result")
    if not isinstance(result, Mapping):
        raise ValueError(f"reusable analysis for {symbol} has no result")
    _validate_reusable_output(result, symbol=symbol, expected_mode=mode)
    return deepcopy(dict(result))


def _reuse_action(
    symbol: str,
    output: Mapping[str, Any],
    reuse: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "action": "reused",
        "analysis_id": output["analysis_id"],
        "analysis_date": reuse.get("analysis_date"),
        "ledger_path": str(reuse["ledger_path"]) if reuse.get("ledger_path") else None,
    }


def _researched_action(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(payload["candidate"]["symbol"]).upper(),
        "action": "researched",
        "analysis_id": payload["analysis_id"],
        "analysis_date": payload.get("analysis_date"),
        "ledger_path": None,
    }


def _error_output(
    payload: Mapping[str, Any],
    *,
    status: str,
    error: str,
    runtime: str,
) -> dict[str, Any]:
    return {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "analysis_id": payload["analysis_id"],
        "symbol": payload["candidate"]["symbol"],
        "mode": "codex",
        "status": status,
        "classification": "unknown",
        "summary": "",
        "claims": [],
        "data_gaps": ["Codex analysis did not produce a valid structured result."],
        "provenance": {
            "executor": "Codex CLI",
            "runtime": runtime,
            "source_ids": [source["source_id"] for source in payload.get("sources", [])],
            "generated_at": _now_iso(),
        },
        "error": error,
    }


def _render_summary(
    mode: str,
    outputs: Sequence[Mapping[str, Any]],
    generated_at: str,
    batch_status: str,
    candidate_actions: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Value Discover Candidate Analysis",
        "",
        f"Mode: `{mode}`",
        f"Batch status: `{batch_status}`",
        f"Generated: {generated_at}",
        "",
        "Research triage only; not financial advice or an order recommendation.",
        "",
        "| Symbol | Action | Status | Classification | Summary | Error |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for output, action in zip(outputs, candidate_actions, strict=True):
        values = [
            str(output.get("symbol", "")),
            str(action.get("action", "")),
            str(output.get("status", "")),
            str(output.get("classification", "")),
            str(output.get("summary", "")).replace("|", "\\|").replace("\n", " "),
            str(output.get("error") or "").replace("|", "\\|").replace("\n", " "),
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _codex_subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in EXTERNAL_MODEL_CREDENTIAL_ENV_VARS:
        environment.pop(name, None)
    return environment


def _write_execution_receipt(
    output_root: Path,
    rank: int,
    symbol: str,
    *,
    runtime: str,
    started_at: str,
    returncode: int | None,
    timed_out: bool,
    events: str,
    failure_message: str | None = None,
) -> None:
    events_path = output_root / f"{rank:02d}_{symbol}.events.jsonl"
    events_path.write_text(events, encoding="utf-8")
    thread_id = None
    usage = None
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
    receipt = {
        "executor": "Codex CLI",
        "runtime": runtime,
        "ephemeral": True,
        "sandbox": "read-only",
        "user_config_loaded": False,
        "credential_variables_removed": [
            name for name in EXTERNAL_MODEL_CREDENTIAL_ENV_VARS if name in os.environ
        ],
        "started_at": started_at,
        "completed_at": _now_iso(),
        "returncode": returncode,
        "timed_out": timed_out,
        "failure_message": failure_message,
        "thread_id": thread_id,
        "usage": usage,
        "events_path": str(events_path),
    }
    (output_root / f"{rank:02d}_{symbol}.receipt.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )


def _codex_failure_message(*, stdout: str, stderr: str) -> str:
    """Prefer structured Codex event errors over rollout warnings on stderr."""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.failed":
            error = event.get("error")
            if isinstance(error, Mapping) and error.get("message"):
                return str(error["message"]).strip()
            if isinstance(error, str) and error.strip():
                return error.strip()
        if event.get("type") == "error" and event.get("message"):
            return str(event["message"]).strip()
        item = event.get("item")
        if (
            isinstance(item, Mapping)
            and item.get("type") == "error"
            and item.get("message")
        ):
            return str(item["message"]).strip()

    fallback = (stderr or stdout or "Codex exec failed").strip()
    return fallback[-2000:] if len(fallback) > 2000 else fallback
