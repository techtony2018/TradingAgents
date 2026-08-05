from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tradingagents.value_discover import ValueCandidate


def _candidate() -> ValueCandidate:
    return ValueCandidate(
        symbol="CHEAP",
        company="Cheap Co",
        sector="Industrials",
        price=50.0,
        market_cap=50_000_000_000,
        trailing_pe=10.0,
        forward_pe=8.0,
        price_to_book=1.2,
        ev_to_ebitda=7.0,
        profit_margin=0.18,
        return_on_equity=0.19,
        debt_to_equity=60.0,
        target_upside_pct=0.30,
        rsi_14=48.0,
        avg_volume_20d=2_000_000.0,
        score=72.0,
        thesis="Low valuation",
        caveats="Target estimate needs corroboration",
    )


def test_value_discover_analysis_module_exists():
    assert importlib.util.find_spec("tradingagents.value_discover_analysis") is not None


def test_analysis_mode_is_explicit_safe_and_backwards_compatible():
    from tradingagents import value_discover_analysis as analysis

    resolve = getattr(analysis, "resolve_analysis_mode", None)
    assert callable(resolve)
    assert resolve({}) == "codex"
    assert resolve({"TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE": "embedded"}) == "embedded"
    assert resolve({"TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED": "true"}) == "embedded"
    assert resolve({"TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED": "false"}) == "disabled"
    assert resolve(
        {
            "TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED": "true",
            "TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE": "codex",
        }
    ) == "codex"
    with pytest.raises(ValueError, match="analysis mode"):
        resolve({"TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE": "automatic"})


def test_timeout_and_batch_status_contracts_are_deterministic():
    from tradingagents import value_discover_analysis as analysis

    timeout = getattr(analysis, "resolve_analysis_timeout", None)
    batch_status = getattr(analysis, "analysis_batch_status", None)
    assert callable(timeout)
    assert callable(batch_status)
    assert timeout({}) == 180
    assert timeout({"TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_TIMEOUT_SECONDS": "45"}) == 45
    with pytest.raises(ValueError, match="timeout"):
        timeout({"TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_TIMEOUT_SECONDS": "0"})

    assert batch_status("disabled", []) == "skipped"
    assert batch_status("codex", [{"status": "ok"}]) == "ok"
    assert batch_status("codex", [{"status": "timeout"}]) == "timeout"
    assert batch_status("codex", [{"status": "ok"}, {"status": "error"}]) == "partial"


def test_candidate_input_and_output_schema_are_shared_and_provenance_first(tmp_path):
    from tradingagents import value_discover_analysis as analysis

    build_input = getattr(analysis, "build_candidate_analysis_input", None)
    output_schema = getattr(analysis, "analysis_output_schema", None)
    assert callable(build_input)
    assert callable(output_schema)

    shortlist = tmp_path / "shortlist.csv"
    shortlist.write_text("symbol,score\nCHEAP,72\n", encoding="utf-8")
    payload = build_input(
        _candidate(),
        rank=1,
        analysis_date="2026-08-02",
        sources=(
            {
                "source_id": "shortlist-csv",
                "source_type": "local_report",
                "location": str(shortlist),
                "as_of": "2026-08-02",
                "retrieved_at": "2026-08-02T20:20:00-07:00",
            },
            {
                "source_id": "issuer-filing",
                "source_type": "primary_filing",
                "location": "https://www.sec.gov/example",
                "as_of": "2026-07-30",
                "retrieved_at": "2026-08-02T20:20:00-07:00",
            },
        ),
    )

    assert payload["contract_version"] == "value_discover.candidate_analysis_input.v1"
    assert payload["candidate"]["symbol"] == "CHEAP"
    assert payload["sources"][0]["source_id"] == "shortlist-csv"
    assert payload["sources"][0]["location"] == str(shortlist)
    for field in (
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
    ):
        fact = payload["financial_facts"][field]
        assert fact["value"] == payload["candidate"][field]
        assert fact["source_ids"] == ["shortlist-csv"]
        assert fact["as_of"] == "2026-08-02"
        assert fact["unknown_reason"] is None

    schema = output_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {
        "contract_version",
        "analysis_id",
        "symbol",
        "mode",
        "status",
        "classification",
        "claims",
        "data_gaps",
        "provenance",
        "error",
    }
    assert schema["properties"]["mode"]["enum"] == ["embedded", "codex"]
    assert "timeout" in schema["properties"]["status"]["enum"]


