"""Public Equity Investing handoff artifacts.

The Codex Public Equity Investing plugin is skill/artifact driven in this
environment. TradingAgents therefore writes a structured idea-generation
payload that the plugin workflow and the local report UI can consume.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from tradingagents.value_discover import ValueCandidate


PAYLOAD_KIND = "public_equity_investing_dashboard.v1"
PLUGIN_ID = "public-equity-investing"
PLUGIN_VERSION = "0.1.29"

PUBLIC_EQUITY_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "id": "company-tearsheet",
        "label": "Company Tearsheet",
        "token_saver_role": "Materialize issuer facts once so later work reuses a compact baseline.",
        "requires_llm": False,
    },
    {
        "id": "comps-valuation",
        "label": "Comps Valuation",
        "token_saver_role": "Use structured valuation fields before asking for valuation narrative.",
        "requires_llm": False,
    },
    {
        "id": "earnings-preview",
        "label": "Earnings Preview",
        "token_saver_role": "Route estimate-risk questions into an earnings-specific template.",
        "requires_llm": True,
    },
    {
        "id": "catalyst-calendar",
        "label": "Catalyst Calendar",
        "token_saver_role": "Track timing and evidence gaps as rows before generating commentary.",
        "requires_llm": False,
    },
    {
        "id": "thesis-tracker",
        "label": "Thesis Tracker",
        "token_saver_role": "Persist thesis, disconfirming evidence, and kill criteria across runs.",
        "requires_llm": False,
    },
    {
        "id": "long-short-pitch",
        "label": "Long/Short Pitch",
        "token_saver_role": "Escalate only high-upside candidates to model-heavy pitch writing.",
        "requires_llm": True,
    },
    {
        "id": "scenario-sensitivity-generator",
        "label": "Scenario Sensitivity",
        "token_saver_role": "Represent uncertainty as scenario rows before interpretation.",
        "requires_llm": False,
    },
    {
        "id": "portfolio-risk-management",
        "label": "Portfolio Risk Management",
        "token_saver_role": "Summarize liquidity, valuation, and technical flags before sizing.",
        "requires_llm": False,
    },
)


def build_idea_generation_payload(
    candidates: Sequence[ValueCandidate],
    *,
    as_of: dt.datetime,
    markdown_path: Path | str,
    csv_path: Path | str,
    llm_summary_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a Public Equity Investing idea-generation payload.

    The payload is intentionally labeled ``support`` because connected
    institutional source categories are not yet verified inside the Python
    process. It preserves source posture and routes candidates to deeper
    workflows without implying an investment recommendation.
    """
    markdown_path = Path(markdown_path)
    csv_path = Path(csv_path)
    llm_summary = Path(llm_summary_path) if llm_summary_path else None
    rows = [
        _candidate_row(candidate, rank)
        for rank, candidate in enumerate(candidates, start=1)
    ]
    advance_count = sum(1 for row in rows if row["triage_bucket"].startswith("A"))
    watchlist_count = sum(1 for row in rows if row["triage_bucket"].startswith("B"))
    evidence_gap_count = sum(
        1 for row in rows if "needs source-backed exposure" in row["missing_evidence"].lower()
    )

    return {
        "kind": PAYLOAD_KIND,
        "mode": "idea_generation",
        "layout": "single_page",
        "title": f"Value Discover Public Equity Triage - {as_of.strftime('%Y-%m-%d')}",
        "subtitle": "Quantitative undervaluation shortlist routed through the Public Equity Investing idea-generation framework.",
        "issuer": {
            "ticker": "MULTI",
            "name": "Value Discover Shortlist",
            "sector": "Multi-sector",
            "accent_color": "#2563eb",
        },
        "metadata": {
            "payload_stage": "support",
            "plugin_version": PLUGIN_VERSION,
            "freeze_time": as_of.isoformat(),
            "source_posture": "TradingAgents quantitative screen; premium Public Equity source connectors not verified in-process",
            "readiness_label": "Research-priority triage, not recommendation",
            "readiness_posture": "Needs source-backed exposure proof before upgrade to deeper research",
            "citation_policy": "warn",
            "decision_context": "Morning candidate shortlist for day-trade or long-term-investment research triage",
            "plugin_id": PLUGIN_ID,
        },
        "hero": {
            "callout": "Prioritize the highest-scoring undervaluation candidates, then validate exposure proof, expectations risk, catalyst path, and first rejection before deeper work.",
            "primary_status": "candidate triage",
        },
        "snapshot": [
            {"label": "Universe output", "value": len(rows), "unit": "candidates"},
            {"label": "Advance", "value": advance_count, "unit": "names"},
            {"label": "Watchlist", "value": watchlist_count, "unit": "names"},
            {"label": "Evidence gaps", "value": evidence_gap_count, "unit": "names"},
            {"label": "PE routes", "value": _workflow_route_count(rows), "unit": "steps"},
        ],
        "tabs": [
            {
                "id": "screen-summary",
                "label": "Screen Summary",
                "modules": [
                    {
                        "type": "decision_box",
                        "title": "Research posture",
                        "body": "This is a research-priority queue generated from valuation, quality, liquidity, and technical signals. It is not financial advice or an order recommendation.",
                    },
                    {
                        "type": "metric_tiles",
                        "items": [
                            {"label": "Candidates", "value": len(rows)},
                            {"label": "Advance to deeper work", "value": advance_count},
                            {"label": "Watchlist / needs trigger", "value": watchlist_count},
                            {"label": "Source posture", "value": "support"},
                        ],
                    },
                ],
            },
            {
                "id": "candidate-board",
                "label": "Candidate Board",
                "modules": [
                    {
                        "type": "table",
                        "title": "Value Discover candidates",
                        "columns": [
                            "rank",
                            "ticker",
                            "company",
                            "sector",
                            "score",
                            "triage_bucket",
                            "why_now",
                            "expectations_risk",
                            "first_rejection",
                            "next_workflow",
                        ],
                        "rows": rows,
                    }
                ],
            },
            {
                "id": "next-actions",
                "label": "Next Actions",
                "modules": [
                    {
                        "type": "question_list",
                        "title": "First diligence questions",
                        "items": [
                            "What primary source or premium data confirms why this name surfaced now?",
                            "What would disconfirm the valuation signal within one trading day?",
                            "Is the setup investable now, or only after an earnings/catalyst trigger?",
                            "Which candidate should route to company tearsheet, earnings preview, thesis tracker, or long/short pitch?",
                        ],
                    },
                    {
                        "type": "missing_evidence",
                        "title": "Missing evidence",
                        "items": sorted({row["missing_evidence"] for row in rows}),
                    },
                ],
            },
            {
                "id": "workflow-router",
                "label": "Workflow Router",
                "modules": [
                    {
                        "type": "workflow_catalog",
                        "title": "Available Public Equity Investing workflows",
                        "rows": list(PUBLIC_EQUITY_WORKFLOWS),
                    },
                    {
                        "type": "workflow_routes",
                        "title": "Deterministic candidate routing",
                        "columns": [
                            "ticker",
                            "priority",
                            "workflow",
                            "why",
                            "requires_llm",
                            "token_saver_role",
                        ],
                        "rows": _workflow_routes(rows),
                    },
                ],
            },
        ],
        "sources": [
            {
                "id": "value-discover-md",
                "title": "Value Discover Markdown report",
                "type": "tradingagents_report",
                "status": "available",
                "url": str(markdown_path),
                "as_of": as_of.date().isoformat(),
            },
            {
                "id": "value-discover-csv",
                "title": "Value Discover CSV export",
                "type": "tradingagents_report",
                "status": "available",
                "url": str(csv_path),
                "as_of": as_of.date().isoformat(),
            },
            {
                "id": "public-equity-plugin",
                "title": "Public Equity Investing plugin workflow",
                "type": "codex_plugin",
                "status": "installed_skills_available_connectors_unverified",
                "version": PLUGIN_VERSION,
                "as_of": as_of.date().isoformat(),
            },
        ],
        "support_files": {
            "markdown_path": str(markdown_path),
            "csv_path": str(csv_path),
            "llm_summary_path": str(llm_summary) if llm_summary else None,
        },
        "qa": {
            "final_recommendation": False,
            "source_connectors_verified": False,
            "preserves_candidate_queue": True,
            "deterministic_workflow_routing": True,
        },
    }


