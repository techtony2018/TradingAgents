#!/usr/bin/env python3
"""Render the frozen 2026-08-02 Value Discover shortlist as a cited HTML triage report.

This renderer never fetches data, invokes an LLM, recalculates a score, or
changes a source artifact.  It requires the exact frozen input files and
materializes a standalone reader artifact plus a machine-readable ledger.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
from pathlib import Path
from typing import Any


FROZEN_CSV = Path("reports/value_discover/2026-08-02/value_discover_20260802_173132.csv")
EARLIER_FROZEN_CSV = (
    Path("reports/value_discover/2026-08-02/value_discover_20260802_172556.csv"),
    Path("reports/value_discover/2026-08-02/value_discover_20260802_173008.csv"),
)
FROZEN_ROUTER = Path("reports/value_discover/2026-08-02/public_equity_idea_generation_20260802_173132.json")
FROZEN_STATUS = Path("reports/value_discover/2026-08-02/status.json")
SCORING_CODE = Path("TradingAgents/value_discover.py")
EXPECTED_ORDER = ("MU", "CI", "DIS", "GILD", "NVDA", "PFE", "CB", "UBER", "ADBE", "INTU")
RETRIEVED_AT = "2026-08-03T00:59:51Z"


MARKET = {
    "MU": (823.03, 54_538_600, "2026-07-31T20:00:00Z", "NasdaqGS", "Micron Technology, Inc."),
    "CI": (279.05, 1_731_318, "2026-07-31T20:00:03Z", "NYSE", "The Cigna Group"),
    "DIS": (96.19, 8_249_340, "2026-07-31T20:04:10Z", "NYSE", "The Walt Disney Company"),
    "GILD": (130.21, 6_531_645, "2026-07-31T20:00:01Z", "NasdaqGS", "Gilead Sciences, Inc."),
    "NVDA": (200.75, 139_961_152, "2026-07-31T20:00:01Z", "NasdaqGS", "NVIDIA Corporation"),
    "PFE": (25.01, 33_906_387, "2026-07-31T20:01:49Z", "NYSE", "Pfizer Inc."),
    "CB": (350.68, 846_578, "2026-07-31T20:00:03Z", "NYSE", "Chubb Limited"),
    "UBER": (70.36, 11_858_962, "2026-07-31T20:00:03Z", "NYSE", "Uber Technologies, Inc."),
    "ADBE": (250.41, 5_589_512, "2026-07-31T20:00:01Z", "NasdaqGS", "Adobe Inc."),
    "INTU": (316.07, 3_118_109, "2026-07-31T20:00:01Z", "NasdaqGS", "Intuit Inc."),
}


SEC = {
    "MU": ("0000723125", "10-Q", "2026-06-25", "2026-05-28", "https://www.sec.gov/Archives/edgar/data/723125/000072312526000015/mu-20260528.htm"),
    "CI": ("0001739940", "10-Q", "2026-07-30", "2026-06-30", "https://www.sec.gov/Archives/edgar/data/1739940/000173994026000065/ci-20260630.htm"),
    "DIS": ("0001744489", "10-Q", "2026-05-06", "2026-03-28", "https://www.sec.gov/Archives/edgar/data/1744489/000174448926000037/dis-20260328.htm"),
    "GILD": ("0000882095", "10-Q", "2026-05-07", "2026-03-31", "https://www.sec.gov/Archives/edgar/data/882095/000088209526000024/gild-20260331.htm"),
    "NVDA": ("0001045810", "10-Q", "2026-05-20", "2026-04-26", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000052/nvda-20260426.htm"),
    "PFE": ("0000078003", "10-Q", "2026-05-05", "2026-03-29", "https://www.sec.gov/Archives/edgar/data/78003/000007800326000054/pfe-20260329.htm"),
    "CB": ("0000896159", "10-Q", "2026-07-28", "2026-06-30", "https://www.sec.gov/Archives/edgar/data/896159/000089615926000017/cb-20260630.htm"),
    "UBER": ("0001543151", "10-Q", "2026-05-06", "2026-03-31", "https://www.sec.gov/Archives/edgar/data/1543151/000154315126000022/uber-20260331.htm"),
    "ADBE": ("0000796343", "10-Q", "2026-06-15", "2026-05-29", "https://www.sec.gov/Archives/edgar/data/796343/000079634326000112/adbe-20260529.htm"),
    "INTU": ("0000896878", "10-Q", "2026-05-20", "2026-04-30", "https://www.sec.gov/Archives/edgar/data/896878/000089687826000025/intu-20260430.htm"),
}


ASSESSMENTS = {
    "CI": {
        "status": "Research candidate",
        "actionability": "Rebuild from the fresh quarterly filing before deciding whether the apparent valuation discount is real.",
        "variant": "Possible wedge: the frozen screen may understate earnings durability; this is an inference, not yet proven.",
        "why": "A Q2 2026 10-Q was filed July 30, creating a current evidence window; the frozen target-gap input is 23.1%.",
        "reject": "The generic score uses valuation and leverage factors that are not insurer-specific, so cheapness may be false precision.",
        "investable": "Rebuild medical-cost, pharmacy, capital-return, and EPS bridges from the 10-Q and current estimates.",
        "kill": "Deteriorating medical-cost/guidance evidence or failure to corroborate the frozen target input.",
        "workflow": "Company tearsheet → financials normalization → earnings deep dive / insurer comps",
        "support": "Partially supported for triage; not decision-ready",
    },
    "DIS": {
        "status": "Research candidate",
        "actionability": "Test whether the 31.5% frozen target gap survives a segment cash-flow and expectations rebuild.",
        "variant": "Possible wedge: the market may discount a weaker recovery than the source packet implies; exposure proof is still unknown.",
        "why": "The July 31 close confirms the frozen price anchor, while the screen shows one of the larger non-extreme target gaps.",
        "reject": "The target has no analyst-count, age, or revision provenance, and the latest periodic source in this packet is from March.",
        "investable": "Source-backed segment bridge, consensus revisions, catalyst dates, and downside case.",
        "kill": "No cash-flow inflection, target support that is stale/conflicted, or downside already implied by segment trends.",
        "workflow": "Company tearsheet → earnings deep dive → scenario sensitivity",
        "support": "Partially supported for triage; not decision-ready",
    },
    "GILD": {
        "status": "Research candidate",
        "actionability": "Underwrite cash-flow durability and pipeline concentration before treating the 20.7% target gap as a wedge.",
        "variant": "Possible wedge: durable cash generation may be underweighted; no source-backed pipeline edge is established here.",
        "why": "Current close matches the frozen price; the score combines positive margin/ROE with a moderate target gap.",
        "reject": "A low multiple can be a value trap if concentration or pipeline replacement risk dominates.",
        "investable": "Product-level revenue bridge, loss-of-exclusivity map, pipeline probabilities, and consensus revision history.",
        "kill": "Core-franchise erosion or pipeline evidence insufficient to replace the earnings base.",
        "workflow": "Company tearsheet → catalyst calendar → earnings deep dive",
        "support": "Partially supported for triage; not decision-ready",
    },
    "NVDA": {
        "status": "Watchlist",
        "actionability": "Wait for estimate/valuation attribution; price confirmation alone does not validate a 50.8% target gap.",
        "variant": "Unknown. The source packet shows expectations risk, not a differentiated edge.",
        "why": "The market-data anchor is confirmed, but target support is unusually large and unproven.",
        "reject": "The score mixes strong quality with valuation and target inputs without an explicit expectations bar.",
        "investable": "Current consensus bridge, data-center demand attribution, margins, valuation scenarios, and supply constraints.",
        "kill": "Estimates already price the upside, or target support cannot be reconstructed from current forecasts.",
        "workflow": "Earnings preview → comps valuation → scenario sensitivity",
        "support": "Numeric input preserved; decision support incomplete",
    },
    "PFE": {
        "status": "Watchlist",
        "actionability": "Require patent-loss, pipeline, and capital-allocation proof before advancing a low-multiple screen.",
        "variant": "Possible wedge: replacement assets may offset erosion better than feared; currently unproven.",
        "why": "The frozen price and current close agree, while the target gap is a moderate 14.9%.",
        "reject": "Low P/E can reflect a declining denominator; the score does not model patent-cliff timing.",
        "investable": "Patent-expiry schedule, product/pipeline bridge, debt and capital-return path, and estimates.",
        "kill": "Erosion exceeds replacement growth or leverage constrains capital allocation.",
        "workflow": "Company tearsheet → catalyst calendar → earnings deep dive",
        "support": "Numeric input preserved; sector-specific work missing",
    },
    "CB": {
        "status": "Watchlist",
        "actionability": "Use the fresh Q2 filing to test quality and underwriting, not the generic target gap.",
        "variant": "Possible quality compounder, but the 4.2% target gap offers little visible dislocation.",
        "why": "A Q2 2026 10-Q was filed July 28, the freshest primary packet among the ten names.",
        "reject": "EV/EBITDA and debt/equity weights are not appropriate substitutes for insurer underwriting metrics.",
        "investable": "Combined ratio, pricing, reserve development, book-value growth, catastrophe load, and insurer comps.",
        "kill": "Adverse reserve development or no valuation cushion after insurer-specific normalization.",
        "workflow": "Financials normalization → insurer comps → thesis tracker",
        "support": "Original score method is poorly matched to insurer economics",
    },
    "UBER": {
        "status": "Watchlist",
        "actionability": "Demand a free-cash-flow and competitive bridge before trusting the 47.7% target gap.",
        "variant": "Possible wedge: operating leverage may be underestimated; the source packet does not prove it.",
        "why": "Current close confirms the price anchor, but the target input is one of the largest in the list.",
        "reject": "Target optimism can dominate a screen without unit-economics, regulatory, or competitive evidence.",
        "investable": "Mobility/delivery growth, bookings-to-FCF bridge, take rate, regulation, and consensus revisions.",
        "kill": "FCF conversion stalls, incentives reaccelerate, or target support is stale.",
        "workflow": "Company tearsheet → earnings deep dive → scenario sensitivity",
        "support": "Numeric input preserved; target support uncorroborated",
    },
    "INTU": {
        "status": "Watchlist",
        "actionability": "Wait for a current annual-results/estimate packet before interpreting the 44.4% target gap.",
        "variant": "Unknown. A price drawdown is not itself a variant perception.",
        "why": "The July 31 close confirms the frozen price; the implied target remains uncorroborated.",
        "reject": "The screen lacks retention, ecosystem, credit, and normalized growth evidence.",
        "investable": "Current annual results, segment/KPI bridge, Credit Karma credit sensitivity, and valuation scenarios.",
        "kill": "Durable growth or margin assumptions reset below the level needed to support the implied target.",
        "workflow": "Earnings deep dive → comps valuation → thesis tracker",
        "support": "Numeric input preserved; current estimate packet missing",
    },
    "MU": {
        "status": "Deprioritized",
        "actionability": "Do not advance until the 85.0% target input and scan-completeness anomaly are independently resolved.",
        "variant": "Unknown. The top score is driven partly by an extreme target input without analyst provenance.",
        "why": "The current close confirms price, but the implied $1,522.26 target is an outlier requiring primary estimate evidence.",
        "reject": "Cyclical peak risk and target-source uncertainty can make a high score a false positive.",
        "investable": "HBM/DRAM supply-demand, capex, normalized margins/FCF, target analyst count/age, and cycle scenarios.",
        "kill": "Target support fails, normalized FCF is materially below the screen thesis, or supply response breaks pricing.",
        "workflow": "Earnings deep dive → cycle scenario sensitivity → thesis tracker",
        "support": "Original score is not supportable for PM use without target and cycle repair",
    },
    "ADBE": {
        "status": "Deprioritized",
        "actionability": "No active work until a catalyst or estimate revision creates a clearer wedge.",
        "variant": "Unknown. The frozen target gap is only 7.7%, with no source-backed catalyst in the packet.",
        "why": "Current close confirms price, but the screen does not establish why the gap should close now.",
        "reject": "Low apparent valuation can reflect growth uncertainty; the packet lacks ARR/AI monetization evidence.",
        "investable": "ARR/net-new ARR, AI monetization, retention, margins, current estimates, and valuation scenarios.",
        "kill": "Growth decelerates or AI monetization does not offset pricing/competitive pressure.",
        "workflow": "Earnings deep dive → comps valuation → catalyst calendar",
        "support": "Numeric input preserved; no actionable wedge established",
    },
}


def load_frozen() -> list[dict[str, Any]]:
    with FROZEN_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if tuple(row["symbol"] for row in rows) != EXPECTED_ORDER:
        raise ValueError("Frozen shortlist identity/order changed")
    router = json.loads(FROZEN_ROUTER.read_text(encoding="utf-8"))
    routed = router["tabs"][1]["modules"][0]["rows"]
    if tuple(row["ticker"] for row in routed) != EXPECTED_ORDER:
        raise ValueError("Frozen Public Equity router input changed")
    for row, routed_row in zip(rows, routed):
        if float(row["score"]) != float(routed_row["score"]):
            raise ValueError(f"Frozen score mismatch for {row['symbol']}")
        market_price = MARKET[row["symbol"]][0]
        if float(row["price"]) != market_price:
            raise ValueError(f"Current close no longer matches frozen anchor for {row['symbol']}")
    return rows


def build_sources(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources: list[dict[str, Any]] = [
        _source("F1", "ALL", "TradingAgents Value Discover", "deterministic screen output", "Prompt/local input", str(FROZEN_CSV), "2026-08-02", "Snapshot run 2026-08-02 17:31:32 America/Los_Angeles", "n/a", "current trial input", "Yahoo-derived fields have no analyst-count/estimate-age provenance; score is heuristic.", "All frozen scores, prices, market caps, factors and target-upside inputs."),
        _source("F4", "ALL", "TradingAgents Value Discover", "deterministic screen output", "Prompt/local input", str(EARLIER_FROZEN_CSV[0]), "2026-08-02", "Snapshot run 2026-08-02 17:25:56 America/Los_Angeles", "n/a", "same-day trial evidence", "Only one candidate survived; per-symbol failures are not enumerated in the artifact.", "First same-day candidate count (1), used only for cross-run coverage comparison."),
        _source("F5", "ALL", "TradingAgents Value Discover", "deterministic screen output", "Prompt/local input", str(EARLIER_FROZEN_CSV[1]), "2026-08-02", "Snapshot run 2026-08-02 17:30:08 America/Los_Angeles", "n/a", "same-day trial evidence", "Candidate presence does not independently prove full source coverage.", "Second same-day candidate count (10), used only for cross-run coverage comparison."),
        _source("F2", "ALL", "TradingAgents Public Equity router", "deterministic routing output", "Prompt/local input", str(FROZEN_ROUTER), "2026-08-02", "Snapshot run 2026-08-02 17:31:32 America/Los_Angeles", "n/a", "current trial input", "Support-stage routing only; not a recommendation.", "Original deterministic workflow routes and candidate order."),
        _source("F3", "ALL", "TradingAgents source code", "local methodology", "Prompt/local input", f"{SCORING_CODE}@3824aa824eb713a6bc9684a660d0fdf856afac7f", "2026-08-02", "Commit snapshot", "n/a", "current code snapshot", "Heuristic weights are not calibrated; exceptions are silently dropped per symbol.", "Score inputs, target-upside formula, top-N sorting and LLM-disabled control flow."),
        _source("R1", "ALL", "Codex runtime", "runtime disclosure", "System", "System runtime disclosure provided to coordinator", "2026-08-02", "Current task runtime", "n/a", "current", "System identifies Codex based on GPT-5; exact deployment model ID/version is unavailable.", "Authoring/runtime disclosure; no embedded TradingAgents LLM was invoked."),
        _source("YF1", "ALL", "yfinance documentation", "official project documentation", "Documentation", "https://ranaroussi.github.io/yfinance/", "2026-07-23", "Documentation page", "n/a", "current", "yfinance is unaffiliated with Yahoo and intended for research/education; Yahoo data use is subject to Yahoo terms.", "Origin and limitations of frozen Yahoo-derived fields."),
    ]
    for row in rows:
        ticker = row["symbol"]
        price, volume, as_of, exchange, company = MARKET[ticker]
        cik, form, filing_date, report_date, filing_url = SEC[ticker]
        sources.append(
            _source(
                f"M-{ticker}", ticker, "Yahoo Finance", "market data", "Market data / reputable aggregator",
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d",
                "2026-07-31", as_of, "5 trading days", "last completed regular close",
                "Single public aggregator; no consensus history, analyst count, estimate age, ownership or short-interest evidence.",
                f"Security identity ({company}, {exchange}), regular close ${price:.2f}, and regular volume {volume:,}.",
            )
        )
        sources.append(
            _source(
                f"S-{ticker}", ticker, "U.S. SEC / issuer filing", "primary filing", "Primary company source",
                filing_url, filing_date, filing_date, f"{form} period ended {report_date}", _freshness(filing_date),
                "Filing presence/date is used; this triage does not extract a full financial model from the filing.",
                f"Issuer identity and latest periodic filing in the source packet: {form}, filed {filing_date}, period ended {report_date}; CIK {cik}.",
            )
        )
    return {"schema": "value_discover_idea_triage_sources.v1", "retrieved_at": RETRIEVED_AT, "source_count": len(sources), "sources": sources}


def _source(source_id: str, ticker: str, owner: str, source_type: str, tier: str, url: str, document_date: str, as_of: str, period: str, freshness: str, limits: str, supports: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "ticker": ticker,
        "source_owner": owner,
        "source_type": source_type,
        "source_tier": tier,
        "url_or_document": url,
        "document_date": document_date,
        "as_of": as_of,
        "filing_period": period,
        "retrieved_at": RETRIEVED_AT,
        "freshness": freshness,
        "limitations": limits,
        "supports": supports,
    }


def _freshness(filing_date: str) -> str:
    age = (dt.date(2026, 8, 2) - dt.date.fromisoformat(filing_date)).days
    return f"{age} days old at trial date"


def render_html(rows: list[dict[str, Any]], ledger: dict[str, Any]) -> str:
    by_symbol = {row["symbol"]: row for row in rows}
    ordered = [by_symbol[s] for s in ("CI", "DIS", "GILD", "NVDA", "PFE", "CB", "UBER", "INTU", "MU", "ADBE")]
    counts = {status: sum(ASSESSMENTS[r["symbol"]]["status"] == status for r in rows) for status in ("Research candidate", "Watchlist", "Deprioritized")}
    table_rows = "".join(_candidate_row(row) for row in ordered)
    top_cards = "".join(_idea_card(by_symbol[s]) for s in ("CI", "DIS", "GILD"))
    source_rows = "".join(_source_row(source) for source in ledger["sources"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Value Discover — 10-name idea triage</title>
<style>
:root{{--ink:#172033;--muted:#647087;--line:#dce2eb;--paper:#f5f7fb;--card:#fff;--navy:#172b4d;--blue:#2f63c6;--green:#147d64;--amber:#a45b05;--red:#a33b42}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
a{{color:var(--blue);text-decoration:none}} a:hover{{text-decoration:underline}} .wrap{{max-width:1240px;margin:auto;padding:36px 24px 80px}}
.hero{{background:linear-gradient(130deg,#132440,#254a83);color:white;border-radius:24px;padding:42px;box-shadow:0 18px 55px #18345a26}}
.eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.14em;font-weight:800;color:#a9c8ff}} h1{{font-size:42px;line-height:1.08;margin:8px 0 14px;max-width:850px}} h2{{font-size:25px;margin:0 0 16px}} h3{{font-size:20px;margin:0}}
.hero p{{max-width:880px;color:#e4ecfa;font-size:17px}} .notice{{margin-top:20px;padding:14px 16px;border:1px solid #ffffff33;background:#ffffff12;border-radius:12px;font-size:14px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:24px}} .tile{{background:#fff;color:var(--ink);padding:16px;border-radius:14px}} .tile strong{{display:block;font-size:27px}} .tile span{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}
.section{{margin-top:30px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:26px;box-shadow:0 8px 25px #1720330a}}
.funnel{{display:grid;grid-template-columns:3fr 5fr 2fr;gap:10px}} .bucket{{padding:18px;border-radius:12px;color:white}} .research{{background:var(--green)}} .watch{{background:var(--amber)}} .deprioritized{{background:var(--red)}} .bucket b{{font-size:24px;display:block}}
.callout{{border-left:4px solid var(--amber);background:#fff7e9;padding:15px 18px;border-radius:0 10px 10px 0;margin:16px 0}} .callout.red{{border-color:var(--red);background:#fff1f2}}
.label{{display:inline-block;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;margin-right:5px}} .fact{{background:#e7f0ff;color:#2453a2}} .estimate{{background:#efe8ff;color:#6542a5}} .inference{{background:#fff1d8;color:#87500a}} .judgment{{background:#e4f5ef;color:#11634f}} .gap{{background:#ffe7e9;color:#8e2f36}}
.scroll{{overflow:auto}} table{{width:100%;border-collapse:collapse;font-size:13px}} th{{position:sticky;top:0;background:#eef2f8;color:#45536a;text-align:left;padding:10px;border-bottom:2px solid var(--line)}} td{{vertical-align:top;padding:11px 10px;border-bottom:1px solid var(--line);min-width:100px}} td.wide{{min-width:220px}} tr:last-child td{{border-bottom:0}}
.status{{font-weight:800;white-space:nowrap}} .status.research-candidate{{color:var(--green)}} .status.watchlist{{color:var(--amber)}} .status.deprioritized{{color:var(--red)}} sup a{{font-weight:800}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}} .idea{{border:1px solid var(--line);border-radius:14px;padding:18px}} .idea .metrics{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}} .pill{{background:#eef2f8;border-radius:999px;padding:5px 9px;font-size:12px}} .idea dl{{margin:12px 0 0}} .idea dt{{font-weight:800;margin-top:10px}} .idea dd{{margin:2px 0;color:#3f4b5f}}
.formula{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f0f3f8;border-radius:8px;padding:8px;font-size:12px;display:block;margin-top:6px}}
.method{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .small{{font-size:12px;color:var(--muted)}} footer{{margin-top:28px;color:var(--muted);font-size:12px}}
@media(max-width:900px){{.tiles,.cards,.method{{grid-template-columns:1fr 1fr}} .funnel{{grid-template-columns:1fr}} h1{{font-size:34px}}}}
@media(max-width:620px){{.wrap{{padding:16px 12px 50px}} .hero{{padding:25px 20px}} .tiles,.cards,.method{{grid-template-columns:1fr}} h1{{font-size:29px}} .section{{padding:18px}}}}
@media print{{body{{background:white}} .wrap{{max-width:none}} .hero,.section{{box-shadow:none}}}}
</style></head><body><main class="wrap">
<section class="hero"><div class="eyebrow">Tammy · Public Equity Investing · Idea generation</div><h1>Ten-name Value Discover trial: three names merit deeper work, but no score is investment-ready</h1>
<p>The frozen deterministic shortlist is preserved exactly. Current public market checks confirm all ten July 31 closing-price anchors, while SEC primary-source checks identify the latest periodic filing for each issuer. The screen remains a research-priority queue—not a buy list, position recommendation, or order instruction.</p>
<div class="notice"><b>Decision boundary:</b> Research candidate / Watchlist / Deprioritized only. No embedded Gemma/OpenRouter call, brokerage access, wallet access, score rerun, score alteration, or transaction occurred. Runtime: Codex based on GPT-5; exact deployment model ID/version unavailable.<sup><a href="#src-R1">R1</a></sup></div>
<div class="tiles"><div class="tile"><strong>10</strong><span>Frozen candidates</span></div><div class="tile"><strong>{counts['Research candidate']}</strong><span>Research candidates</span></div><div class="tile"><strong>{counts['Watchlist']}</strong><span>Watchlist</span></div><div class="tile"><strong>{counts['Deprioritized']}</strong><span>Deprioritized</span></div></div></section>

<section class="section"><h2>Candidate funnel</h2><div class="funnel"><div class="bucket research"><b>3</b>Research candidate<br><small>CI · DIS · GILD</small></div><div class="bucket watch"><b>5</b>Watchlist<br><small>NVDA · PFE · CB · UBER · INTU</small></div><div class="bucket deprioritized"><b>2</b>Deprioritized<br><small>MU · ADBE</small></div></div>
<div class="callout"><span class="label judgment">PM judgment</span>Advance means “spend the next research hour,” not “own the stock.” CI leads because a Q2 filing arrived July 30; DIS and GILD advance because their frozen gaps are material enough to test and not as extreme as MU/NVDA/UBER/INTU. Evidence is still incomplete.</div>
<div class="callout red"><span class="label fact">Fact</span>The three same-day screen files produced 1, 10, and 10 candidates.<sup><a href="#src-F4">F4</a></sup><sup><a href="#src-F5">F5</a></sup><sup><a href="#src-F1">F1</a></sup> The scanner catches all per-symbol exceptions without recording coverage or cause, so a successful run can mask source failure.<sup><a href="#src-F3">F3</a></sup> <span class="label judgment">PM judgment</span>That failure mode prevents treating score rank as robust.</div></section>

<section class="section"><h2>Ranked candidate board</h2><p class="small">Original scores are displayed in their frozen order but are not reused as PM rankings. Every close below independently matched the frozen price at the July 31 regular close.</p><div class="scroll"><table><thead><tr><th>Name</th><th>Status</th><th>Frozen score / support</th><th>Current anchor</th><th>Actionability & variant wedge</th><th>Why now</th><th>First rejection</th><th>Investable / kill</th><th>Next workflow</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>

<section class="section"><h2>Highest-priority work cards</h2><div class="cards">{top_cards}</div></section>

<section class="section"><h2>Input integrity and calculation audit</h2><div class="method"><div><h3>What is supported</h3><ul><li><span class="label fact">Fact</span>All ten current closes match the frozen price fields.<sup><a href="#src-F1">F1</a></sup></li><li><span class="label fact">Fact</span>Frozen CSV and Public Equity JSON contain the same names, order, and scores.<sup><a href="#src-F1">F1</a></sup><sup><a href="#src-F2">F2</a></sup></li><li><span class="label fact">Fact</span>The deterministic code computes target upside as (targetMeanPrice − price) / price, scores supplied factors, sorts descending, and selects top N.<sup><a href="#src-F3">F3</a></sup></li></ul></div>
<div><h3>What is suspect or unknown</h3><ul><li><span class="label gap">Data gap</span>Target inputs lack analyst count, contributor identity, estimate age, and revision history.</li><li><span class="label inference">Inference</span>MU’s 85.0% target gap is an outlier; NVDA 50.8%, UBER 47.7%, and INTU 44.4% also require corroboration.<sup><a href="#src-F1">F1</a></sup></li><li><span class="label judgment">PM judgment</span>Generic EV/EBITDA and debt/equity weights are not sufficient for insurers CI/CB.</li><li><span class="label gap">Data gap</span>No direct consensus revisions, ownership, short interest, borrow, options, or portfolio context.</li></ul></div></div></section>

<section class="section"><h2>Source ledger</h2><p class="small">{ledger['source_count']} entries. Material numbers and dated facts link to this ledger. Conflicts and limits are preserved rather than reconciled by assumption.</p><div class="scroll"><table><thead><tr><th>ID</th><th>Ticker</th><th>Owner / type / tier</th><th>Document</th><th>Date / period / as-of</th><th>Freshness</th><th>Limits</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
<footer>Generated for research-support triage on 2026-08-02. Standalone HTML; no remote assets or scripts. Not financial advice and not an order recommendation.</footer>
</main></body></html>"""


