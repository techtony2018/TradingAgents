from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from tradingagents.value_discover import (
    PACIFIC_TZ,
    cron_entry,
    render_markdown,
    run_llm_analysis_for_candidates,
    run_value_discover,
)


class FakeTicker:
    DATA = {
        "CHEAP": {
            "shortName": "Cheap Co",
            "sector": "Industrials",
            "currentPrice": 50,
            "marketCap": 50_000_000_000,
            "forwardPE": 8,
            "trailingPE": 10,
            "priceToBook": 1.2,
            "enterpriseToEbitda": 7,
            "profitMargins": 0.18,
            "returnOnEquity": 0.19,
            "debtToEquity": 60,
            "targetMeanPrice": 65,
        },
        "PRICEY": {
            "shortName": "Pricey Co",
            "sector": "Technology",
            "currentPrice": 100,
            "marketCap": 70_000_000_000,
            "forwardPE": 38,
            "trailingPE": 45,
            "priceToBook": 12,
            "enterpriseToEbitda": 30,
            "profitMargins": 0.08,
            "returnOnEquity": 0.10,
            "debtToEquity": 80,
            "targetMeanPrice": 95,
        },
    }

    def __init__(self, symbol: str):
        self.info = self.DATA[symbol]

    def history(self, *args, **kwargs):
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        close = pd.Series([45 + index * 0.2 for index in range(40)], index=dates)
        return pd.DataFrame(
            {
                "Close": close,
                "Volume": [2_000_000] * 40,
            },
            index=dates,
        )


def test_value_discover_ranks_and_writes_artifacts(tmp_path):
    candidates, markdown_path, csv_path = run_value_discover(
        universe=("PRICEY", "CHEAP"),
        limit=1,
        output_dir=tmp_path,
        as_of=dt.datetime(2026, 6, 1, 6, 45, 0),
        ticker_factory=FakeTicker,
    )

    assert [candidate.symbol for candidate in candidates] == ["CHEAP"]
    assert markdown_path.exists()
    assert csv_path.exists()
    assert "Value Discover" in markdown_path.read_text(encoding="utf-8")
    assert "CHEAP" in csv_path.read_text(encoding="utf-8")


def test_value_discover_markdown_includes_disclaimer():
    markdown = render_markdown([], as_of=dt.datetime(2026, 6, 1, 6, 45, 0))
    assert "not financial advice" in markdown
    assert "not an order recommendation" in markdown


def test_cron_entry_uses_pacific_720_schedule(tmp_path):
    entry = cron_entry(tmp_path, tmp_path / ".venv/bin/python")
    assert f"CRON_TZ={PACIFIC_TZ}" in entry
    assert "20 7 * * *" in entry
    assert "tradingagents.value_discover" in entry


def test_llm_analysis_receives_discovered_candidates(tmp_path):
    candidates, _, _ = run_value_discover(
        universe=("CHEAP",),
        limit=1,
        output_dir=tmp_path,
        as_of=dt.datetime(2026, 6, 1, 6, 45, 0),
        ticker_factory=FakeTicker,
    )
    calls = []

    class FakeGraph:
        def __init__(self, selected_analysts, debug, config):
            self.selected_analysts = selected_analysts
            self.debug = debug
            self.config = config

        def propagate(self, ticker, analysis_date, asset_type="stock"):
            calls.append((ticker, analysis_date, asset_type))
            return (
                {
                    "market_report": "Market report",
                    "sentiment_report": "Sentiment report",
                    "news_report": "News report",
                    "fundamentals_report": "Fundamentals report",
                    "trader_investment_plan": "Trader plan",
                    "final_trade_decision": "Final decision",
                },
                "Hold",
            )

    results, summary_path = run_llm_analysis_for_candidates(
        candidates,
        analysis_date="2026-06-01",
        output_dir=tmp_path,
        graph_factory=FakeGraph,
        config={"llm_provider": "nvidia"},
    )

    assert calls == [("CHEAP", "2026-06-01", "stock")]
    assert results[0].status == "ok"
    assert results[0].report_path.exists()
    assert "Final decision" in results[0].report_path.read_text(encoding="utf-8")
    assert "CHEAP" in summary_path.read_text(encoding="utf-8")


def test_llm_analysis_records_timeout(tmp_path):
    candidates, _, _ = run_value_discover(
        universe=("CHEAP",),
        limit=1,
        output_dir=tmp_path,
        as_of=dt.datetime(2026, 6, 1, 6, 45, 0),
        ticker_factory=FakeTicker,
    )

    class SlowGraph:
        def __init__(self, selected_analysts, debug, config):
            pass

        def propagate(self, ticker, analysis_date, asset_type="stock"):
            time.sleep(2)
            return ({}, "Hold")

    results, summary_path = run_llm_analysis_for_candidates(
        candidates,
        analysis_date="2026-06-01",
        output_dir=tmp_path,
        graph_factory=SlowGraph,
        per_ticker_timeout_seconds=1,
    )

    assert results[0].status == "error"
    assert "exceeded 1 seconds" in (results[0].error or "")
    assert "exceeded 1 seconds" in summary_path.read_text(encoding="utf-8")
