"""Value Discover: scheduled undervaluation watchlist generation.

The scanner is intentionally transparent and deterministic. It ranks a broad
large-cap universe with simple valuation, quality, and liquidity signals, then
writes a Markdown/CSV watchlist for human review. It does not place trades.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd
import yfinance as yf


PACIFIC_TZ = "America/Los_Angeles"
VALUE_DISCOVER_DEFAULT_LLM_PROVIDER = "openrouter"
VALUE_DISCOVER_DEFAULT_LLM_MODEL = "google/gemma-4-26b-a4b-it"
DEFAULT_SCHEDULE_HOUR = 7
DEFAULT_SCHEDULE_MINUTE = 20
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "AVGO", "JPM",
    "V", "UNH", "XOM", "MA", "COST", "HD", "PG", "WMT", "JNJ", "ABBV",
    "NFLX", "BAC", "KO", "ORCL", "CRM", "MRK", "CVX", "WFC", "CSCO", "AMD",
    "PEP", "ACN", "TMO", "MCD", "LIN", "ABT", "DIS", "ADBE", "GE", "IBM",
    "QCOM", "CAT", "TXN", "NOW", "INTU", "AMAT", "VZ", "ISRG", "PFE", "DHR",
    "SPGI", "NEE", "RTX", "LOW", "PM", "UNP", "UBER", "BKNG", "HON", "TJX",
    "COP", "BA", "GS", "SCHW", "AXP", "BLK", "SYK", "C", "MDT", "LMT",
    "VRTX", "AMGN", "ADP", "DE", "PANW", "CB", "MMC", "ADI", "GILD", "PLD",
    "SO", "MU", "ELV", "MO", "BMY", "DUK", "ETN", "ICE", "REGN", "CI",
    "SHW", "KLAC", "BSX", "WM", "MCO", "EQIX", "HCA", "USB", "TGT", "FDX",
)


@dataclass(frozen=True)
class ValueCandidate:
    symbol: str
    company: str
    sector: str
    price: float | None
    market_cap: float | None
    trailing_pe: float | None
    forward_pe: float | None
    price_to_book: float | None
    ev_to_ebitda: float | None
    profit_margin: float | None
    return_on_equity: float | None
    debt_to_equity: float | None
    target_upside_pct: float | None
    rsi_14: float | None
    avg_volume_20d: float | None
    score: float
    thesis: str
    caveats: str


@dataclass(frozen=True)
class LLMAnalysisResult:
    symbol: str
    status: str
    decision: str
    report_path: Path | None
    error: str | None = None


def run_value_discover(
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    limit: int = 10,
    output_dir: Path | str = "reports/value_discover",
    as_of: dt.datetime | None = None,
    ticker_factory: Callable[[str], object] = yf.Ticker,
) -> tuple[list[ValueCandidate], Path, Path]:
    """Generate a top-N undervaluation watchlist and write report artifacts."""
    as_of = as_of or dt.datetime.now()
    candidates = [
        candidate
        for symbol in universe
        if (candidate := _screen_symbol(symbol, ticker_factory=ticker_factory)) is not None
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = candidates[:limit]

    report_root = Path(output_dir) / as_of.strftime("%Y-%m-%d")
    report_root.mkdir(parents=True, exist_ok=True)
    stem = f"value_discover_{as_of.strftime('%Y%m%d_%H%M%S')}"
    markdown_path = report_root / f"{stem}.md"
    csv_path = report_root / f"{stem}.csv"

    markdown_path.write_text(render_markdown(selected, as_of=as_of), encoding="utf-8")
    _write_csv(selected, csv_path)
    return selected, markdown_path, csv_path


def run_llm_analysis_for_candidates(
    candidates: Sequence[ValueCandidate],
    *,
    analysis_date: str | None = None,
    output_dir: Path | str = "reports/value_discover",
    config: dict[str, Any] | None = None,
    graph_factory: Callable[..., Any] | None = None,
    selected_analysts: Sequence[str] = ("market", "social", "news", "fundamentals"),
    per_ticker_timeout_seconds: int | None = None,
) -> tuple[list[LLMAnalysisResult], Path]:
    """Run TradingAgents LLM analysis for each Value Discover candidate."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    analysis_date = analysis_date or dt.date.today().isoformat()
    output_root = Path(output_dir) / analysis_date / "llm_analysis"
    output_root.mkdir(parents=True, exist_ok=True)

    llm_config = value_discover_llm_config(config)

    factory = graph_factory or TradingAgentsGraph
    use_hard_timeout = (
        graph_factory is None
        and per_ticker_timeout_seconds is not None
        and per_ticker_timeout_seconds > 0
    )
    graph = None
    if not use_hard_timeout:
        graph = factory(list(selected_analysts), debug=False, config=llm_config)

    results: list[LLMAnalysisResult] = []
    for rank, candidate in enumerate(candidates, start=1):
        report_path = output_root / f"{rank:02d}_{candidate.symbol}.md"
        if use_hard_timeout:
            results.append(
                _run_llm_candidate_with_hard_timeout(
                    candidate,
                    rank=rank,
                    analysis_date=analysis_date,
                    output_root=output_root,
                    config=llm_config,
                    selected_analysts=selected_analysts,
                    timeout_seconds=per_ticker_timeout_seconds or 0,
                )
            )
            continue
        timeout_active = bool(per_ticker_timeout_seconds and per_ticker_timeout_seconds > 0)
        previous_handler = None
        try:
            if timeout_active:
                previous_handler = signal.signal(
                    signal.SIGALRM,
                    _raise_llm_timeout(candidate.symbol, per_ticker_timeout_seconds),
                )
                signal.alarm(per_ticker_timeout_seconds)
            assert graph is not None
            final_state, decision = graph.propagate(
                candidate.symbol,
                analysis_date,
                asset_type="stock",
            )
            report_path.write_text(
                render_llm_report(candidate, final_state, decision, analysis_date),
                encoding="utf-8",
            )
            results.append(
                LLMAnalysisResult(
                    symbol=candidate.symbol,
                    status="ok",
                    decision=str(decision),
                    report_path=report_path,
                )
            )
        except Exception as exc:
            report_path.write_text(
                f"# {candidate.symbol} LLM Analysis Failed\n\n{exc}\n",
                encoding="utf-8",
            )
            results.append(
                LLMAnalysisResult(
                    symbol=candidate.symbol,
                    status="error",
                    decision="",
                    report_path=report_path,
                    error=str(exc),
                )
            )
        finally:
            if timeout_active:
                signal.alarm(0)
                if previous_handler is not None:
                    signal.signal(signal.SIGALRM, previous_handler)

    summary_path = output_root / "summary.md"
    summary_path.write_text(render_llm_summary(results, analysis_date), encoding="utf-8")
    return results, summary_path