def _candidate_row(row: dict[str, Any]) -> str:
    ticker = row["symbol"]
    a = ASSESSMENTS[ticker]
    price = float(row["price"])
    upside = float(row["target_upside_pct"])
    target = price * (1 + upside)
    cls = a["status"].lower().replace(" ", "-")
    return f"<tr><td><b>{ticker}</b><br><span class='small'>{html.escape(row['company'])}</span></td><td><span class='status {cls}'>{a['status']}</span></td><td><b>{float(row['score']):.2f}</b><sup><a href='#src-F1'>F1</a></sup><br><span class='small'>{html.escape(a['support'])}</span></td><td>${price:,.2f}<sup><a href='#src-M-{ticker}'>M-{ticker}</a></sup><br><span class='small'>Mkt cap {_money(float(row['market_cap']))}<sup><a href='#src-F1'>F1</a></sup><br>Target gap {upside*100:.1f}%</span><span class='formula'>({target:.2f} − {price:.2f}) / {price:.2f} = {upside*100:.1f}% <a href='#src-F1'>[F1]</a></span></td><td class='wide'><span class='label judgment'>PM judgment</span>{html.escape(a['actionability'])}<br><span class='label inference'>Inference</span>{html.escape(a['variant'])}</td><td class='wide'>{html.escape(a['why'])}<sup><a href='#src-S-{ticker}'>S-{ticker}</a></sup></td><td class='wide'>{html.escape(a['reject'])}</td><td class='wide'><b>Make investable:</b> {html.escape(a['investable'])}<br><b>Kill:</b> {html.escape(a['kill'])}</td><td class='wide'>{html.escape(a['workflow'])}</td></tr>"