def _candidate_input(tmp_path: Path) -> dict:
    from tradingagents.value_discover_analysis import build_candidate_analysis_input

    shortlist = tmp_path / "shortlist.csv"
    shortlist.write_text("symbol,score\nCHEAP,72\n", encoding="utf-8")
    return build_candidate_analysis_input(
        _candidate(),
        rank=1,
        analysis_date="2026-08-02",
        sources=(
            {
                "source_id": "shortlist-csv",
                "source_type": "local_report",
                "location": str(shortlist),
                "as_of": "2026-08-02",
                "retrieved_at": "2026-08-02T20:20:00-07:00",
            },
        ),
    )


def _valid_codex_output() -> dict:
    return {
        "contract_version": "value_discover.candidate_analysis_output.v1",
        "analysis_id": "2026-08-02:01:CHEAP",
        "symbol": "CHEAP",
        "mode": "codex",
        "status": "ok",
        "classification": "watchlist",
        "summary": "Cheap valuation requires source-backed confirmation.",
        "claims": [
            {
                "claim_type": "Fact",
                "text": "The frozen screen score is 72.",
                "source_ids": ["shortlist-csv"],
            }
        ],
        "data_gaps": ["No primary filing was included in this bounded proof."],
        "provenance": {
            "executor": "model-supplied-value",
            "runtime": "model-supplied-value",
            "source_ids": ["shortlist-csv"],
            "generated_at": "2026-08-02T20:21:00-07:00",
        },
        "error": None,
    }