def value_discover_llm_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the LLM config used by Value Discover candidate analysis."""
    from tradingagents.default_config import DEFAULT_CONFIG

    llm_config = DEFAULT_CONFIG.copy()
    if config:
        llm_config.update(config)
    else:
        llm_config.update(
            {
                "llm_provider": os.environ.get(
                    "TRADINGAGENTS_VALUE_DISCOVER_LLM_PROVIDER",
                    VALUE_DISCOVER_DEFAULT_LLM_PROVIDER,
                ),
                "quick_think_llm": os.environ.get(
                    "TRADINGAGENTS_VALUE_DISCOVER_QUICK_THINK_LLM",
                    VALUE_DISCOVER_DEFAULT_LLM_MODEL,
                ),
                "deep_think_llm": os.environ.get(
                    "TRADINGAGENTS_VALUE_DISCOVER_DEEP_THINK_LLM",
                    VALUE_DISCOVER_DEFAULT_LLM_MODEL,
                ),
                "backend_url": os.environ.get(
                    "TRADINGAGENTS_VALUE_DISCOVER_LLM_BACKEND_URL",
                    "",
                )
                or None,
            }
        )
    llm_config.setdefault("checkpoint_enabled", False)
    return llm_config


def _llm_status_fields(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "llm_provider": config.get("llm_provider"),
        "quick_think_llm": config.get("quick_think_llm"),
        "deep_think_llm": config.get("deep_think_llm"),
    }


def _raise_llm_timeout(symbol: str, timeout_seconds: int):
    def _handler(signum, frame):
        raise TimeoutError(
            f"{symbol} LLM analysis exceeded {timeout_seconds} seconds"
        )

    return _handler


def _run_llm_candidate_with_hard_timeout(
    candidate: ValueCandidate,
    *,
    rank: int,
    analysis_date: str,
    output_root: Path,
    config: dict[str, Any],
    selected_analysts: Sequence[str],
    timeout_seconds: int,
) -> LLMAnalysisResult:
    report_path = output_root / f"{rank:02d}_{candidate.symbol}.md"
    ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_run_llm_candidate_child,
        args=(queue, candidate, rank, analysis_date, output_root, config, tuple(selected_analysts)),
    )
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        error = f"{candidate.symbol} LLM analysis exceeded {timeout_seconds} seconds"
        report_path.write_text(
            f"# {candidate.symbol} LLM Analysis Failed\n\n{error}\n",
            encoding="utf-8",
        )
        return LLMAnalysisResult(
            symbol=candidate.symbol,
            status="error",
            decision="",
            report_path=report_path,
            error=error,
        )
    if not queue.empty():
        payload = queue.get()
        return LLMAnalysisResult(
            symbol=payload["symbol"],
            status=payload["status"],
            decision=payload["decision"],
            report_path=Path(payload["report_path"]) if payload.get("report_path") else None,
            error=payload.get("error"),
        )
    error = f"{candidate.symbol} LLM analysis exited without a result (exit code {process.exitcode})"
    report_path.write_text(
        f"# {candidate.symbol} LLM Analysis Failed\n\n{error}\n",
        encoding="utf-8",
    )
    return LLMAnalysisResult(
        symbol=candidate.symbol,
        status="error",
        decision="",
        report_path=report_path,
        error=error,
    )


def _run_llm_candidate_child(
    queue: Any,
    candidate: ValueCandidate,
    rank: int,
    analysis_date: str,
    output_root: Path,
    config: dict[str, Any],
    selected_analysts: tuple[str, ...],
) -> None:
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    report_path = output_root / f"{rank:02d}_{candidate.symbol}.md"
    try:
        graph = TradingAgentsGraph(list(selected_analysts), debug=False, config=config)
        final_state, decision = graph.propagate(
            candidate.symbol,
            analysis_date,
            asset_type="stock",
        )
        report_path.write_text(
            render_llm_report(candidate, final_state, decision, analysis_date),
            encoding="utf-8",
        )
        queue.put(
            {
                "symbol": candidate.symbol,
                "status": "ok",
                "decision": str(decision),
                "report_path": str(report_path),
                "error": None,
            }
        )
    except Exception as exc:
        report_path.write_text(
            f"# {candidate.symbol} LLM Analysis Failed\n\n{exc}\n",
            encoding="utf-8",
        )
        queue.put(
            {
                "symbol": candidate.symbol,
                "status": "error",
                "decision": "",
                "report_path": str(report_path),
                "error": str(exc),
            }
        )


def render_llm_report(
    candidate: ValueCandidate,
    final_state: dict[str, Any],
    decision: Any,
    analysis_date: str,
) -> str:
    sections = [
        f"# {candidate.symbol} LLM Analysis",
        "",
        f"Analysis date: {analysis_date}",
        f"Value Discover score: {candidate.score:.1f}",
        f"Quant thesis: {candidate.thesis}",
        f"Quant caveats: {candidate.caveats}",
        "",
        "Important: this is research support, not financial advice and not an order recommendation.",
        "",
        f"## Parsed Decision\n\n{decision}",
    ]
    for key, title in (
        ("market_report", "Market Analysis"),
        ("sentiment_report", "Sentiment Analysis"),
        ("news_report", "News Analysis"),
        ("fundamentals_report", "Fundamentals Analysis"),
        ("investment_plan", "Research Manager Plan"),
        ("trader_investment_plan", "Trader Plan"),
        ("final_trade_decision", "Portfolio Manager Decision"),
    ):
        content = final_state.get(key)
        if content:
            sections.append(f"## {title}\n\n{content}")
    return "\n\n".join(sections) + "\n"


def render_llm_summary(results: Sequence[LLMAnalysisResult], analysis_date: str) -> str:
    lines = [
        "# Value Discover LLM Analysis Summary",
        "",
        f"Analysis date: {analysis_date}",
        "",
        "| Ticker | Status | Decision | Report | Error |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in results:
        report = item.report_path.name if item.report_path else "N/A"
        lines.append(
            f"| {item.symbol} | {item.status} | {_md(item.decision)} | {report} | {_md(item.error or '')} |"
        )
    return "\n".join(lines) + "\n"


def render_markdown(candidates: Sequence[ValueCandidate], *, as_of: dt.datetime) -> str:
    lines = [
        "# Value Discover",
        "",
        f"Generated: {as_of.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Purpose: surface up to 10 potentially undervalued stocks for additional day-trade or long-term-investment research.",
        "",
        "Important: this is a quantitative research shortlist, not financial advice and not an order recommendation. Review news, liquidity, earnings dates, risk limits, and your own strategy before trading.",
        "",
        "| Rank | Ticker | Company | Sector | Score | Price | Fwd P/E | P/B | EV/EBITDA | Target Upside | RSI 14 | 20D Avg Vol | Thesis | Caveats |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for rank, item in enumerate(candidates, start=1):
        lines.append(
            "| {rank} | {symbol} | {company} | {sector} | {score:.1f} | {price} | {fpe} | {pb} | {ev} | {upside} | {rsi} | {vol} | {thesis} | {caveats} |".format(
                rank=rank,
                symbol=item.symbol,
                company=_md(item.company),
                sector=_md(item.sector),
                score=item.score,
                price=_fmt_money(item.price),
                fpe=_fmt_num(item.forward_pe),
                pb=_fmt_num(item.price_to_book),
                ev=_fmt_num(item.ev_to_ebitda),
                upside=_fmt_pct(item.target_upside_pct),
                rsi=_fmt_num(item.rsi_14),
                vol=_fmt_big(item.avg_volume_20d),
                thesis=_md(item.thesis),
                caveats=_md(item.caveats),
            )
        )
    return "\n".join(lines) + "\n"


def cron_entry(project_dir: Path | str, python_path: Path | str | None = None) -> str:
    project_dir = Path(project_dir).resolve()
    python_path = Path(python_path).resolve() if python_path else project_dir / ".venv" / "bin" / "python"
    command = (
        f'cd "{project_dir}" && "{python_path}" -m tradingagents.value_discover '
        f'>> "{project_dir / "reports" / "value_discover.log"}" 2>&1'
    )
    return (
        "# TradingAgents Value Discover\n"
        f"CRON_TZ={PACIFIC_TZ}\n"
        f"{DEFAULT_SCHEDULE_MINUTE} {DEFAULT_SCHEDULE_HOUR} * * * {command}\n"
        "# End TradingAgents Value Discover"
    )


def install_cron(project_dir: Path | str, python_path: Path | str | None = None) -> str:
    """Install or replace the Value Discover block in the user's crontab."""
    entry = cron_entry(project_dir, python_path)
    current = subprocess.run(
        ["crontab", "-l"],
        text=True,
        capture_output=True,
        check=False,
    )
    existing = current.stdout if current.returncode == 0 else ""
    cleaned = _remove_managed_cron_block(existing).rstrip()
    updated = f"{cleaned}\n\n{entry}\n" if cleaned else f"{entry}\n"
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    return entry


