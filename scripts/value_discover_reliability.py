#!/usr/bin/env python3
"""Reproducible, non-trading reliability harness for Value Discover.

The harness uses deterministic fixtures plus a deliberately small set of
public, read-only Yahoo Finance samples.  It never initializes an LLM client,
reads credential values, or talks to a brokerage/wallet.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

import pandas as pd
import yfinance as yf

import tradingagents.dataflows.config as dataflow_config
from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating, render_pm_decision
from tradingagents.agents.utils.structured import invoke_structured_or_freetext
from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages
from tradingagents.dataflows.yfinance_news import get_global_news_yfinance, get_news_yfinance
from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV, get_api_key_env
from tradingagents.public_equity import build_idea_generation_payload
from tradingagents.value_discover import (
    VALUE_DISCOVER_DEFAULT_LLM_MODEL,
    VALUE_DISCOVER_DEFAULT_LLM_PROVIDER,
    ValueCandidate,
    _screen_symbol,
    run_value_discover,
    value_discover_llm_config,
)


SCHEMA_VERSION = "value_discover_reliability.v1"
DEFAULT_SYMBOLS = ("AAPL", "MSFT")
REQUIRED_CANDIDATE_FIELDS = tuple(ValueCandidate.__dataclass_fields__)
SENSITIVE_ENV_NAMES = tuple(
    sorted({name for name in PROVIDER_API_KEY_ENV.values() if name} | {"ALPHA_VANTAGE_API_KEY"})
)


class FixtureTicker:
    DATA = {
        "NORMAL": {
            "shortName": "Fixture Normal",
            "sector": "Industrials",
            "currentPrice": 50.0,
            "marketCap": 50_000_000_000,
            "forwardPE": 8.0,
            "trailingPE": 10.0,
            "priceToBook": 1.2,
            "enterpriseToEbitda": 7.0,
            "profitMargins": 0.18,
            "returnOnEquity": 0.19,
            "debtToEquity": 60.0,
            "targetMeanPrice": 65.0,
        },
        "ANOMALOUS": {
            "shortName": "Fixture Anomalous",
            "sector": "Technology",
            "currentPrice": 10.0,
            "marketCap": 500.0,
            "forwardPE": 7.0,
            "trailingPE": 9.0,
            "priceToBook": 1.0,
            "enterpriseToEbitda": 6.0,
            "profitMargins": 0.20,
            "returnOnEquity": 0.22,
            "debtToEquity": 40.0,
            "targetMeanPrice": 1_000.0,
        },
        "MISSING": {
            "shortName": "Fixture Missing Fields",
            "sector": "Unknown",
            "currentPrice": 25.0,
            "forwardPE": 12.0,
            "profitMargins": 0.10,
        },
    }

    def __init__(self, symbol: str):
        self.info = dict(self.DATA[symbol])

    def history(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        close = pd.Series([45.0 + index * 0.2 for index in range(40)], index=dates)
        return pd.DataFrame({"Close": close, "Volume": [2_000_000] * 40}, index=dates)


def stable_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def detect_candidate_anomalies(candidate: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    symbol = str(candidate.get("symbol") or "?")
    price = _finite_float(candidate.get("price"))
    market_cap = _finite_float(candidate.get("market_cap"))
    upside = _finite_float(candidate.get("target_upside_pct"))

    if price is None or price <= 0:
        findings.append(_anomaly(symbol, "invalid_price", "critical", "Price is missing, non-finite, or non-positive."))
    if market_cap is None:
        findings.append(_anomaly(symbol, "missing_market_cap", "warning", "Market capitalization is missing."))
    elif market_cap <= 0:
        findings.append(_anomaly(symbol, "invalid_market_cap", "critical", "Market capitalization is non-positive."))
    if price and market_cap and market_cap > 0:
        implied_shares = market_cap / price
        if implied_shares < 10_000 or implied_shares > 100_000_000_000:
            findings.append(
                _anomaly(
                    symbol,
                    "price_market_cap_inconsistency",
                    "critical",
                    f"Market cap / price implies {implied_shares:,.0f} shares, outside the broad sanity band.",
                )
            )
    if upside is not None and (upside < -0.95 or upside > 0.75):
        findings.append(
            _anomaly(
                symbol,
                "extreme_target_upside",
                "warning",
                f"Analyst target implies {upside * 100:.1f}% upside/downside; require independent confirmation.",
            )
        )
    return findings


def _anomaly(symbol: str, code: str, severity: str, detail: str) -> dict[str, str]:
    return {"symbol": symbol, "code": code, "severity": severity, "detail": detail}


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def run_fixture_checks() -> dict[str, Any]:
    runs: list[list[dict[str, Any]]] = []
    artifact_checks: list[dict[str, Any]] = []
    for repeat in range(2):
        with tempfile.TemporaryDirectory(prefix="value-discover-fixture-") as tmp:
            candidates, markdown_path, csv_path = run_value_discover(
                universe=("NORMAL", "ANOMALOUS", "MISSING"),
                limit=3,
                output_dir=Path(tmp),
                as_of=dt.datetime(2026, 8, 2, 7, 20, repeat),
                ticker_factory=FixtureTicker,
            )
            rows = [asdict(candidate) for candidate in candidates]
            runs.append(rows)
            with csv_path.open(encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            payload = build_idea_generation_payload(
                candidates,
                as_of=dt.datetime(2026, 8, 2, 7, 20, repeat),
                markdown_path=markdown_path,
                csv_path=csv_path,
            )
            artifact_checks.append(
                {
                    "markdown_exists": markdown_path.exists(),
                    "csv_exists": csv_path.exists(),
                    "csv_row_count": len(csv_rows),
                    "csv_schema_valid": bool(csv_rows) and set(REQUIRED_CANDIDATE_FIELDS) == set(csv_rows[0]),
                    "public_equity_schema_valid": _valid_public_equity_payload(payload),
                }
            )

    normalized_runs = [[{**row, "symbol": row["symbol"]} for row in rows] for rows in runs]
    digests = [stable_digest(rows) for rows in normalized_runs]
    anomalies = [finding for row in runs[0] for finding in detect_candidate_anomalies(row)]
    missing_fields = {
        row["symbol"]: sorted(key for key, value in row.items() if value is None)
        for row in runs[0]
    }
    return {
        "repeats": 2,
        "candidate_count_each_run": [len(run) for run in runs],
        "digests": digests,
        "cross_run_consistent": len(set(digests)) == 1,
        "schema_valid": all(set(row) == set(REQUIRED_CANDIDATE_FIELDS) for run in runs for row in run),
        "missing_fields": missing_fields,
        "anomalies": anomalies,
        "artifact_checks": artifact_checks,
    }


def _valid_public_equity_payload(payload: dict[str, Any]) -> bool:
    return (
        payload.get("kind") == "public_equity_investing_dashboard.v1"
        and payload.get("mode") == "idea_generation"
        and isinstance(payload.get("tabs"), list)
        and payload.get("qa", {}).get("final_recommendation") is False
    )


def run_fallback_check() -> dict[str, Any]:
    original_methods = interface.VENDOR_METHODS["get_stock_data"]
    original_config = get_config()
    calls: list[str] = []

    def primary(*args: Any, **kwargs: Any) -> str:
        calls.append("alpha_vantage")
        raise AlphaVantageRateLimitError("fixture rate limit")

    def fallback(*args: Any, **kwargs: Any) -> str:
        calls.append("yfinance")
        return "fixture fallback ok"

    try:
        interface.VENDOR_METHODS["get_stock_data"] = {
            "alpha_vantage": primary,
            "yfinance": fallback,
        }
        set_config({"tool_vendors": {"get_stock_data": "alpha_vantage"}})
        result = interface.route_to_vendor("get_stock_data", "NORMAL", "2026-01-01", "2026-02-01")
    finally:
        interface.VENDOR_METHODS["get_stock_data"] = original_methods
        dataflow_config._config = original_config
    return {
        "configured_primary": "alpha_vantage",
        "calls": calls,
        "result": result,
        "fallback_on_alpha_vantage_rate_limit_verified": calls == ["alpha_vantage", "yfinance"],
        "does_not_cover_yfinance_primary_failures": True,
    }


def run_parser_fallback_check() -> dict[str, Any]:
    class Structured:
        def invoke(self, prompt: Any) -> Any:
            raise ValueError("fixture malformed structured response")

    class Plain:
        def invoke(self, prompt: Any) -> Any:
            return SimpleNamespace(content="fixture free-text fallback")

    fallback = invoke_structured_or_freetext(
        Structured(), Plain(), "fixture prompt", render_pm_decision, "Fixture PM"
    )
    parsed = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="Fixture summary",
        investment_thesis="Fixture thesis",
    )
    rendered = render_pm_decision(parsed)
    return {
        "pydantic_schema_valid": parsed.rating == PortfolioRating.HOLD,
        "rendered_rating_present": "**Rating**: Hold" in rendered,
        "structured_failure_falls_back_once": fallback == "fixture free-text fallback",
        "warning": "Fallback free text is not revalidated against the Pydantic schema.",
    }


def _sleep_worker(queue: Any, seconds: float) -> None:
    time.sleep(seconds)
    queue.put("completed")


def run_timeout_check(timeout_seconds: float = 0.1) -> dict[str, Any]:
    ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_sleep_worker, args=(queue, timeout_seconds * 5))
    started = time.monotonic()
    process.start()
    process.join(timeout_seconds)
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        process.join(2)
    return {
        "configured_seconds": timeout_seconds,
        "timed_out": timed_out,
        "terminated": not process.is_alive(),
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
    }


def _public_sample_worker(queue: Any, symbol: str) -> None:
    started = time.monotonic()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        history = ticker.history(period="1y", auto_adjust=False)

        class CachedTicker:
            def __init__(self, _symbol: str):
                self.info = info

            def history(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
                return history

        candidate = _screen_symbol(symbol, ticker_factory=CachedTicker)
        row = asdict(candidate) if candidate else None
        queue.put(
            {
                "symbol": symbol,
                "status": "ok" if candidate else "filtered_or_invalid",
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "info_present": bool(info),
                "history_rows": len(history),
                "candidate": row,
                "candidate_schema_valid": row is not None and set(row) == set(REQUIRED_CANDIDATE_FIELDS),
                "missing_fields": sorted(key for key, value in (row or {}).items() if value is None),
                "anomalies": detect_candidate_anomalies(row or {"symbol": symbol}),
                "provenance": ["yfinance.Ticker.info", "yfinance.Ticker.history(period=1y, auto_adjust=False)"],
            }
        )
    except Exception as exc:
        queue.put(
            {
                "symbol": symbol,
                "status": "error",
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
                "provenance": ["yfinance.Ticker.info", "yfinance.Ticker.history(period=1y, auto_adjust=False)"],
            }
        )


def run_public_sample(symbol: str, timeout_seconds: float) -> dict[str, Any]:
    ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_public_sample_worker, args=(queue, symbol))
    started = time.monotonic()
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(2)
        return {
            "symbol": symbol,
            "status": "timeout",
            "timeout_seconds": timeout_seconds,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "provenance": ["yfinance.Ticker.info", "yfinance.Ticker.history(period=1y, auto_adjust=False)"],
        }
    if queue.empty():
        return {
            "symbol": symbol,
            "status": "error",
            "error": f"worker exited without evidence (exit code {process.exitcode})",
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
        }
    return queue.get()


def run_public_checks(symbols: Sequence[str], timeout_seconds: float) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for repeat in range(2):
        for symbol in symbols:
            result = run_public_sample(symbol, timeout_seconds)
            result["repeat"] = repeat + 1
            runs.append(result)

    consistency: dict[str, Any] = {}
    for symbol in symbols:
        samples = [row for row in runs if row["symbol"] == symbol]
        candidate_digests = [stable_digest(row.get("candidate")) for row in samples if row.get("candidate")]
        consistency[symbol] = {
            "statuses": [row["status"] for row in samples],
            "candidate_digests": candidate_digests,
            "consistent": len(candidate_digests) == 2 and len(set(candidate_digests)) == 1,
        }
    return {
        "symbols": list(symbols),
        "repeats": 2,
        "timeout_seconds_per_sample": timeout_seconds,
        "runs": runs,
        "success_count": sum(row["status"] == "ok" for row in runs),
        "failure_count": sum(row["status"] not in {"ok", "filtered_or_invalid"} for row in runs),
        "timeout_count": sum(row["status"] == "timeout" for row in runs),
        "consistency": consistency,
    }


def _run_public_adapter(source_id: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if source_id == "stocktwits":
            text = fetch_stocktwits_messages("AAPL", limit=5, timeout=8.0)
            provenance = "StockTwits public symbol stream"
        elif source_id == "reddit":
            text = fetch_reddit_posts(
                "AAPL",
                subreddits=("stocks",),
                limit_per_sub=2,
                timeout=8.0,
                inter_request_delay=0,
            )
            provenance = "Reddit public JSON search: r/stocks"
        elif source_id == "ticker_news":
            text = get_news_yfinance("AAPL", "2026-07-26", "2026-08-02")
            provenance = "yfinance Ticker.get_news"
        elif source_id == "global_news":
            set_config(
                {
                    "global_news_queries": ["Federal Reserve interest rates inflation"],
                    "global_news_article_limit": 2,
                    "global_news_lookback_days": 7,
                }
            )
            text = get_global_news_yfinance("2026-08-02", look_back_days=7, limit=2)
            provenance = "yfinance Search (one bounded macro query)"
        else:
            raise ValueError(f"unknown public adapter: {source_id}")
        lower = text.strip().lower()
        if "unavailable:" in lower or lower.startswith("error fetching"):
            status = "degraded"
        elif "no news found" in lower or "<no " in lower:
            status = "empty"
        else:
            status = "ok"
        return {
            "source": source_id,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "output_bytes": len(text.encode("utf-8")),
            "output_digest": stable_digest(text),
            "provenance": provenance,
            "typed_status_available": False,
        }
    except Exception as exc:
        return {
            "source": source_id,
            "status": "error",
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }


def run_public_adapter_sample(source_id: str, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--adapter-worker", source_id],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "source": source_id,
            "status": "timeout",
            "timeout_seconds": timeout_seconds,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
        }
    if result.returncode != 0:
        return {
            "source": source_id,
            "status": "error",
            "error": f"worker exited without evidence (exit code {result.returncode})",
            "stderr": result.stderr[-300:],
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "source": source_id,
            "status": "error",
            "error": "worker returned invalid JSON evidence",
            "stdout": result.stdout[-300:],
        }


def run_public_adapter_checks(timeout_seconds: float) -> dict[str, Any]:
    source_ids = ("stocktwits", "reddit", "ticker_news", "global_news")
    runs: list[dict[str, Any]] = []
    for repeat in range(2):
        for source_id in source_ids:
            result = run_public_adapter_sample(source_id, timeout_seconds)
            result["repeat"] = repeat + 1
            runs.append(result)
    consistency: dict[str, Any] = {}
    for source_id in source_ids:
        samples = [run for run in runs if run["source"] == source_id]
        digests = [run["output_digest"] for run in samples if run.get("output_digest")]
        consistency[source_id] = {
            "statuses": [run["status"] for run in samples],
            "output_digests": digests,
            "consistent": len(digests) == 2 and len(set(digests)) == 1,
            "note": "News/social content may legitimately change between calls; a mismatch is drift evidence, not automatically a defect.",
        }
    return {
        "sources": list(source_ids),
        "repeats": 2,
        "timeout_seconds_per_sample": timeout_seconds,
        "runs": runs,
        "success_count": sum(run["status"] == "ok" for run in runs),
        "degraded_count": sum(run["status"] in {"degraded", "empty"} for run in runs),
        "failure_count": sum(run["status"] in {"error", "timeout"} for run in runs),
        "consistency": consistency,
    }


def inspect_prior_runs(report_root: Path) -> dict[str, Any]:
    csv_paths = sorted(report_root.glob("*/*value_discover_*.csv"))
    runs: list[dict[str, Any]] = []
    for path in csv_paths:
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            runs.append({"path": str(path), "status": "error", "error_type": type(exc).__name__})
            continue
        anomalies = [finding for row in rows for finding in detect_candidate_anomalies(row)]
        runs.append(
            {
                "path": str(path),
                "status": "parsed",
                "candidate_count": len(rows),
                "symbols": [row.get("symbol") for row in rows],
                "schema_valid": not rows or set(REQUIRED_CANDIDATE_FIELDS).issubset(rows[0]),
                "anomalies": anomalies,
            }
        )
    counts = [run["candidate_count"] for run in runs if "candidate_count" in run]
    return {
        "run_count": len(runs),
        "runs": runs,
        "candidate_counts": counts,
        "coverage_collapse_detected": bool(counts) and min(counts) < max(counts) * 0.5,
        "warning": "The scanner catches every per-symbol exception and records neither attempted-symbol coverage nor error cause.",
    }


def build_inventory(config: dict[str, Any], llm_enabled: bool, credential_present: bool) -> list[dict[str, Any]]:
    llm_provider = str(config["llm_provider"])
    llm_model = str(config["quick_think_llm"])
    common_uninvoked = "configured for the LLM-enabled graph; not invoked by this harness or the LLM-disabled baseline"
    return [
        _component("yfinance_info", "screen", "data_source", "yfinance.Ticker.info", True, "invoked by baseline and bounded live harness", "none at call site", "exception -> symbol silently dropped", "dict; field-by-field float coercion", "valuation/quality scoring", "conditionally reliable", "screening support only", "Silent symbol loss and no source timestamp.", "Record per-symbol outcome, source timestamp, retries, and error class."),
        _component("yfinance_history", "screen", "data_source", "yfinance.Ticker.history(period=1y, auto_adjust=False)", True, "invoked by baseline and bounded live harness", "harness: hard process timeout; production: none", "exception -> symbol silently dropped", "pandas DataFrame with Close/Volume", "RSI-14 and 20-day mean volume", "conditionally reliable", "screening support only", "Production can hang or collapse coverage without a failed run.", "Add per-symbol timeout and minimum universe-coverage gate."),
        _component("score_model", "screen", "deterministic_model", "_score: valuation + margin + ROE + leverage + target + liquidity + market-cap + RSI", True, "invoked by fixtures and baseline", "not applicable", "missing factors contribute no points or caveats inconsistently", "ValueCandidate dataclass", "descending score; top-N", "verified reliable", "ranking within the exact supplied snapshot", "Heuristic weights are not calibrated or backtested evidence.", "Version weights and evaluate precision/turnover against a frozen benchmark."),
        _component("target_upside", "screen", "deterministic_model", "(targetMeanPrice-currentPrice)/currentPrice", True, "invoked by fixtures and baseline", "not applicable", "None on invalid target/price", "finite float expected", "score bonus and workflow routing", "conditionally reliable", "triage signal after secondary confirmation", "Extreme analyst targets can materially inflate score.", "Cap/winsorize score contribution and require target age/source/analyst count."),
        _component("rsi_volume", "screen", "deterministic_model", "RSI-14 plus 20-day mean volume derived from one-year OHLCV", True, "invoked by fixtures and bounded live harness", "not applicable", "missing/short history yields None or symbol drop", "finite floats expected", "score bonus and liquidity context", "conditionally reliable", "screening support only", "Short or anomalous histories can silently remove or distort the signal.", "Record observation count/as-of date and reject non-finite or insufficient-history inputs."),
        _component("public_equity_router", "post_processing", "deterministic_model", "Public Equity bucket/workflow rules", True, "invoked by fixtures and baseline", "not applicable", "none", "dashboard.v1 JSON", "Markdown + workflow routes", "verified reliable", "research routing only", "Routes inherit upstream data quality.", "Carry source-quality flags and anomaly gates into each route."),
        _component("yfinance_ohlcv_tools", "llm_graph", "data_source", "get_stock_data + stockstats indicator cache", True, common_uninvoked, "yf_retry only on rate limit; no hard request timeout", "Alpha Vantage fallback only if Alpha Vantage is primary and rate-limited", "CSV text and indicator text", "market analyst prompt", "untested", "disabled until live graph test", "Tool-call choice and data shape were not exercised.", "Fixture tool calls, then one authorized bounded live graph call."),
        _component("yfinance_fundamentals", "llm_graph", "data_source", "info, balance sheet, cash flow, income statement", True, common_uninvoked, "no hard request timeout", "errors returned as strings; no vendor fallback", "free-text/CSV blocks", "fundamentals analyst prompt", "untested", "disabled until live graph test", "Error strings can be treated as substantive data by the LLM.", "Return typed source envelopes with status, as-of, and missing fields."),
        _component("yfinance_news", "llm_graph", "data_source", "Ticker.get_news and yfinance.Search global queries", True, common_uninvoked, "yf_retry on rate limit only", "errors returned as strings", "formatted Markdown text", "news + sentiment prompts", "untested", "disabled until live graph test", "No explicit provenance schema or completeness threshold.", "Emit typed article records, dates, URLs, and query coverage."),
        _component("yfinance_insiders", "llm_graph", "data_source", "Ticker.insider_transactions", True, "registered in tools_news but not bound by the current news analyst, so not actually reachable", "none", "errors returned as strings", "CSV text", "none in current default path", "unreliable", "do not rely on it", "Configured tool-node membership overstates actual invocation capability.", "Either bind it in the analyst tools or remove it from the advertised node."),
        _component("stocktwits", "llm_graph", "data_source", "public StockTwits symbol stream", True, common_uninvoked, "10 seconds", "placeholder string on HTTP/URL/JSON/timeout failure", "formatted text with sentiment counts", "sentiment analyst prompt", "untested", "disabled until live graph test", "Placeholder is not a typed failure and can be summarized as evidence.", "Return a typed envelope and enforce minimum message count/freshness."),
        _component("reddit", "llm_graph", "data_source", "public JSON search for wallstreetbets, stocks, investing", True, common_uninvoked, "10 seconds per subreddit + 0.4 second pacing", "empty list/placeholder on failure", "formatted text with engagement", "sentiment analyst prompt", "untested", "disabled until live graph test", "HTTP failures and true zero results collapse to similar output.", "Preserve per-subreddit HTTP status, error, latency, and result count."),
        _component("alpha_vantage", "vendor_fallback", "data_source", "Alpha Vantage APIs", True, "fallback control flow verified with fixtures; live API not invoked", "library request behavior; no Value Discover hard timeout", "only AlphaVantageRateLimitError advances to next vendor", "vendor-specific JSON/CSV converted to strings", "same analyst tools", "unreliable", "do not rely on as yfinance failover", "With yfinance primary, yfinance exceptions/error strings do not trigger Alpha Vantage.", "Define typed retryable failures for every vendor and symmetric fallback."),
        _component("openrouter_gemma4", "llm_graph", "llm", f"{llm_provider}/{llm_model}", True, "not invoked: LLM disabled and configured credential absent" if not credential_present else "not invoked: baseline LLM disabled; credential present but unused", "180-second hard process timeout per ticker", "OpenRouter provider routing; no application model fallback", "analyst free text + three Pydantic decision schemas", "full multi-agent graph", "untested", "keep disabled", "Official model availability is not application-level reliability evidence.", "After authorization, run a one-ticker bounded canary with callback/tool/schema evidence."),
        _component("structured_decisions", "parser", "parser_schema", "ResearchPlan, TraderProposal, PortfolioDecision Pydantic schemas", True, "fixture schema + fallback path invoked; live LLM not invoked", "inherits LLM timeout", "one retry as unvalidated free text", "Pydantic then Markdown", "rating parser + saved reports", "conditionally reliable", "typed path is usable; fallback output requires review", "Free-text fallback bypasses schema validity guarantees.", "Mark fallback explicitly and reject/hold when required fields are absent."),
        _component("llm_timeout", "timeout", "control", "per-ticker child process join/terminate/kill", True, "deterministic timeout harness invoked; live LLM not invoked", "default 180 seconds", "writes per-ticker error Markdown and continues", "LLMAnalysisResult", "summary counts", "conditionally reliable", "fault containment only", "Timeout does not identify which provider/tool stage hung.", "Add stage-level monotonic timings and provider/tool callback events."),
        _component("status_index", "post_processing", "reporting", "status.json + reports/index.json", True, "baseline invoked; fixture schema checked", "none", "top-level exception -> error status", "JSON", "latest-run dashboard index", "unreliable", "do not treat status=ok as complete reliability proof", "LLM-disabled runs label the LLM step ok rather than skipped; silent screen losses remain ok.", "Record invoked/skipped, attempted/succeeded/failed symbols, and enforce coverage gates."),
    ]


def _component(
    component_id: str,
    stage: str,
    component_type: str,
    implementation: str,
    configured: bool,
    invoked_evidence: str,
    timeout: str,
    fallback: str,
    parser_schema: str,
    post_processing: str,
    classification: str,
    allowed_use: str,
    warning: str,
    remediation: str,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "stage": stage,
        "type": component_type,
        "implementation": implementation,
        "configured": configured,
        "invoked_evidence": invoked_evidence,
        "timeout": timeout,
        "fallback": fallback,
        "parser_schema": parser_schema,
        "post_processing": post_processing,
        "classification": classification,
        "allowed_use": allowed_use,
        "warning": warning,
        "remediation": remediation,
    }


def _credential_presence() -> dict[str, bool]:
    return {name: bool(os.environ.get(name)) for name in SENSITIVE_ENV_NAMES}


def _git_commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True)
    return result.stdout.strip()


def build_evidence(symbols: Sequence[str], timeout_seconds: float, skip_live: bool) -> dict[str, Any]:
    config = value_discover_llm_config()
    llm_enabled = os.environ.get("TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    credential_env = get_api_key_env(str(config["llm_provider"]))
    credentials = _credential_presence()
    required_present = bool(credential_env and credentials.get(credential_env))
    public_checks = {"skipped": True, "reason": "--skip-live"} if skip_live else run_public_checks(symbols, timeout_seconds)
    public_adapter_checks = (
        {"skipped": True, "reason": "--skip-live"}
        if skip_live
        else run_public_adapter_checks(min(timeout_seconds, 15.0))
    )
    fixture_checks = run_fixture_checks()
    fallback_check = run_fallback_check()
    parser_check = run_parser_fallback_check()
    timeout_check = run_timeout_check()
    prior_runs = inspect_prior_runs(Path("reports/value_discover"))
    inventory = build_inventory(config, llm_enabled, required_present)
    classification_counts = dict(collections.Counter(row["classification"] for row in inventory))
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": "research-support reliability only; no trading, account, wallet, or paid/model API action",
        "git_commit": _git_commit(),
        "configuration": {
            "llm_enabled": llm_enabled,
            "llm_provider": config["llm_provider"],
            "quick_model": config["quick_think_llm"],
            "deep_model": config["deep_think_llm"],
            "per_ticker_llm_timeout_seconds": int(os.environ.get("TRADINGAGENTS_VALUE_DISCOVER_LLM_TIMEOUT_SECONDS", "180")),
            "data_vendors": config.get("data_vendors"),
        },
        "credential_presence": credentials,
        "configured_provider_credential_env": credential_env,
        "configured_provider_credential_present": required_present,
        "supported_provider_catalog": [
            {
                "provider": provider,
                "credential_env": env_name,
                "configured_for_value_discover": provider == config["llm_provider"],
                "live_reliability": "untested",
            }
            for provider, env_name in PROVIDER_API_KEY_ENV.items()
        ],
        "configured_model_fallbacks": [],
        "llm_reliability": "untested",
        "llm_reliability_reason": "Baseline LLM is disabled and no paid/model API was invoked.",
        "fixture_checks": fixture_checks,
        "fallback_check": fallback_check,
        "parser_check": parser_check,
        "timeout_check": timeout_check,
        "public_checks": public_checks,
        "public_adapter_checks": public_adapter_checks,
        "prior_run_checks": prior_runs,
        "inventory": inventory,
        "classification_counts": classification_counts,
        "source_documentation": [
            {
                "url": "https://ranaroussi.github.io/yfinance/",
                "finding": "yfinance describes itself as an unaffiliated research/education tool over Yahoo public APIs and points users to Yahoo terms/personal-use limits.",
            },
            {
                "url": "https://openrouter.ai/docs/api/reference/overview",
                "finding": "OpenRouter normalizes request/response schemas across routed providers; supported structured output is model-dependent.",
            },
            {
                "url": "https://openrouter.ai/google/gemma-4-26b-a4b-it",
                "finding": "The configured model is currently listed with function calling and structured output support, but this does not verify the local application path.",
            },
        ],
        "credential_question": None,
        "embedded_llm_policy": (
            "Disabled by the current authorized Codex trial; credential absence is not a blocker "
            "and no embedded model canary should be requested or run."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--live-timeout", type=float, default=25.0)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--adapter-worker", choices=("stocktwits", "reddit", "ticker_news", "global_news"))
    args = parser.parse_args()
    if args.adapter_worker:
        print(json.dumps(_run_public_adapter(args.adapter_worker), sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required unless --adapter-worker is used")
    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    evidence = build_evidence(symbols, args.live_timeout, args.skip_live)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "output": str(args.output),
        "fixture_consistent": evidence["fixture_checks"]["cross_run_consistent"],
        "public_success_count": evidence["public_checks"].get("success_count", 0),
        "public_failure_count": evidence["public_checks"].get("failure_count", 0),
        "public_adapter_failure_count": evidence["public_adapter_checks"].get("failure_count", 0),
        "coverage_collapse_detected": evidence["prior_run_checks"]["coverage_collapse_detected"],
        "configured_provider_credential_present": evidence["configured_provider_credential_present"],
        "llm_reliability": evidence["llm_reliability"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
