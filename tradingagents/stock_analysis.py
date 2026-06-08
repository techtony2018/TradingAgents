"""Programmatic stock analysis runner used by the web UI."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Sequence

from cli.main import save_report_to_disk
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.report_index import write_report_index


DEFAULT_ANALYSTS: tuple[str, ...] = ("market", "social", "news", "fundamentals")


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

    run_root = Path(output_dir) / symbol / date / dt.datetime.now().strftime("%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)

    run_config = DEFAULT_CONFIG.copy()
    if config:
        run_config.update(config)
    run_config.setdefault("checkpoint_enabled", False)

    status_path = run_root / "status.json"
    _write_status(
        status_path,
        {
            "status": "running",
            "ticker": symbol,
            "analysis_date": date,
            "started_at": dt.datetime.now().isoformat(),
            "selected_analysts": list(selected_analysts),
        },
    )
    try:
        graph = TradingAgentsGraph(
            list(selected_analysts),
            debug=False,
            config=run_config,
        )
        final_state, decision = graph.propagate(symbol, date, asset_type="stock")
        report_path = save_report_to_disk(final_state, symbol, run_root)
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
        _write_status(status_path, payload)
        write_report_index(Path(output_dir).parent)
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
        _write_status(status_path, payload)
        (run_root / "error.md").write_text(
            f"# {symbol} Analysis Failed\n\n{exc}\n",
            encoding="utf-8",
        )
        write_report_index(Path(output_dir).parent)
        return payload


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
    args = parser.parse_args()
    result = run_stock_analysis(
        args.ticker,
        analysis_date=args.analysis_date,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