def _screen_symbol(
    symbol: str,
    *,
    ticker_factory: Callable[[str], object],
) -> ValueCandidate | None:
    try:
        ticker = ticker_factory(symbol)
        info = getattr(ticker, "info", {}) or {}
        history = ticker.history(period="1y", auto_adjust=False)
    except Exception:
        return None

    price = _first_number(info, "currentPrice", "regularMarketPrice", "previousClose")
    if price is None and isinstance(history, pd.DataFrame) and not history.empty:
        price = _safe_float(history["Close"].dropna().iloc[-1])

    if price is None or price <= 0:
        return None

    score, positives, negatives = _score(info, price, history)
    if score <= 0:
        return None

    avg_volume = _average_volume(history)
    rsi = _rsi_14(history)
    company = str(info.get("shortName") or info.get("longName") or symbol)
    sector = str(info.get("sector") or "Unknown")

    return ValueCandidate(
        symbol=symbol.upper(),
        company=company,
        sector=sector,
        price=price,
        market_cap=_safe_float(info.get("marketCap")),
        trailing_pe=_safe_float(info.get("trailingPE")),
        forward_pe=_safe_float(info.get("forwardPE")),
        price_to_book=_safe_float(info.get("priceToBook")),
        ev_to_ebitda=_safe_float(info.get("enterpriseToEbitda")),
        profit_margin=_safe_float(info.get("profitMargins")),
        return_on_equity=_safe_float(info.get("returnOnEquity")),
        debt_to_equity=_safe_float(info.get("debtToEquity")),
        target_upside_pct=_target_upside(info, price),
        rsi_14=rsi,
        avg_volume_20d=avg_volume,
        score=round(score, 2),
        thesis="; ".join(positives[:4]) or "Valuation screen passed",
        caveats="; ".join(negatives[:3]) or "No major quantitative caveat found",
    )


