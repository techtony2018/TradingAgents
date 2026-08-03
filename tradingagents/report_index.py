"""Report discovery and indexing utilities for TradingAgents."""

from __future__ import annotations

import json
import csv
from pathlib import Path
from typing import Any


def build_report_index(report_root: Path | str = "reports") -> dict[str, Any]:
    root = Path(report_root)
    value_root = root / "value_discover"
    stock_root = root / "stock_analysis"
    runs: list[dict[str, Any]] = []
    if value_root.exists():
        for date_dir in sorted(value_root.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            markdowns = sorted(date_dir.glob("value_discover_*.md"), reverse=True)
            csvs = sorted(date_dir.glob("value_discover_*.csv"), reverse=True)
            public_equity_json = sorted(date_dir.glob("public_equity_idea_generation_*.json"), reverse=True)
            public_equity_md = sorted(date_dir.glob("public_equity_idea_generation_*.md"), reverse=True)
            llm_summary = date_dir / "llm_analysis" / "summary.md"
            status_path = date_dir / "status.json"
            if not markdowns and not csvs and not public_equity_json:
                continue
            status = _load_json(status_path) if status_path.exists() else {}
            csv_summary = _summarize_value_csv(csvs[0]) if csvs else {}
            llm_counts = _summarize_llm_summary(llm_summary) if llm_summary.exists() else {}
            public_equity_summary = (
                _summarize_public_equity_payload(public_equity_json[0])
                if public_equity_json
                else {"metrics": [], "workflow_route_count": 0, "top_workflow_routes": []}
            )
            runs.append(
                {
                    "date": date_dir.name,
                    "status": status.get("status") or "ok",
                    "error": status.get("error"),
                    "started_at": status.get("started_at"),
                    "completed_at": status.get("completed_at"),
                    "candidate_count": status.get("candidate_count", csv_summary.get("candidate_count", 0)),
                    "top_candidates": csv_summary.get("top_candidates", []),
                    "llm_success_count": status.get("llm_success_count", llm_counts.get("ok", 0)),
                    "llm_error_count": status.get("llm_error_count", llm_counts.get("error", 0)),
                    "metrics": public_equity_summary["metrics"],
                    "public_equity_workflow_route_count": public_equity_summary["workflow_route_count"],
                    "top_public_equity_routes": public_equity_summary["top_workflow_routes"],
                    "steps": status.get("steps", []),
                    "value_discover_markdown": _path(markdowns[0] if markdowns else None),
                    "value_discover_csv": _path(csvs[0] if csvs else None),
                    "public_equity_payload": _path(public_equity_json[0] if public_equity_json else None),
                    "public_equity_markdown": _path(public_equity_md[0] if public_equity_md else None),
                    "llm_summary": _path(llm_summary if llm_summary.exists() else None),
                    "status_json": _path(status_path if status_path.exists() else None),
                }
            )
    stock_runs: list[dict[str, Any]] = []
    if stock_root.exists():
        status_paths = sorted(
            stock_root.glob("*/*/*/status.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for status_path in status_paths:
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            run_dir = status_path.parent
            report_path = run_dir / "complete_report.md"
            error_path = run_dir / "error.md"
            stock_runs.append(
                {
                    "ticker": status.get("ticker"),
                    "analysis_date": status.get("analysis_date"),
                    "status": status.get("status"),
                    "decision": status.get("decision"),
                    "error": status.get("error"),
                    "run_dir": str(run_dir),
                    "complete_report": _path(report_path if report_path.exists() else None),
                    "error_report": _path(error_path if error_path.exists() else None),
                    "status_json": str(status_path),
                    "completed_at": status.get("completed_at"),
                    "started_at": status.get("started_at"),
                    "llm_provider": status.get("llm_provider"),
                    "quick_think_llm": status.get("quick_think_llm"),
                    "deep_think_llm": status.get("deep_think_llm"),
                    "steps": status.get("steps", []),
                    "events": status.get("events", []),
                }
            )
    return {
        "version": 1,
        "report_root": str(root),
        "value_discover_runs": runs,
        "latest_value_discover": runs[0] if runs else None,
        "stock_analysis_runs": stock_runs,
        "latest_stock_analysis": stock_runs[0] if stock_runs else None,
    }


def write_report_index(report_root: Path | str = "reports") -> Path:
    root = Path(report_root)
    root.mkdir(parents=True, exist_ok=True)
    index = build_report_index(root)
    path = root / "index.json"
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return path


def _path(path: Path | None) -> str | None:
    return str(path) if path else None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _summarize_value_csv(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return {"candidate_count": 0, "top_candidates": []}
    top_candidates = [
        {
            "symbol": row.get("symbol", ""),
            "score": row.get("score", ""),
            "thesis": row.get("thesis", ""),
        }
        for row in rows[:5]
    ]
    return {"candidate_count": len(rows), "top_candidates": top_candidates}


def _summarize_llm_summary(path: Path) -> dict[str, int]:
    counts = {"ok": 0, "error": 0}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return counts
    for line in lines:
        if not line.startswith("|") or "---" in line or "Ticker" in line:
            continue
        parts = [part.strip().lower() for part in line.strip("|").split("|")]
        if len(parts) < 2:
            continue
        status = parts[1]
        if status in counts:
            counts[status] += 1
    return counts


def _summarize_public_equity_payload(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    metrics = payload.get("snapshot")
    routes: list[dict[str, Any]] = []
    for tab in payload.get("tabs", []):
        if tab.get("id") != "workflow-router":
            continue
        for module in tab.get("modules", []):
            if module.get("type") == "workflow_routes":
                routes = module.get("rows", [])
                break
    return {
        "metrics": metrics if isinstance(metrics, list) else [],
        "workflow_route_count": len(routes),
        "top_workflow_routes": routes[:8],
    }
