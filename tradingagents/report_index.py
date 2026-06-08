"""Report discovery and indexing utilities for TradingAgents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_report_index(report_root: Path | str = "reports") -> dict[str, Any]:
    root = Path(report_root)
    value_root = root / "value_discover"
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
            if not markdowns and not csvs and not public_equity_json:
                continue
            runs.append(
                {
                    "date": date_dir.name,
                    "value_discover_markdown": _path(markdowns[0] if markdowns else None),
                    "value_discover_csv": _path(csvs[0] if csvs else None),
                    "public_equity_payload": _path(public_equity_json[0] if public_equity_json else None),
                    "public_equity_markdown": _path(public_equity_md[0] if public_equity_md else None),
                    "llm_summary": _path(llm_summary if llm_summary.exists() else None),
                }
            )
    return {
        "version": 1,
        "report_root": str(root),
        "value_discover_runs": runs,
        "latest_value_discover": runs[0] if runs else None,
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