def _score(info: dict, price: float, history: pd.DataFrame) -> tuple[float, list[str], list[str]]:
    score = 0.0
    positives: list[str] = []
    negatives: list[str] = []

    forward_pe = _safe_float(info.get("forwardPE"))
    trailing_pe = _safe_float(info.get("trailingPE"))
    price_to_book = _safe_float(info.get("priceToBook"))
    ev_to_ebitda = _safe_float(info.get("enterpriseToEbitda"))
    margin = _safe_float(info.get("profitMargins"))
    roe = _safe_float(info.get("returnOnEquity"))
    debt_to_equity = _safe_float(info.get("debtToEquity"))
    upside = _target_upside(info, price)
    market_cap = _safe_float(info.get("marketCap"))
    avg_volume = _average_volume(history)

    if forward_pe is not None and 0 < forward_pe <= 18:
        score += min(20.0, (18.0 - forward_pe) * 1.1 + 7.0)
        positives.append(f"forward P/E {forward_pe:.1f}")
    elif trailing_pe is not None and 0 < trailing_pe <= 20:
        score += min(15.0, (20.0 - trailing_pe) * 0.7 + 4.0)
        positives.append(f"trailing P/E {trailing_pe:.1f}")
    else:
        negatives.append("P/E not clearly cheap")

    if price_to_book is not None and 0 < price_to_book <= 3:
        score += min(15.0, (3.0 - price_to_book) * 4.0 + 3.0)
        positives.append(f"P/B {price_to_book:.1f}")
    elif price_to_book is not None and price_to_book > 8:
        negatives.append(f"high P/B {price_to_book:.1f}")

    if ev_to_ebitda is not None and 0 < ev_to_ebitda <= 13:
        score += min(15.0, (13.0 - ev_to_ebitda) * 0.9 + 4.0)
        positives.append(f"EV/EBITDA {ev_to_ebitda:.1f}")

    if margin is not None and margin > 0:
        score += min(12.0, margin * 60.0)
        positives.append(f"positive margin {_fmt_pct(margin)}")
    else:
        negatives.append("weak or unavailable margins")

    if roe is not None and roe > 0.08:
        score += min(12.0, roe * 40.0)
        positives.append(f"ROE {_fmt_pct(roe)}")

    if debt_to_equity is not None:
        if debt_to_equity <= 120:
            score += 8.0
            positives.append(f"debt/equity {debt_to_equity:.0f}")
        elif debt_to_equity > 250:
            score -= 8.0
            negatives.append(f"high debt/equity {debt_to_equity:.0f}")

    if upside is not None:
        if upside >= 0.10:
            score += min(12.0, upside * 35.0)
            positives.append(f"analyst target upside {_fmt_pct(upside)}")
        elif upside < -0.05:
            score -= 5.0
            negatives.append(f"analyst target downside {_fmt_pct(upside)}")

    if avg_volume is not None and avg_volume >= 1_000_000:
        score += 4.0
        positives.append("liquid 20D volume")
    else:
        negatives.append("liquidity may be thin for day trading")

    if market_cap is not None and market_cap >= 10_000_000_000:
        score += 4.0

    rsi = _rsi_14(history)
    if rsi is not None:
        if 30 <= rsi <= 55:
            score += 5.0
            positives.append(f"RSI {rsi:.1f} not overextended")
        elif rsi > 70:
            score -= 5.0
            negatives.append(f"RSI {rsi:.1f} overbought")

    return max(score, 0.0), positives, negatives


