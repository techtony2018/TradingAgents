from __future__ import annotations

from tradingagents.stock_analysis import run_stock_analysis


def test_stock_analysis_runner_writes_report_with_fake_graph(monkeypatch, tmp_path):
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
    )

    assert calls == [("CHEAP", "2026-06-01", "stock")]
    assert result["status"] == "ok"
    assert result["decision"] == "Hold"
    assert "complete_report.md" in result["report_path"]
