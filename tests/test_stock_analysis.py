from __future__ import annotations

import json

from tradingagents.stock_analysis import run_stock_analysis


def test_stock_analysis_runner_writes_report_with_fake_graph(monkeypatch, tmp_path):
    calls = []

    class FakeGraph:
        def __init__(self, selected_analysts, debug, config, callbacks=None):
            self.selected_analysts = selected_analysts
            self.debug = debug
            self.config = config
            self.callbacks = callbacks or []

        def propagate(self, ticker, analysis_date, asset_type="stock"):
            calls.append((ticker, analysis_date, asset_type))
            for callback in self.callbacks:
                callback.on_llm_start({}, ["prompt"], invocation_params={"model": self.config["quick_think_llm"]})
                callback.on_llm_end(None)
            return (
                {
                    "market_report": "Market report",
                    "sentiment_report": "Sentiment report",
                    "news_report": "News report",
                    "fundamentals_report": "Fundamentals report",
                    "investment_debate_state": {
                        "bull_history": "Bull",
                        "bear_history": "Bear",
                        "judge_decision": "Research manager",
                    },
                    "trader_investment_plan": "Trader plan",
                    "risk_debate_state": {
                        "aggressive_history": "Aggressive",
                        "conservative_history": "Conservative",
                        "neutral_history": "Neutral",
                        "judge_decision": "Portfolio decision",
                    },
                    "final_trade_decision": "HOLD",
                },
                "Hold",
            )

    monkeypatch.setattr("tradingagents.stock_analysis.TradingAgentsGraph", FakeGraph)

    result = run_stock_analysis(
        "cheap",
        analysis_date="2026-06-01",
        output_dir=tmp_path / "stock_analysis",
        config={
            "llm_provider": "openai",
            "quick_think_llm": "gpt-5.4-mini",
            "deep_think_llm": "gpt-5.4",
        },
    )

    assert calls == [("CHEAP", "2026-06-01", "stock")]
    assert result["status"] == "ok"
    assert result["decision"] == "Hold"
    assert "complete_report.md" in result["report_path"]
    status = json.loads((tmp_path / "stock_analysis" / "CHEAP" / "2026-06-01").glob("*/status.json").__next__().read_text())
    assert [step["status"] for step in status["steps"]] == ["ok", "ok", "ok", "ok", "ok"]
    assert status["llm_provider"] == "openai"
    assert any(event["kind"] == "llm" for event in status["events"])


def test_stock_analysis_runner_uses_unique_run_directories(monkeypatch, tmp_path):
    class FakeGraph:
        def __init__(self, selected_analysts, debug, config, callbacks=None):
            pass

        def propagate(self, ticker, analysis_date, asset_type="stock"):
            return (
                {
                    "market_report": "",
                    "sentiment_report": "",
                    "news_report": "",
                    "fundamentals_report": "",
                    "investment_debate_state": {},
                    "trader_investment_plan": "",
                    "risk_debate_state": {},
                    "final_trade_decision": "HOLD",
                },
                "Hold",
            )

    monkeypatch.setattr("tradingagents.stock_analysis.TradingAgentsGraph", FakeGraph)

    first = run_stock_analysis("NVDA", analysis_date="2026-06-01", output_dir=tmp_path / "stock_analysis")
    second = run_stock_analysis("NVDA", analysis_date="2026-06-01", output_dir=tmp_path / "stock_analysis")

    assert first["run_dir"] != second["run_dir"]