def _idea_card(row: dict[str, Any]) -> str:
    ticker = row["symbol"]
    a = ASSESSMENTS[ticker]
    price = float(row["price"])
    upside = float(row["target_upside_pct"])
    return f"<article class='idea'><h3>{ticker} · {html.escape(row['company'])}</h3><div class='metrics'><span class='pill'>{a['status']}</span><span class='pill'>Score {float(row['score']):.2f}<sup><a href='#src-F1'>F1</a></sup></span><span class='pill'>Close ${price:,.2f}<sup><a href='#src-M-{ticker}'>M-{ticker}</a></sup></span><span class='pill'>Frozen gap {upside*100:.1f}%<sup><a href='#src-F1'>F1</a></sup></span></div><dl><dt>Actionability</dt><dd><span class='label judgment'>PM judgment</span>{html.escape(a['actionability'])}</dd><dt>Variant wedge</dt><dd><span class='label inference'>Inference</span>{html.escape(a['variant'])}</dd><dt>Why now</dt><dd>{html.escape(a['why'])}<sup><a href='#src-S-{ticker}'>S-{ticker}</a></sup></dd><dt>First rejection</dt><dd>{html.escape(a['reject'])}</dd><dt>What makes investable</dt><dd>{html.escape(a['investable'])}</dd><dt>Kill condition</dt><dd>{html.escape(a['kill'])}</dd><dt>Next workflow</dt><dd>{html.escape(a['workflow'])}</dd></dl></article>"