def write_idea_generation_payload(
    candidates: Sequence[ValueCandidate],
    *,
    as_of: dt.datetime,
    output_dir: Path | str,
    markdown_path: Path | str,
    csv_path: Path | str,
    llm_summary_path: Path | str | None = None,
) -> tuple[Path, Path]:
    report_root = Path(output_dir) / as_of.strftime("%Y-%m-%d")
    report_root.mkdir(parents=True, exist_ok=True)
    stem = f"public_equity_idea_generation_{as_of.strftime('%Y%m%d_%H%M%S')}"
    payload = build_idea_generation_payload(
        candidates,
        as_of=as_of,
        markdown_path=markdown_path,
        csv_path=csv_path,
        llm_summary_path=llm_summary_path,
    )
    json_path = report_root / f"{stem}.json"
    markdown_out = report_root / f"{stem}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown_out.write_text(render_idea_generation_markdown(payload), encoding="utf-8")
    return json_path, markdown_out


def render_idea_generation_markdown(payload: dict[str, Any]) -> str:
    rows = payload["tabs"][1]["modules"][0]["rows"]
    lines = [
        f"# {payload['title']}",
        "",
        payload["subtitle"],
        "",
        "Important: this is research-priority triage, not financial advice and not an order recommendation.",
        "",
        "## Candidate Funnel",
        "",
        "| Rank | Ticker | Company | Score | Bucket | Why Now | First Rejection | Next Workflow |",
        "| ---: | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {rank} | {ticker} | {company} | {score:.1f} | {bucket} | {why_now} | {first_rejection} | {next_workflow} |".format(
                rank=row["rank"],
                ticker=_md(row["ticker"]),
                company=_md(row["company"]),
                score=float(row["score"]),
                bucket=_md(row["triage_bucket"]),
                why_now=_md(row["why_now"]),
                first_rejection=_md(row["first_rejection"]),
                next_workflow=_md(row["next_workflow"]),
            )
        )
    lines.extend(
        [
            "",
            "## Source Posture",
            "",
            payload["metadata"]["source_posture"],
            "",
            "## Token-Saving Public Equity Routes",
            "",
            "| Ticker | Priority | Workflow | Why | LLM Needed |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    workflow_rows = payload["tabs"][3]["modules"][1]["rows"]
    for route in workflow_rows:
        lines.append(
            "| {ticker} | {priority} | {workflow} | {why} | {requires_llm} |".format(
                ticker=_md(route["ticker"]),
                priority=_md(route["priority"]),
                workflow=_md(route["workflow"]),
                why=_md(route["why"]),
                requires_llm="yes" if route["requires_llm"] else "no",
            )
        )
    lines.extend(
        [
            "",
            "These routes are deterministic Public Equity workflow handoffs. Use them to reuse structured context and call LLMs only for judgment-heavy steps.",
            "",
            "## Missing Evidence",
            "",
        ]
    )
    for item in payload["tabs"][2]["modules"][1]["items"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _candidate_row(candidate: ValueCandidate, rank: int) -> dict[str, Any]:
    bucket = _triage_bucket(candidate.score)
    return {
        "rank": rank,
        "ticker": candidate.symbol,
        "company": candidate.company,
        "sector": candidate.sector,
        "score": candidate.score,
        "price": candidate.price,
        "forward_pe": candidate.forward_pe,
        "price_to_book": candidate.price_to_book,
        "ev_to_ebitda": candidate.ev_to_ebitda,
        "target_upside_pct": candidate.target_upside_pct,
        "rsi_14": candidate.rsi_14,
        "avg_volume_20d": candidate.avg_volume_20d,
        "triage_bucket": bucket,
        "archetype": "long candidate / value screen",
        "exposure_proof": candidate.thesis,
        "why_now": candidate.thesis,
        "expectations_risk": _expectations_risk(candidate),
        "first_rejection": _first_rejection(candidate),
        "what_would_make_it_investable": "Source-backed earnings durability, catalyst timing, and valuation support.",
        "what_would_kill_it": "Broken fundamentals, value trap evidence, weak liquidity, or adverse near-term catalyst.",
        "next_workflow": _next_workflow(candidate),
        "workflow_routes": _candidate_workflow_routes(candidate, bucket),
        "missing_evidence": "Needs source-backed exposure proof, consensus/estimate context, ownership/positioning, and catalyst validation.",
        "raw_candidate": asdict(candidate),
    }


def _triage_bucket(score: float) -> str:
    if score >= 72:
        return "A - immediate research candidate"
    if score >= 66:
        return "B - watchlist / needs trigger"
    if score > 0:
        return "C - screen flag only"
    return "Reject"


def _expectations_risk(candidate: ValueCandidate) -> str:
    risks = []
    if candidate.price_to_book is not None and candidate.price_to_book >= 6:
        risks.append("valuation-gated")
    if candidate.rsi_14 is not None and candidate.rsi_14 >= 65:
        risks.append("near-term overextension")
    if candidate.target_upside_pct is None:
        risks.append("missing target-price context")
    return ", ".join(risks) if risks else "needs consensus and positioning check"


def _first_rejection(candidate: ValueCandidate) -> str:
    if candidate.avg_volume_20d is not None and candidate.avg_volume_20d < 1_000_000:
        return "Liquidity may be too thin for day-trade workflow."
    if candidate.caveats and "No major" not in candidate.caveats:
        return candidate.caveats
    return "Value trap or stale data after source-backed review."


def _next_workflow(candidate: ValueCandidate) -> str:
    routes = _candidate_workflow_routes(candidate, _triage_bucket(candidate.score))
    return " -> ".join(route["workflow"] for route in routes[:3])


def _candidate_workflow_routes(candidate: ValueCandidate, bucket: str) -> list[dict[str, Any]]:
    routes = [
        _route(
            candidate,
            "company-tearsheet",
            "P0",
            "Build the compact issuer baseline before any model-heavy analysis.",
        ),
        _route(
            candidate,
            "comps-valuation",
            "P0",
            "Reuse existing valuation multiples from the quantitative screen.",
        ),
        _route(
            candidate,
            "portfolio-risk-management",
            "P1",
            "Check liquidity, valuation, and technical risk before sizing or trading.",
        ),
    ]
    if bucket.startswith("A"):
        routes.append(
            _route(
                candidate,
                "thesis-tracker",
                "P1",
                "High-score candidates should persist thesis, kill criteria, and open evidence gaps.",
            )
        )
    if candidate.target_upside_pct is not None and candidate.target_upside_pct >= 0.25:
        routes.append(
            _route(
                candidate,
                "long-short-pitch",
                "P2",
                "High target upside merits a pitch outline only after baseline and valuation checks.",
            )
        )
        routes.append(
            _route(
                candidate,
                "scenario-sensitivity-generator",
                "P2",
                "Upside depends on scenario assumptions that can be tabulated first.",
            )
        )
    if candidate.target_upside_pct is None or "missing" in _expectations_risk(candidate).lower():
        routes.append(
            _route(
                candidate,
                "earnings-preview",
                "P2",
                "Missing consensus or expectations context should route to earnings-specific questions.",
            )
        )
    if candidate.rsi_14 is not None or candidate.caveats:
        routes.append(
            _route(
                candidate,
                "catalyst-calendar",
                "P2",
                "Timing, technical setup, and rejection risks should be tracked as catalyst gates.",
            )
        )
    return routes


def _route(candidate: ValueCandidate, workflow_id: str, priority: str, why: str) -> dict[str, Any]:
    workflow = _workflow(workflow_id)
    return {
        "ticker": candidate.symbol,
        "priority": priority,
        "workflow": workflow_id,
        "workflow_label": workflow["label"],
        "why": why,
        "requires_llm": workflow["requires_llm"],
        "token_saver_role": workflow["token_saver_role"],
    }


def _workflow(workflow_id: str) -> dict[str, Any]:
    for workflow in PUBLIC_EQUITY_WORKFLOWS:
        if workflow["id"] == workflow_id:
            return workflow
    raise KeyError(workflow_id)


def _workflow_routes(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [route for row in rows for route in row.get("workflow_routes", [])]


def _workflow_route_count(rows: Sequence[dict[str, Any]]) -> int:
    return sum(len(row.get("workflow_routes", [])) for row in rows)


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
