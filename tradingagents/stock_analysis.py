"""Programmatic stock analysis runner used by the web UI."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import uuid
from typing import Any, Sequence

from langchain_core.callbacks import BaseCallbackHandler

from cli.main import save_report_to_disk
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.report_index import write_report_index


DEFAULT_ANALYSTS: tuple[str, ...] = ("market", "social", "news", "fundamentals")
PIPELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare", "Prepare run"),
    ("create_graph", "Create TradingAgents graph"),
    ("run_agents", "Run analyst and decision agents"),
    ("save_report", "Save report bundle"),
    ("index_report", "Update report index"),
)


def run_stock_analysis(
    ticker: str,
    *,
    analysis_date: str | None = None,
    output_dir: Path | str = "reports/stock_analysis",
    selected_analysts: Sequence[str] = DEFAULT_ANALYSTS,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a TradingAgents stock analysis and save a Markdown report bundle."""
    symbol = _normalize_ticker(ticker)
    date = analysis_date or dt.date.today().isoformat()
    _validate_date(date)

    run_id = dt.datetime.now().strftime("%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    run_root = Path(output_dir) / symbol / date / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    run_config = DEFAULT_CONFIG.copy()
    if config:
        run_config.update(config)
    run_config.setdefault("checkpoint_enabled", False)

    tracker = AnalysisProgressTracker(
        run_root / "status.json",
        ticker=symbol,
        analysis_date=date,
        run_dir=run_root,
        selected_analysts=selected_analysts,
        config=run_config,
    )
    tracker.start()
    try:
        tracker.step_running("create_graph")
        graph = TradingAgentsGraph(
            list(selected_analysts),
            debug=False,
            config=run_config,
            callbacks=[tracker],
        )
        tracker.step_ok("create_graph")
        tracker.step_running("run_agents")
        final_state, decision = graph.propagate(symbol, date, asset_type="stock")
        tracker.step_ok("run_agents")
        tracker.step_running("save_report")
        report_path = save_report_to_disk(final_state, symbol, run_root)
        tracker.step_ok("save_report", report_path=str(report_path))
        tracker.step_running("index_report")
        write_report_index(Path(output_dir).parent)
        tracker.step_ok("index_report")
        payload = {
            "status": "ok",
            "ticker": symbol,
            "analysis_date": date,
            "decision": str(decision),
            "report_path": str(report_path),
            "run_dir": str(run_root),
            "completed_at": dt.datetime.now().isoformat(),
            "selected_analysts": list(selected_analysts),
        }
        tracker.finish(payload)
        return payload
    except Exception as exc:
        payload = {
            "status": "error",
            "ticker": symbol,
            "analysis_date": date,
            "error": str(exc),
            "run_dir": str(run_root),
            "completed_at": dt.datetime.now().isoformat(),
            "selected_analysts": list(selected_analysts),
        }
        tracker.fail(str(exc), payload)
        (run_root / "error.md").write_text(
            f"# {symbol} Analysis Failed\n\n{exc}\n",
            encoding="utf-8",
        )
        write_report_index(Path(output_dir).parent)
        return payload


class AnalysisProgressTracker(BaseCallbackHandler):
    """Persist human-readable progress for a stock analysis run."""

    def __init__(
        self,
        status_path: Path,
        *,
        ticker: str,
        analysis_date: str,
        run_dir: Path,
        selected_analysts: Sequence[str],
        config: dict[str, Any],
    ) -> None:
        self.status_path = status_path
        self.payload: dict[str, Any] = {
            "status": "queued",
            "ticker": ticker,
            "analysis_date": analysis_date,
            "run_dir": str(run_dir),
            "started_at": dt.datetime.now().isoformat(),
            "selected_analysts": list(selected_analysts),
            "llm_provider": config.get("llm_provider"),
            "quick_think_llm": config.get("quick_think_llm"),
            "deep_think_llm": config.get("deep_think_llm"),
            "backend_url": config.get("backend_url"),
            "steps": [
                {"id": step_id, "label": label, "status": "pending"}
                for step_id, label in PIPELINE_STEPS
            ],
            "events": [],
        }

    def start(self) -> None:
        self.payload["status"] = "running"
        self.step_ok("prepare")

    def step_running(self, step_id: str) -> None:
        self._set_step(step_id, "running", started_at=dt.datetime.now().isoformat())
        self._append_event("step", f"{self._step_label(step_id)} started")

    def step_ok(self, step_id: str, **extra: Any) -> None:
        self._set_step(
            step_id,
            "ok",
            completed_at=dt.datetime.now().isoformat(),
            **extra,
        )
        self._append_event("step", f"{self._step_label(step_id)} completed")

    def finish(self, extra: dict[str, Any]) -> None:
        self.payload.update(extra)
        self.payload["status"] = "ok"
        self.payload["completed_at"] = dt.datetime.now().isoformat()
        self._write()

    def fail(self, error: str, extra: dict[str, Any]) -> None:
        current = self._current_step()
        if current:
            self._set_step(current, "error", error=error, completed_at=dt.datetime.now().isoformat())
        self.payload.update(extra)
        self.payload["status"] = "error"
        self.payload["error"] = error
        self.payload["completed_at"] = dt.datetime.now().isoformat()
        self._append_event("error", error)
        self._write()

    def on_llm_start(self, serialized, prompts, **kwargs):  # type: ignore[no-untyped-def]
        model = kwargs.get("invocation_params", {}).get("model")
        label = f"LLM started{': ' + model if model else ''}"
        self._append_event("llm", label)

    def on_llm_end(self, response, **kwargs):  # type: ignore[no-untyped-def]
        self._append_event("llm", "LLM completed")

    def on_llm_error(self, error, **kwargs):  # type: ignore[no-untyped-def]
        self._append_event("llm", f"LLM error: {error}")

    def on_tool_start(self, serialized, input_str, **kwargs):  # type: ignore[no-untyped-def]
        name = serialized.get("name") if isinstance(serialized, dict) else None
        self._append_event("tool", f"Tool started{': ' + name if name else ''}")

    def on_tool_end(self, output, **kwargs):  # type: ignore[no-untyped-def]
        self._append_event("tool", "Tool completed")

    def on_tool_error(self, error, **kwargs):  # type: ignore[no-untyped-def]
        self._append_event("tool", f"Tool error: {error}")

    def on_chain_start(self, serialized, inputs, **kwargs):  # type: ignore[no-untyped-def]
        name = serialized.get("name") if isinstance(serialized, dict) else None
        if name:
            self._append_event("chain", f"{name} started")

    def on_chain_end(self, outputs, **kwargs):  # type: ignore[no-untyped-def]
        self._append_event("chain", "Chain completed")

    def on_chain_error(self, error, **kwargs):  # type: ignore[no-untyped-def]
        self._append_event("chain", f"Chain error: {error}")

    def _set_step(self, step_id: str, status: str, **extra: Any) -> None:
        for step in self.payload["steps"]:
            if step["id"] == step_id:
                step.update({"status": status, **extra})
                break
        self._write()

    def _append_event(self, kind: str, message: str) -> None:
        events = self.payload.setdefault("events", [])
        events.append(
            {
                "time": dt.datetime.now().isoformat(),
                "kind": kind,
                "message": str(message),
            }
        )
        self.payload["events"] = events[-100:]
        self._write()

    def _current_step(self) -> str | None:
        for step in self.payload["steps"]:
            if step["status"] == "running":
                return step["id"]
        return None

    def _step_label(self, step_id: str) -> str:
        for step in self.payload["steps"]:
            if step["id"] == step_id:
                return step["label"]
        return step_id

    def _write(self) -> None:
        _write_status(self.status_path, self.payload)


def _normalize_ticker(ticker: str) -> str:
    symbol = ticker.strip().upper()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
    if not symbol or any(char not in allowed for char in symbol):
        raise ValueError("Ticker must contain only letters, numbers, dot, or dash.")
    return symbol


def _validate_date(value: str) -> None:
    parsed = dt.datetime.strptime(value, "%Y-%m-%d").date()
    if parsed > dt.date.today():
        raise ValueError("Analysis date cannot be in the future.")


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TradingAgents stock analysis")
    parser.add_argument("ticker")
    parser.add_argument("--date", dest="analysis_date")
    parser.add_argument("--output-dir", default="reports/stock_analysis")
    parser.add_argument("--provider", dest="llm_provider")
    parser.add_argument("--quick-model", dest="quick_think_llm")
    parser.add_argument("--deep-model", dest="deep_think_llm")
    parser.add_argument("--backend-url", dest="backend_url")
    args = parser.parse_args()
    config = {
        key: value
        for key, value in {
            "llm_provider": args.llm_provider,
            "quick_think_llm": args.quick_think_llm,
            "deep_think_llm": args.deep_think_llm,
            "backend_url": args.backend_url,
        }.items()
        if value
    }
    result = run_stock_analysis(
        args.ticker,
        analysis_date=args.analysis_date,
        output_dir=args.output_dir,
        config=config or None,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