def test_codex_mode_is_ephemeral_read_only_structured_and_writes_report_artifacts(
    tmp_path, monkeypatch
):
    from tradingagents import value_discover_analysis as analysis

    run_batch = getattr(analysis, "run_analysis_batch", None)
    assert callable(run_batch)
    observed = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-codex")

    def command_runner(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(json.dumps(_valid_codex_output()), encoding="utf-8")
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "ephemeral-test"}),
                json.dumps({"type": "turn.completed", "usage": {"output_tokens": 20}}),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    outputs, summary_path, ledger_path = run_batch(
        [_candidate_input(tmp_path)],
        mode="codex",
        output_dir=tmp_path / "analysis",
        timeout_seconds=30,
        project_dir=tmp_path,
        command_runner=command_runner,
        codex_runtime="codex-cli-test",
    )

    command = observed["command"]
    assert command[:2] == ["codex", "exec"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--json" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in command
    assert command[-1] == "-"
    assert observed["kwargs"]["timeout"] == 30
    assert "OPENROUTER_API_KEY" not in observed["kwargs"]["env"]
    assert "OPENAI_API_KEY" not in observed["kwargs"]["env"]
    assert "CODEX_API_KEY" not in observed["kwargs"]["env"]
    assert outputs[0]["provenance"]["executor"] == "Codex CLI"
    assert outputs[0]["provenance"]["runtime"] == "codex-cli-test"
    assert summary_path.exists()
    assert ledger_path.exists()
    assert (tmp_path / "analysis/outputs/01_CHEAP.events.jsonl").exists()
    receipt = json.loads(
        (tmp_path / "analysis/outputs/01_CHEAP.receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["thread_id"] == "ephemeral-test"
    assert receipt["returncode"] == 0
    assert set(receipt["credential_variables_removed"]) >= {
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    }
    normalized_output = json.loads(
        (tmp_path / "analysis/outputs/01_CHEAP.json").read_text(encoding="utf-8")
    )
    assert normalized_output["provenance"]["runtime"] == "codex-cli-test"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["mode"] == "codex"
    assert ledger["batch_status"] == "ok"
    assert ledger["provider_provenance"] == {
        "execution_provider": "codex_cli",
        "runtime": "codex-cli-test",
        "external_model_api_invoked": False,
        "embedded_provider": None,
        "embedded_models": [],
    }
    assert ledger["source_registry"][0]["source_id"] == "shortlist-csv"
    assert ledger["source_registry"][0]["as_of"] == "2026-08-02"


def test_codex_timeout_is_terminal_and_never_falls_back_to_embedded(tmp_path):
    from tradingagents import value_discover_analysis as analysis

    embedded_called = False

    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    def embedded_runner(inputs):
        nonlocal embedded_called
        embedded_called = True
        return []

    first = _candidate_input(tmp_path)
    second = deepcopy(first)
    second["rank"] = 2
    second["analysis_id"] = "2026-08-02:02:SECOND"
    second["candidate"]["symbol"] = "SECOND"
    command_attempts = 0

    def counted_timeout_runner(command, **kwargs):
        nonlocal command_attempts
        command_attempts += 1
        return timeout_runner(command, **kwargs)

    outputs, _, _ = analysis.run_analysis_batch(
        [first, second],
        mode="codex",
        output_dir=tmp_path / "analysis",
        timeout_seconds=1,
        project_dir=tmp_path,
        command_runner=counted_timeout_runner,
        embedded_runner=embedded_runner,
        codex_runtime="codex-cli-test",
    )

    assert embedded_called is False
    assert command_attempts == 1
    assert len(outputs) == 1
    assert outputs[0]["status"] == "timeout"
    assert outputs[0]["mode"] == "codex"
    assert "exceeded 1 seconds" in outputs[0]["error"]


def test_codex_nonzero_prefers_structured_failure_over_stderr_warnings(tmp_path):
    from tradingagents import value_discover_analysis as analysis

    warning_tail = "\n".join(
        [
            "2026-08-04T13:51:30Z WARN codex_rollout::list: falling_back",
            "2026-08-04T13:51:31Z WARN codex_rollout::list: falling_back",
        ]
    )
    limit_message = (
        "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
        "to purchase more credits or try again at Aug 7th, 2026 8:36 PM."
    )

    def command_runner(command, **kwargs):
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "quota-test"}),
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "error", "message": limit_message}),
                json.dumps({"type": "turn.failed", "error": {"message": limit_message}}),
            ]
        )
        return subprocess.CompletedProcess(command, 1, stdout=stdout, stderr=warning_tail)

    outputs, _, ledger_path = analysis.run_analysis_batch(
        [_candidate_input(tmp_path)],
        mode="codex",
        output_dir=tmp_path / "analysis",
        timeout_seconds=30,
        project_dir=tmp_path,
        command_runner=command_runner,
        codex_runtime="codex-cli-test",
    )

    assert outputs[0]["status"] == "error"
    assert outputs[0]["error"] == limit_message
    assert "codex_rollout" not in outputs[0]["error"]
    receipt = json.loads(
        (tmp_path / "analysis/outputs/01_CHEAP.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["failure_message"] == limit_message
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["results"][0]["error"] == limit_message


def test_analysis_provider_provenance_distinguishes_codex_and_embedded():
    from tradingagents import value_discover_analysis as analysis

    codex = analysis.analysis_provider_provenance(
        "codex", codex_runtime="codex-cli-test"
    )
    embedded = analysis.analysis_provider_provenance(
        "embedded",
        embedded_config={
            "llm_provider": "openrouter",
            "quick_think_llm": "google/gemma-4-26b-a4b-it",
            "deep_think_llm": "google/gemma-4-26b-a4b-it",
        },
    )

    assert codex["execution_provider"] == "codex_cli"
    assert codex["external_model_api_invoked"] is False
    assert embedded["execution_provider"] == "embedded_model"
    assert embedded["external_model_api_invoked"] is True
    assert embedded["embedded_provider"] == "openrouter"
    assert embedded["embedded_models"] == ["google/gemma-4-26b-a4b-it"]


def test_scheduled_main_defaults_to_codex_and_writes_status_provenance(
    tmp_path, monkeypatch
):
    from tradingagents import public_equity, report_index, value_discover
    from tradingagents import value_discover_analysis as analysis

    output_dir = tmp_path / "reports" / "value_discover"
    monkeypatch.setenv("TRADINGAGENTS_VALUE_DISCOVER_DIR", str(output_dir))
    monkeypatch.delenv("TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED", raising=False)
    markdown_path = output_dir / "screen.md"
    csv_path = output_dir / "screen.csv"
    public_json = output_dir / "public.json"
    public_markdown = output_dir / "public.md"
    for path in (markdown_path, csv_path, public_json, public_markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("proof", encoding="utf-8")

    monkeypatch.setattr(
        value_discover,
        "run_value_discover",
        lambda **kwargs: ([_candidate()], markdown_path, csv_path),
    )
    monkeypatch.setattr(
        public_equity,
        "write_idea_generation_payload",
        lambda *args, **kwargs: (public_json, public_markdown),
    )
    monkeypatch.setattr(
        value_discover,
        "run_llm_analysis_for_candidates",
        lambda *args, **kwargs: pytest.fail("embedded analysis must not run by default"),
    )
    monkeypatch.setattr(
        value_discover,
        "_codex_runtime_version",
        lambda: "codex-cli-test",
        raising=False,
    )

    def fake_run_analysis_batch(inputs, **kwargs):
        assert kwargs["mode"] == "codex"
        assert inputs[0]["financial_facts"]["price"]["as_of"]
        root = Path(kwargs["output_dir"])
        root.mkdir(parents=True, exist_ok=True)
        summary = root / "summary.md"
        ledger = root / "analysis_results.json"
        summary.write_text("# Codex proof", encoding="utf-8")
        ledger.write_text('{"mode":"codex","batch_status":"ok"}', encoding="utf-8")
        return [_valid_codex_output()], summary, ledger

    monkeypatch.setattr(analysis, "run_analysis_batch", fake_run_analysis_batch)

    def fake_write_report_index(root):
        path = Path(root) / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        return path

    monkeypatch.setattr(report_index, "write_report_index", fake_write_report_index)

    value_discover.main()

    status_paths = list(output_dir.glob("*/status.json"))
    assert len(status_paths) == 1
    status = json.loads(status_paths[0].read_text(encoding="utf-8"))
    assert status["status"] == "ok"
    assert status["analysis_mode"] == "codex"
    assert status["analysis_status"] == "ok"
    assert status["analysis_provider_provenance"] == {
        "execution_provider": "codex_cli",
        "runtime": "codex-cli-test",
        "external_model_api_invoked": False,
        "embedded_provider": None,
        "embedded_models": [],
    }
    assert status["analysis_summary"].endswith("candidate_analysis/summary.md")
    assert status["analysis_results"].endswith(
        "candidate_analysis/analysis_results.json"
    )


def test_scheduled_main_records_bounded_codex_failure_and_stops(
    tmp_path, monkeypatch
):
    from tradingagents import public_equity, report_index, value_discover
    from tradingagents import value_discover_analysis as analysis

    output_dir = tmp_path / "reports" / "value_discover"
    monkeypatch.setenv("TRADINGAGENTS_VALUE_DISCOVER_DIR", str(output_dir))
    monkeypatch.delenv("TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED", raising=False)
    markdown_path = output_dir / "screen.md"
    csv_path = output_dir / "screen.csv"
    public_json = output_dir / "public.json"
    public_markdown = output_dir / "public.md"
    for path in (markdown_path, csv_path, public_json, public_markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("proof", encoding="utf-8")
    candidates = [_candidate(), ValueCandidate(**{**_candidate().__dict__, "symbol": "SECOND"})]
    monkeypatch.setattr(
        value_discover,
        "run_value_discover",
        lambda **kwargs: (candidates, markdown_path, csv_path),
    )
    monkeypatch.setattr(
        public_equity,
        "write_idea_generation_payload",
        lambda *args, **kwargs: (public_json, public_markdown),
    )
    monkeypatch.setattr(
        value_discover,
        "run_llm_analysis_for_candidates",
        lambda *args, **kwargs: pytest.fail("embedded analysis must never be invoked"),
    )
    monkeypatch.setattr(
        value_discover,
        "_codex_runtime_version",
        lambda: "codex-cli-test",
        raising=False,
    )

    failure = _valid_codex_output()
    failure.update(
        {
            "status": "timeout",
            "classification": "unknown",
            "summary": "",
            "claims": [],
            "error": "CHEAP Codex analysis exceeded 30 seconds",
        }
    )

    def fake_run_analysis_batch(inputs, **kwargs):
        assert len(inputs) == 2
        root = Path(kwargs["output_dir"])
        root.mkdir(parents=True, exist_ok=True)
        summary = root / "summary.md"
        ledger = root / "analysis_results.json"
        summary.write_text("# Bounded failure", encoding="utf-8")
        ledger.write_text('{"mode":"codex","batch_status":"timeout"}', encoding="utf-8")
        return [failure], summary, ledger

    monkeypatch.setattr(analysis, "run_analysis_batch", fake_run_analysis_batch)
    index_called = False

    def fail_if_indexed(root):
        nonlocal index_called
        index_called = True
        pytest.fail("a failed Codex batch must stop before report-index success")

    monkeypatch.setattr(report_index, "write_report_index", fail_if_indexed)

    with pytest.raises(RuntimeError, match="exceeded 30 seconds"):
        value_discover.main()

    assert index_called is False
    status_path = next(output_dir.glob("*/status.json"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "error"
    assert status["analysis_status"] == "timeout"
    assert status["analysis_expected_count"] == 2
    assert status["analysis_attempted_count"] == 1
    assert status["analysis_success_count"] == 0
    assert status["analysis_error_count"] == 1
    assert status["analysis_provider_provenance"]["external_model_api_invoked"] is False


def test_embedded_adapter_preserves_direct_model_result_in_the_shared_schema(tmp_path):
    from tradingagents import value_discover_analysis as analysis

    normalize = getattr(analysis, "normalize_embedded_results", None)
    assert callable(normalize)
    candidate_input = _candidate_input(tmp_path)
    outputs = normalize(
        [candidate_input],
        [
            SimpleNamespace(
                symbol="CHEAP",
                status="ok",
                decision="Hold pending filing confirmation",
                report_path=tmp_path / "legacy.md",
                error=None,
            )
        ],
        runtime="openrouter/google-gemma-test",
    )

    assert outputs[0]["mode"] == "embedded"
    assert outputs[0]["status"] == "ok"
    assert outputs[0]["classification"] == "unknown"
    assert outputs[0]["summary"] == "Hold pending filing confirmation"
    assert outputs[0]["provenance"]["executor"] == "TradingAgentsGraph"
    assert outputs[0]["provenance"]["source_ids"] == ["shortlist-csv"]

    batch_outputs, _, _ = analysis.run_analysis_batch(
        [candidate_input],
        mode="embedded",
        output_dir=tmp_path / "analysis",
        timeout_seconds=30,
        project_dir=tmp_path,
        embedded_runner=lambda inputs: outputs,
    )
    assert batch_outputs == outputs


def test_ok_status_rejects_an_error_payload_as_invalid_output(tmp_path):
    from tradingagents import value_discover_analysis as analysis

    invalid = _valid_codex_output()
    invalid["error"] = "contradictory error"

    def command_runner(command, **kwargs):
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(json.dumps(invalid), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    outputs, _, _ = analysis.run_analysis_batch(
        [_candidate_input(tmp_path)],
        mode="codex",
        output_dir=tmp_path / "analysis",
        timeout_seconds=30,
        project_dir=tmp_path,
        command_runner=command_runner,
        codex_runtime="codex-cli-test",
    )

    assert outputs[0]["status"] == "invalid_output"
    assert "ok output must have error=null" in outputs[0]["error"]
