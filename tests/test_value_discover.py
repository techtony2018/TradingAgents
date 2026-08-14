from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from tradingagents.value_discover import (
    PACIFIC_TZ,
    ValueCandidate,
    ValueSelectionPolicy,
    cron_entry,
    render_markdown,
    run_llm_analysis_for_candidates,
    run_value_discover,
    read_recent_value_discover_symbols,
    select_value_candidates,
    value_discover_llm_config,
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


def _candidate_for_selection(
    symbol: str,
    *,
    score: float,
    sector: str = "Industrials",
) -> ValueCandidate:
    return ValueCandidate(
        symbol=symbol,
        company=f"{symbol} Co",
        sector=sector,
        price=50.0,
        market_cap=50_000_000_000,
        trailing_pe=10.0,
        forward_pe=8.0,
        price_to_book=1.2,
        ev_to_ebitda=7.0,
        profit_margin=0.18,
        return_on_equity=0.19,
        debt_to_equity=60.0,
        target_upside_pct=0.30,
        rsi_14=48.0,
        avg_volume_20d=2_000_000.0,
        score=score,
        thesis="Low valuation",
        caveats="Target estimate needs corroboration",
    )


def test_value_selector_surfaces_nearby_fresh_alternatives_after_recent_repeats():
    candidates = [
        _candidate_for_selection("REPEAT1", score=80),
        _candidate_for_selection("REPEAT2", score=79),
        _candidate_for_selection("REPEAT3", score=78),
        _candidate_for_selection("REPEAT4", score=77),
        _candidate_for_selection("REPEAT5", score=76),
        _candidate_for_selection("FRESH1", score=75),
        _candidate_for_selection("FRESH2", score=74),
    ]

    selected = select_value_candidates(
        candidates,
        limit=5,
        policy=ValueSelectionPolicy(
            recent_lookback_days=5,
            repeat_penalty=3.0,
            max_per_sector=5,
        ),
        recent_symbol_counts={
            "REPEAT1": 2,
            "REPEAT2": 2,
            "REPEAT3": 2,
            "REPEAT4": 2,
            "REPEAT5": 2,
        },
    )

    symbols = [candidate.symbol for candidate in selected]
    assert "FRESH1" in symbols
    assert "FRESH2" in symbols
    assert symbols != ["REPEAT1", "REPEAT2", "REPEAT3", "REPEAT4", "REPEAT5"]


def test_value_selector_keeps_materially_superior_repeated_candidate():
    candidates = [
        _candidate_for_selection("DOMINANT", score=92),
        _candidate_for_selection("FRESH1", score=75),
        _candidate_for_selection("FRESH2", score=74),
    ]

    selected = select_value_candidates(
        candidates,
        limit=2,
        policy=ValueSelectionPolicy(
            recent_lookback_days=5,
            repeat_penalty=3.0,
            max_per_sector=5,
        ),
        recent_symbol_counts={"DOMINANT": 2},
    )

    assert [candidate.symbol for candidate in selected] == ["DOMINANT", "FRESH1"]


def test_value_selector_limits_sector_concentration_when_alternatives_exist():
    candidates = [
        _candidate_for_selection("TECH1", score=80, sector="Technology"),
        _candidate_for_selection("TECH2", score=79, sector="Technology"),
        _candidate_for_selection("TECH3", score=78, sector="Technology"),
        _candidate_for_selection("HEALTH1", score=70, sector="Healthcare"),
    ]

    selected = select_value_candidates(
        candidates,
        limit=3,
        policy=ValueSelectionPolicy(
            recent_lookback_days=0,
            repeat_penalty=0.0,
            max_per_sector=2,
        ),
        recent_symbol_counts={},
    )

    assert [candidate.symbol for candidate in selected] == ["TECH1", "TECH2", "HEALTH1"]


def test_recent_symbol_reader_counts_once_per_prior_day_not_retry_file(tmp_path):
    prior_day = tmp_path / "2026-08-12"
    prior_day.mkdir()
    header = "symbol,company,score\n"
    for index in range(2):
        (prior_day / f"value_discover_retry_{index}.csv").write_text(
            header + "REPEAT,Repeat Co,75\nFRESH,Fresh Co,70\n",
            encoding="utf-8",
        )

    counts = read_recent_value_discover_symbols(
        tmp_path,
        dt.date(2026, 8, 13),
        lookback_days=5,
    )

    assert counts == {"FRESH": 1, "REPEAT": 1}


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


def test_value_discover_defaults_to_openrouter_gemma(monkeypatch):
    for key in (
        "TRADINGAGENTS_VALUE_DISCOVER_LLM_PROVIDER",
        "TRADINGAGENTS_VALUE_DISCOVER_QUICK_THINK_LLM",
        "TRADINGAGENTS_VALUE_DISCOVER_DEEP_THINK_LLM",
        "TRADINGAGENTS_VALUE_DISCOVER_LLM_BACKEND_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    config = value_discover_llm_config()

    assert config["llm_provider"] == "openrouter"
    assert config["quick_think_llm"] == "google/gemma-4-26b-a4b-it"
    assert config["deep_think_llm"] == "google/gemma-4-26b-a4b-it"
    assert config["backend_url"] is None


def test_cli_analysis_mode_defaults_to_codex_and_keeps_embedded_explicit():
    from cli import main as cli_main

    resolve = getattr(cli_main, "_resolve_value_discover_analysis_mode", None)
    assert callable(resolve)
    assert resolve(None, None, {}) == "codex"
    assert resolve("embedded", None, {}) == "embedded"
    assert resolve(None, True, {}) == "embedded"
    assert resolve(None, False, {}) == "disabled"


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
