# Value Discover analysis modes

Date: 2026-08-02 (America/Los_Angeles)

## Decision

Value Discover has one deterministic screen and two explicitly selectable analysis backends:

| Mode | Selection | Executor | External model API | Fallback |
| --- | --- | --- | --- | --- |
| `codex` | Default when no mode or legacy flag is set | Installed `codex exec` using the current Codex account | No provider API key is passed | None; first failure stops the batch |
| `embedded` | `TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE=embedded` or explicit CLI `--analysis-mode embedded` | Existing `TradingAgentsGraph` path | Yes; configured provider/model is invoked | None |
| `disabled` | Explicit mode or legacy `TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED=false` | No candidate-analysis backend | No | Not applicable |

The existing OpenRouter/Gemma configuration is preserved for embedded mode. It is never selected implicitly and is not a Codex fallback.

## Shared contract

Both active backends consume `value_discover.candidate_analysis_input.v1` and emit `value_discover.candidate_analysis_output.v1`.

Each input contains:

- stable analysis ID, date, rank, and candidate identity;
- the frozen deterministic-screen candidate record;
- a `financial_facts` map covering price, market cap, valuation, profitability, leverage, target-upside, technical, liquidity, and score fields;
- for every financial fact, a value plus source IDs and as-of date, or an explicit unknown reason;
- canonical source records and research-only/no-transaction constraints.

Each output contains classification, summary, typed claims, source IDs, data gaps, executor/runtime provenance, status, and bounded error detail. Allowed classifications are research candidate, watchlist, deprioritized, or unknown. They are research-priority labels, not trading recommendations.

## Codex execution boundary

The Codex adapter uses the officially documented non-interactive interface:

```text
codex exec --ephemeral --ignore-user-config --json --sandbox read-only \
  --output-schema <schema> -o <output> -C <project> -
```

The adapter removes external-model credential variables from the child environment, including OpenRouter, OpenAI API, Codex API-key override, Anthropic, Google, Azure, DeepSeek, Qwen/DashScope, GLM/Zhipu, MiniMax, NVIDIA, xAI, and Alpha Vantage keys. Saved Codex CLI account authentication remains owned by Codex itself; no credential value is read, copied, logged, or written to a report.

Codex failures are terminal for the batch. The ledger and status report the expected candidate count, actual attempted count, successful count, failure count, failing status, and provider provenance. No embedded adapter is called after a Codex error, timeout, or invalid structured output.

## Status and report provenance

The scheduled module and CLI write these fields to the daily `status.json`, with the same fields propagated into `reports/index.json`:

- `analysis_mode`
- `analysis_status`
- `analysis_provider_provenance`
- `analysis_expected_count`
- `analysis_attempted_count`
- `analysis_success_count`
- `analysis_error_count`
- `analysis_summary`
- `analysis_results`
- `analysis_output_schema`

`analysis_provider_provenance.external_model_api_invoked` is `false` for Codex and disabled modes. Embedded mode records the configured provider and unique model IDs and sets it to `true`.

## Verification

Focused contract and integration tests:

```bash
uv run --frozen --with pytest pytest -q \
  tests/test_value_discover_analysis.py \
  tests/test_value_discover.py \
  tests/test_report_ui.py
```

Full repository tests:

```bash
uv run --frozen --with pytest pytest -q
```

A live Codex proof must be limited to a frozen candidate envelope, must leave provider credentials unset or stripped, and must preserve the execution receipt, JSONL events, normalized result, schema, summary, and batch ledger. It must not access a brokerage, wallet, financial account, order, or transaction.

## Production rollout boundary

Implementation and proof do not authorize production automation activation. The current `tammy-value-discover` heartbeat must retain its existing research-only behavior until Tony separately authorizes rollout. At rollout time, update that existing automation rather than creating an overlapping scheduler, make the resolved Codex mode explicit, preserve failed-runs-only notifications and fixed-task reporting, and read back the automation after the supported update.
