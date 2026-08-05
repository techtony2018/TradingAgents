from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "value_discover_reliability.py"
SPEC = importlib.util.spec_from_file_location("value_discover_reliability", MODULE_PATH)
assert SPEC and SPEC.loader
reliability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reliability)

TRIAGE_PATH = Path(__file__).parents[1] / "scripts" / "value_discover_idea_triage.py"
TRIAGE_SPEC = importlib.util.spec_from_file_location("value_discover_idea_triage", TRIAGE_PATH)
assert TRIAGE_SPEC and TRIAGE_SPEC.loader
triage = importlib.util.module_from_spec(TRIAGE_SPEC)
TRIAGE_SPEC.loader.exec_module(triage)


class ValueDiscoverReliabilityTests(unittest.TestCase):
    def test_fixture_harness_is_deterministic_and_schema_valid(self):
        result = reliability.run_fixture_checks()

        self.assertEqual(result["candidate_count_each_run"], [3, 3])
        self.assertTrue(result["cross_run_consistent"])
        self.assertTrue(result["schema_valid"])
        self.assertTrue(all(check["csv_schema_valid"] for check in result["artifact_checks"]))
        self.assertTrue(all(check["public_equity_schema_valid"] for check in result["artifact_checks"]))

    def test_anomaly_checks_cover_price_market_cap_and_target_upside(self):
        findings = reliability.detect_candidate_anomalies(
            {
                "symbol": "BROKEN",
                "price": 10,
                "market_cap": 500,
                "target_upside_pct": 99,
            }
        )
        codes = {finding["code"] for finding in findings}

        self.assertIn("price_market_cap_inconsistency", codes)
        self.assertIn("extreme_target_upside", codes)

    def test_alpha_vantage_rate_limit_fallback_is_exercised(self):
        result = reliability.run_fallback_check()

        self.assertTrue(result["fallback_on_alpha_vantage_rate_limit_verified"])
        self.assertEqual(result["calls"], ["alpha_vantage", "yfinance"])
        self.assertTrue(result["does_not_cover_yfinance_primary_failures"])

    def test_structured_parser_has_unvalidated_freetext_fallback(self):
        result = reliability.run_parser_fallback_check()

        self.assertTrue(result["pydantic_schema_valid"])
        self.assertTrue(result["rendered_rating_present"])
        self.assertTrue(result["structured_failure_falls_back_once"])

    def test_hard_timeout_terminates_worker(self):
        result = reliability.run_timeout_check(0.05)

        self.assertTrue(result["timed_out"])
        self.assertTrue(result["terminated"])

    def test_inventory_distinguishes_configured_and_invoked(self):
        inventory = reliability.build_inventory(
            {
                "llm_provider": "openrouter",
                "quick_think_llm": "google/gemma-4-26b-a4b-it",
            },
            llm_enabled=False,
            credential_present=False,
        )
        by_id = {row["id"]: row for row in inventory}

        self.assertEqual(by_id["openrouter_gemma4"]["classification"], "untested")
        self.assertIn("not invoked", by_id["openrouter_gemma4"]["invoked_evidence"])
        self.assertEqual(by_id["score_model"]["classification"], "verified reliable")
        self.assertEqual(by_id["rsi_volume"]["classification"], "conditionally reliable")
        self.assertEqual(by_id["yfinance_insiders"]["classification"], "unreliable")

    def test_public_adapter_worker_rejects_unknown_source_without_network(self):
        result = reliability._run_public_adapter("unknown")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "ValueError")

    def test_prior_run_coverage_collapse_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            day = tmp_path / "2026-08-02"
            day.mkdir()
            header = ",".join(reliability.REQUIRED_CANDIDATE_FIELDS)
            row = ",".join(["AAPL"] + [""] * (len(reliability.REQUIRED_CANDIDATE_FIELDS) - 1))
            (day / "value_discover_1.csv").write_text(header + "\n" + row + "\n", encoding="utf-8")
            (day / "value_discover_2.csv").write_text(
                header + "\n" + "\n".join([row] * 10) + "\n", encoding="utf-8"
            )

            result = reliability.inspect_prior_runs(tmp_path)

        self.assertEqual(result["candidate_counts"], [1, 10])
        self.assertTrue(result["coverage_collapse_detected"])

    def test_triage_uses_exact_frozen_shortlist_and_scores(self):
        rows = triage.load_frozen()

        self.assertEqual(tuple(row["symbol"] for row in rows), triage.EXPECTED_ORDER)
        self.assertEqual(
            [float(row["score"]) for row in rows],
            [77.0, 71.52, 70.67, 69.23, 66.67, 66.22, 65.31, 63.81, 63.14, 63.08],
        )

    def test_triage_source_ledger_is_complete_and_typed(self):
        rows = triage.load_frozen()
        ledger = triage.build_sources(rows)

        self.assertEqual(ledger["source_count"], 27)
        required = {
            "source_id", "ticker", "source_owner", "source_type", "source_tier",
            "url_or_document", "document_date", "as_of", "filing_period",
            "retrieved_at", "freshness", "limitations", "supports",
        }
        self.assertTrue(all(set(source) == required for source in ledger["sources"]))
        self.assertEqual(sum(source["source_id"].startswith("M-") for source in ledger["sources"]), 10)
        self.assertEqual(sum(source["source_id"].startswith("S-") for source in ledger["sources"]), 10)

    def test_triage_html_has_required_pm_fields_and_runtime_boundary(self):
        rows = triage.load_frozen()
        rendered = triage.render_html(rows, triage.build_sources(rows))

        for phrase in (
            "Candidate funnel", "Actionability", "Variant wedge", "Why now",
            "First rejection", "What makes investable", "Kill condition", "Next workflow",
            "Research candidate", "Watchlist", "Deprioritized", "Codex based on GPT-5",
            "exact deployment model ID/version unavailable", "Source ledger",
        ):
            self.assertIn(phrase, rendered)
        self.assertNotIn("crowded", rendered.lower())
        self.assertIn("no embedded gemma/openrouter call", rendered.lower())


if __name__ == "__main__":
    unittest.main()
