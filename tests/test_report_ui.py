from __future__ import annotations

import json

from tradingagents.report_index import build_report_index, write_report_index
from tradingagents import web
from tradingagents.web import render_home, render_value_discover_control


def test_report_index_discovers_latest_value_discover_run(tmp_path):
    day = tmp_path / "value_discover" / "2026-06-07"
    day.mkdir(parents=True)
    (day / "value_discover_20260607_072000.md").write_text("# Value", encoding="utf-8")
    (day / "value_discover_20260607_072000.csv").write_text(
        "symbol,score,thesis\nCHEAP,92.5,Deep value setup\n", encoding="utf-8"
    )
    (day / "public_equity_idea_generation_20260607_072000.json").write_text(
        json.dumps(
            {
                "kind": "public_equity_investing_dashboard.v1",
                "snapshot": [{"label": "Universe output", "value": 1, "unit": "candidate"}],
                "tabs": [
                    {"id": "screen-summary", "modules": []},
                    {"id": "candidate-board", "modules": []},
                    {"id": "next-actions", "modules": []},
                    {
                        "id": "workflow-router",
                        "modules": [
                            {"type": "workflow_catalog", "rows": []},
                            {
                                "type": "workflow_routes",
                                "rows": [
                                    {
                                        "ticker": "CHEAP",
                                        "priority": "P0",
                                        "workflow": "company-tearsheet",
                                        "why": "Build baseline",
                                        "requires_llm": False,
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (day / "public_equity_idea_generation_20260607_072000.md").write_text(
        "# PE", encoding="utf-8"
    )
    (day / "llm_analysis").mkdir()
    (day / "llm_analysis" / "summary.md").write_text(
        "# Summary\n\n| Ticker | Status | Decision | Report | Error |\n| --- | --- | --- | --- | --- |\n| CHEAP | ok | Buy | path |  |\n",
        encoding="utf-8",
    )

    index_path = write_report_index(tmp_path)
    index = build_report_index(tmp_path)

    assert index_path.exists()
    assert index["latest_value_discover"]["date"] == "2026-06-07"
    assert index["latest_value_discover"]["public_equity_payload"].endswith(".json")
    assert index["latest_value_discover"]["status"] == "ok"
    assert index["latest_value_discover"]["candidate_count"] == 1
    assert index["latest_value_discover"]["top_candidates"][0]["symbol"] == "CHEAP"
    assert index["latest_value_discover"]["llm_success_count"] == 1
    assert index["latest_value_discover"]["public_equity_workflow_route_count"] == 1
    assert index["latest_value_discover"]["top_public_equity_routes"][0]["workflow"] == "company-tearsheet"


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
    (day / "value_discover_20260607_072000.csv").write_text(
        "symbol,score,thesis\nCHEAP,91,Deep value setup\n",
        encoding="utf-8",
    )
    (day / "status.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "candidate_count": 1,
                "llm_success_count": 1,
                "llm_error_count": 0,
                "steps": [{"id": "screen", "label": "Screen universe", "status": "ok"}],
            }
        ),
        encoding="utf-8",
    )
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
    assert "data-value-discover-form" in html
    assert "data-value-discover-button" in html
    assert "Run Stock Analysis" in html
    assert "Analyze Stock" in html
    assert "NVIDIA NIM" in html
    assert "google/gemma-4-31b-it" in html
    assert "OpenRouter" in html
    assert "nvidia/nemotron-3-super-120b-a12b" in html
    assert "openai/gpt-oss-20b" in html
    assert "openai/gpt-oss-120b" in html
    assert "openrouter/free" in html
    assert "data-analysis-form" in html
    assert "Value Discover Results: 2026-06-07" in html
    assert "Deep value setup" in html
    assert "LLM OK" in html
    assert "PE Routes" in html
    assert "Open shortlist" in html


def test_value_discover_lock_prevents_duplicate_start(monkeypatch, tmp_path):
    lock_path = tmp_path / "value_discover.lock"
    lock_path.write_text('{"pid": 12345}', encoding="utf-8")
    monkeypatch.setattr(web, "VALUE_DISCOVER_LOCK", lock_path)
    monkeypatch.setattr(web, "_pid_is_running", lambda pid: True)

    assert web._start_value_discover_job() is False


def test_value_discover_control_disables_button_when_running():
    html = render_value_discover_control(running=True, already_running=True)

    assert "Value Discover Running..." in html
    assert "data-value-discover-button disabled" in html
    assert "Duplicate starts are blocked" in html
    assert "data-auto-refresh" in html
