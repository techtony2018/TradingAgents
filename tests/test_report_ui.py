from __future__ import annotations

import json

from tradingagents.report_index import build_report_index, write_report_index
from tradingagents.web import render_home


def test_report_index_discovers_latest_value_discover_run(tmp_path):
    day = tmp_path / "value_discover" / "2026-06-07"
    day.mkdir(parents=True)
    (day / "value_discover_20260607_072000.md").write_text("# Value", encoding="utf-8")
    (day / "value_discover_20260607_072000.csv").write_text("symbol\nCHEAP\n", encoding="utf-8")
    (day / "public_equity_idea_generation_20260607_072000.json").write_text(
        json.dumps({"kind": "public_equity_investing_dashboard.v1"}),
        encoding="utf-8",
    )
    (day / "public_equity_idea_generation_20260607_072000.md").write_text(
        "# PE", encoding="utf-8"
    )
    (day / "llm_analysis").mkdir()
    (day / "llm_analysis" / "summary.md").write_text("# Summary", encoding="utf-8")

    index_path = write_report_index(tmp_path)
    index = build_report_index(tmp_path)

    assert index_path.exists()
    assert index["latest_value_discover"]["date"] == "2026-06-07"
    assert index["latest_value_discover"]["public_equity_payload"].endswith(".json")


def test_report_index_discovers_stock_analysis_runs(tmp_path):
    run = tmp_path / "stock_analysis" / "NVDA" / "2026-06-07" / "101010"
    run.mkdir(parents=True)
    (run / "complete_report.md").write_text("# NVDA", encoding="utf-8")
    (run / "status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "ticker": "NVDA",
                "analysis_date": "2026-06-07",
                "decision": "Hold",
                "llm_provider": "nvidia",
                "quick_think_llm": "google/gemma-3n-e4b-it",
                "deep_think_llm": "google/gemma-4-31b-it",
                "steps": [{"id": "run_agents", "label": "Run analyst and decision agents", "status": "ok"}],
                "events": [{"kind": "llm", "message": "LLM completed"}],
            }
        ),
        encoding="utf-8",
    )

    index = build_report_index(tmp_path)

    assert index["latest_stock_analysis"]["ticker"] == "NVDA"
    assert index["latest_stock_analysis"]["complete_report"].endswith("complete_report.md")
    assert index["latest_stock_analysis"]["llm_provider"] == "nvidia"
    assert index["latest_stock_analysis"]["steps"][0]["status"] == "ok"


def test_web_home_renders_report_links(tmp_path):
    day = tmp_path / "value_discover" / "2026-06-07"
    day.mkdir(parents=True)
    (day / "value_discover_20260607_072000.md").write_text("# Value", encoding="utf-8")
    (day / "public_equity_idea_generation_20260607_072000.json").write_text(
        json.dumps(
            {
                "snapshot": [{"label": "Candidates", "value": 10, "unit": "names"}],
                "tabs": [],
            }
        ),
        encoding="utf-8",
    )

    html = render_home(tmp_path)

    assert "TradingAgents Reports" in html
    assert "Run Stock Analysis" in html
    assert "Analyze Stock" in html
    assert "NVIDIA NIM" in html
    assert "google/gemma-4-31b-it" in html
    assert "data-analysis-form" in html
    assert "Latest Run: 2026-06-07" in html
    assert "Open shortlist" in html