def _source_row(source: dict[str, str]) -> str:
    sid = source["source_id"]
    url = source["url_or_document"]
    link = f"<a href='{html.escape(url)}'>{html.escape(url)}</a>" if url.startswith("http") else html.escape(url)
    return f"<tr id='src-{sid}'><td><b>{sid}</b></td><td>{source['ticker']}</td><td>{html.escape(source['source_owner'])}<br><span class='small'>{html.escape(source['source_type'])} · {html.escape(source['source_tier'])}</span></td><td class='wide'>{link}</td><td>{source['document_date']}<br><span class='small'>{html.escape(source['filing_period'])}<br>as-of {html.escape(source['as_of'])}<br>retrieved {source['retrieved_at']}</span></td><td>{html.escape(source['freshness'])}</td><td class='wide'>{html.escape(source['limitations'])}</td></tr>"


def _money(value: float) -> str:
    if value >= 1_000_000_000_000:
        return f"${value/1_000_000_000_000:.2f}T"
    return f"${value/1_000_000_000:.1f}B"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    rows = load_frozen()
    ledger = build_sources(rows)
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.sources.parent.mkdir(parents=True, exist_ok=True)
    args.sources.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    args.html.write_text(render_html(rows, ledger), encoding="utf-8")
    print(json.dumps({"html": str(args.html), "sources": str(args.sources), "source_count": ledger["source_count"], "candidate_count": len(rows), "scores_preserved": True, "embedded_llm_invoked": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