def _target_upside(info: dict, price: float) -> float | None:
    target = _safe_float(info.get("targetMeanPrice"))
    if target is None or target <= 0 or price <= 0:
        return None
    return (target - price) / price


def _average_volume(history: pd.DataFrame) -> float | None:
    if not isinstance(history, pd.DataFrame) or "Volume" not in history or history.empty:
        return None
    volume = history["Volume"].dropna().tail(20)
    if volume.empty:
        return None
    return _safe_float(volume.mean())


def _rsi_14(history: pd.DataFrame) -> float | None:
    if not isinstance(history, pd.DataFrame) or "Close" not in history or len(history) < 16:
        return None
    close = history["Close"].dropna()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    latest_loss = loss.iloc[-1]
    if pd.isna(latest_loss) or latest_loss == 0:
        return None
    rsi = 100 - (100 / (1 + gain.iloc[-1] / latest_loss))
    return _safe_float(rsi)


def _write_csv(candidates: Sequence[ValueCandidate], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(candidates[0]).keys()) if candidates else list(ValueCandidate.__dataclass_fields__.keys()))
        writer.writeheader()
        for item in candidates:
            writer.writerow(asdict(item))


def _remove_managed_cron_block(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == "# TradingAgents Value Discover":
            skipping = True
            continue
        if line.strip() == "# End TradingAgents Value Discover":
            skipping = False
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _first_number(info: dict, *keys: str) -> float | None:
    for key in keys:
        value = _safe_float(info.get(key))
        if value is not None:
            return value
    return None


def _safe_float(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f}"


def _fmt_money(value: float | None) -> str:
    return "N/A" if value is None else f"${value:.2f}"


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _fmt_big(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    from tradingagents.public_equity import write_idea_generation_payload
    from tradingagents.report_index import write_report_index

    output_dir = Path(os.environ.get("TRADINGAGENTS_VALUE_DISCOVER_DIR", "reports/value_discover"))
    limit = _env_int("TRADINGAGENTS_VALUE_DISCOVER_LIMIT", 10)
    as_of = dt.datetime.now()
    run_dir = output_dir / as_of.strftime("%Y-%m-%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    llm_config = value_discover_llm_config()
    _write_value_status(
        status_path,
        {
            "status": "running",
            "analysis_date": as_of.strftime("%Y-%m-%d"),
            "started_at": as_of.isoformat(),
            **_llm_status_fields(llm_config),
            "steps": [
                {"id": "screen", "label": "Screen universe", "status": "running"},
                {"id": "public_equity", "label": "Write Public Equity triage", "status": "pending"},
                {"id": "llm_analysis", "label": "Run LLM analysis", "status": "pending"},
                {"id": "index_report", "label": "Update report index", "status": "pending"},
            ],
        },
    )
    try:
        candidates, markdown_path, csv_path = run_value_discover(
            limit=limit,
            output_dir=output_dir,
            as_of=as_of,
        )
        print(f"Value Discover completed: {len(candidates)} candidates")
        print(f"Markdown: {markdown_path}")
        print(f"CSV: {csv_path}")
        _write_value_status(
            status_path,
            {
                "status": "running",
                "analysis_date": as_of.strftime("%Y-%m-%d"),
                "started_at": as_of.isoformat(),
                "candidate_count": len(candidates),
                "value_discover_markdown": str(markdown_path),
                "value_discover_csv": str(csv_path),
                **_llm_status_fields(llm_config),
                "steps": [
                    {"id": "screen", "label": "Screen universe", "status": "ok"},
                    {"id": "public_equity", "label": "Write Public Equity triage", "status": "running"},
                    {"id": "llm_analysis", "label": "Run LLM analysis", "status": "pending"},
                    {"id": "index_report", "label": "Update report index", "status": "pending"},
                ],
            },
        )
        public_equity_json, public_equity_markdown = write_idea_generation_payload(
            candidates,
            as_of=as_of,
            output_dir=output_dir,
            markdown_path=markdown_path,
            csv_path=csv_path,
        )
        print(f"Public Equity payload: {public_equity_json}")
        print(f"Public Equity triage: {public_equity_markdown}")
        llm_enabled = os.environ.get("TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED", "true")
        summary_path = None
        results = []
        _write_value_status(
            status_path,
            {
                "status": "running",
                "analysis_date": as_of.strftime("%Y-%m-%d"),
                "started_at": as_of.isoformat(),
                "candidate_count": len(candidates),
                "value_discover_markdown": str(markdown_path),
                "value_discover_csv": str(csv_path),
                "public_equity_payload": str(public_equity_json),
                "public_equity_markdown": str(public_equity_markdown),
                **_llm_status_fields(llm_config),
                "steps": [
                    {"id": "screen", "label": "Screen universe", "status": "ok"},
                    {"id": "public_equity", "label": "Write Public Equity triage", "status": "ok"},
                    {"id": "llm_analysis", "label": "Run LLM analysis", "status": "running"},
                    {"id": "index_report", "label": "Update report index", "status": "pending"},
                ],
            },
        )
        if llm_enabled.strip().lower() in ("1", "true", "yes", "on"):
            llm_limit = _env_int("TRADINGAGENTS_VALUE_DISCOVER_LLM_LIMIT", len(candidates))
            timeout_seconds = _env_int("TRADINGAGENTS_VALUE_DISCOVER_LLM_TIMEOUT_SECONDS", 180)
            results, summary_path = run_llm_analysis_for_candidates(
                candidates[:llm_limit],
                output_dir=output_dir,
                analysis_date=dt.date.today().isoformat(),
                config=llm_config,
                per_ticker_timeout_seconds=timeout_seconds,
            )
            ok_count = sum(1 for result in results if result.status == "ok")
            print(f"LLM analysis completed: {ok_count}/{len(results)} succeeded")
            print(f"LLM summary: {summary_path}")
            public_equity_json, public_equity_markdown = write_idea_generation_payload(
                candidates,
                as_of=as_of,
                output_dir=output_dir,
                markdown_path=markdown_path,
                csv_path=csv_path,
                llm_summary_path=summary_path,
            )
        _write_value_status(
            status_path,
            {
                "status": "running",
                "analysis_date": as_of.strftime("%Y-%m-%d"),
                "started_at": as_of.isoformat(),
                "candidate_count": len(candidates),
                "llm_success_count": sum(1 for result in results if result.status == "ok"),
                "llm_error_count": sum(1 for result in results if result.status == "error"),
                "value_discover_markdown": str(markdown_path),
                "value_discover_csv": str(csv_path),
                "public_equity_payload": str(public_equity_json),
                "public_equity_markdown": str(public_equity_markdown),
                "llm_summary": str(summary_path) if summary_path else None,
                **_llm_status_fields(llm_config),
                "steps": [
                    {"id": "screen", "label": "Screen universe", "status": "ok"},
                    {"id": "public_equity", "label": "Write Public Equity triage", "status": "ok"},
                    {"id": "llm_analysis", "label": "Run LLM analysis", "status": "ok"},
                    {"id": "index_report", "label": "Update report index", "status": "running"},
                ],
            },
        )
        index_path = write_report_index(output_dir.parent)
        print(f"Report index: {index_path}")
        _write_value_status(
            status_path,
            {
                "status": "ok",
                "analysis_date": as_of.strftime("%Y-%m-%d"),
                "started_at": as_of.isoformat(),
                "completed_at": dt.datetime.now().isoformat(),
                "candidate_count": len(candidates),
                "llm_success_count": sum(1 for result in results if result.status == "ok"),
                "llm_error_count": sum(1 for result in results if result.status == "error"),
                "value_discover_markdown": str(markdown_path),
                "value_discover_csv": str(csv_path),
                "public_equity_payload": str(public_equity_json),
                "public_equity_markdown": str(public_equity_markdown),
                "llm_summary": str(summary_path) if summary_path else None,
                "report_index": str(index_path),
                **_llm_status_fields(llm_config),
                "steps": [
                    {"id": "screen", "label": "Screen universe", "status": "ok"},
                    {"id": "public_equity", "label": "Write Public Equity triage", "status": "ok"},
                    {"id": "llm_analysis", "label": "Run LLM analysis", "status": "ok"},
                    {"id": "index_report", "label": "Update report index", "status": "ok"},
                ],
            },
        )
    except Exception as exc:
        _write_value_status(
            status_path,
            {
                "status": "error",
                "analysis_date": as_of.strftime("%Y-%m-%d"),
                "started_at": as_of.isoformat(),
                "completed_at": dt.datetime.now().isoformat(),
                "error": str(exc),
                **_llm_status_fields(llm_config),
            },
        )
        raise


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _write_value_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Value Discover failed: {exc}", file=sys.stderr)
        raise
