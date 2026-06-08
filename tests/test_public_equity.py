from __future__ import annotations

import datetime as dt
import json

from tradingagents.public_equity import (
    PAYLOAD_KIND,
    build_idea_generation_payload,
    write_idea_generation_payload,
)
from tradingagents.value_discover import ValueCandidate


def _candidate(score: float = 73.0) -> ValueCandidate:
    return ValueCandidate(
        symbol="CHEAP",
        company="Cheap Co",
        sector="Industrials",
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
        rsi_14=42.0,
        avg_volume_20d=2_000_000,
        score=score,
        thesis="forward P/E 8.0; liquid 20D volume",
        caveats="No major quantitative caveat found",
    )


def test_public_equity_payload_matches_dashboard_contract():
    payload = build_idea_generation_payload(
        [_candidate()],
        as_of=dt.datetime(2026, 6, 7, 7, 20),
        markdown_path="reports/value_discover/2026-06-07/value.md",
        csv_path="reports/value_discover/2026-06-07/value.csv",
    )

    assert payload["kind"] == PAYLOAD_KIND
    assert payload["mode"] == "idea_generation"
    assert payload["metadata"]["payload_stage"] == "support"
    assert payload["qa"]["final_recommendation"] is False
    row = payload["tabs"][1]["modules"][0]["rows"][0]
    assert row["ticker"] == "CHEAP"
    assert row["triage_bucket"].startswith("A")
    assert row["next_workflow"] == "company-tearsheet -> thesis-tracker"


def test_public_equity_payload_writer_materializes_json_and_markdown(tmp_path):
    json_path, markdown_path = write_idea_generation_payload(
        [_candidate(score=68.0)],
        as_of=dt.datetime(2026, 6, 7, 7, 20),
        output_dir=tmp_path,
        markdown_path=tmp_path / "value.md",
        csv_path=tmp_path / "value.csv",
    )

    assert json_path.exists()
    assert markdown_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["kind"] == PAYLOAD_KIND
    assert "Public Equity Triage" in markdown_path.read_text(encoding="utf-8")
