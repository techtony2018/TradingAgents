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
    assert "Latest Run: 2026-06-07" in html
    assert "Open shortlist" in html
