# Value Discover Reliability Matrix

Date: 2026-08-02 (America/Los_Angeles)
Repository commit: `3824aa824eb713a6bc9684a660d0fdf856afac7f`
Machine evidence: `reports/value_discover_reliability/2026-08-02/evidence.json`

## Executive finding

Value Discover is safe to use as a **research-only quantitative screen with human review**, but it is not reliable enough to support trading decisions or unattended escalation.

The deterministic scoring, ranking, CSV/Markdown output, and Public Equity routing were reproducible across fixtures. The small public-data canary also succeeded for AAPL and MSFT twice each. However, three production weaknesses prevent a stronger rating:

1. A completed run can silently lose most of its universe. Three existing same-day reports contained `1`, `10`, and `10` candidates while each top-level run was treated as successful. `_screen_symbol` catches every per-symbol exception and records no attempted-symbol count, error class, or coverage threshold.
2. An LLM-disabled run records the `llm_analysis` step as `ok`, even though it was not invoked. This is a status-semantics defect, not evidence of LLM reliability.
3. The configured LLM path—OpenRouter using `google/gemma-4-26b-a4b-it`—was not called and remains intentionally disabled under the current Codex-only trial scope. Live embedded-LLM reliability therefore remains **untested**, but credential absence is not a blocker and no credential request is needed. The unrelated `OPENAI_API_KEY` present in the process was not read or used.

No brokerage, wallet, order, transaction, or paid/model API was accessed.

## Configuration and actual invocation

| Item | Configured | Actually invoked in baseline/harness | Result |
| --- | --- | --- | --- |
| Value Discover LLM flag | `false` | No LLM call | Correctly treated as untested by this audit |
| LLM provider | `openrouter` | No | `OPENROUTER_API_KEY` absent |
| Quick/deep model | `google/gemma-4-26b-a4b-it` for both | No | Official listing exists; local application reliability untested |
| Per-ticker LLM timeout | 180 seconds | Deterministic timeout control only | Worker termination verified; no live provider timing |
| Core stock / indicators / fundamentals / news vendor | `yfinance` | Screen info/history, ticker news, and bounded global news invoked | Small sample passed; production coverage remains conditional |
| Alpha Vantage | Registered fallback | Fixture control flow only | Not a working yfinance failover under current routing |
| StockTwits | Sentiment prefetch | Direct bounded sample invoked twice | Both `ok`; full LLM graph untested |
| Reddit | Sentiment prefetch | Direct bounded r/stocks sample invoked twice | Both `empty`; code cannot distinguish true zero from swallowed fetch failure |

The shared factory supports `openai`, `anthropic`, `google`, `azure`, `xai`, `deepseek`, `qwen`, `qwen-cn`, `glm`, `glm-cn`, `minimax`, `minimax-cn`, `nvidia`, `openrouter`, and local `ollama`. Only OpenRouter is configured for Value Discover. No alternate provider or model fallback is configured; every alternate provider/model is untested for this workflow.

Credential presence was checked by variable name and Boolean only:

- Present: `OPENAI_API_KEY`
- Absent: `OPENROUTER_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `DASHSCOPE_API_KEY`, `DASHSCOPE_CN_API_KEY`, `DEEPSEEK_API_KEY`, `GOOGLE_API_KEY`, `MINIMAX_API_KEY`, `MINIMAX_CN_API_KEY`, `NVIDIA_API_KEY`, `XAI_API_KEY`, `ZHIPU_API_KEY`, `ZHIPU_CN_API_KEY`

No credential value was read, printed, copied, or included in the evidence.

## Reliability matrix

Classification meanings:

- **Verified reliable**: deterministic behavior passed repeatable fixture/schema checks.
- **Conditionally reliable**: bounded evidence passed, but a stated control or coverage gap remains.
- **Unreliable**: current behavior can materially mislead or cannot perform its advertised fallback.
- **Untested**: configured code path was not actually invoked under authorized conditions.

Inventory total: 17 components — 2 verified reliable, 6 conditionally reliable, 3 unreliable, and 6 untested.

| Component | Configured vs invoked | Classification | Allowed use | Evidence / warning | Exact remediation |
| --- | --- | --- | --- | --- | --- |
| Yahoo `Ticker.info` screen | Configured; baseline + live canary invoked | **Conditionally reliable** | Research screen only | AAPL/MSFT passed twice; exceptions silently drop symbols; no source timestamp | Record per-symbol status, source timestamp, retries, latency, and error class |
| Yahoo one-year history | Configured; baseline + live canary invoked | **Conditionally reliable** | Research screen only | Four live calls returned 251 rows in 677–1,335 ms; production has no hard per-symbol timeout | Add hard timeout and minimum universe-coverage gate |
| Deterministic score/rank/top-N | Configured and invoked | **Verified reliable** | Rank one frozen input snapshot | Two fixture runs had identical SHA-256 digest and valid schemas | Version weights; evaluate calibration, turnover, and predictive precision on frozen history |
| Target-upside formula and score bonus | Configured and invoked | **Conditionally reliable** | Triage only after secondary confirmation | Formula is deterministic, but extreme stale targets can inflate scores | Store target source/date/analyst count; cap or winsorize score contribution |
| RSI-14 and 20-day volume | Configured and invoked through history | **Conditionally reliable** | Supporting technical context | Deterministic when history is complete; RSI returns missing when loss denominator is zero/insufficient | Record history coverage and emit typed reason for unavailable RSI |
| Public Equity bucket/workflow routing | Configured and invoked | **Verified reliable** | Research workflow routing only | JSON/CSV/Markdown schemas passed twice | Propagate source-quality and anomaly flags into every routed row |
| Yahoo OHLCV + stockstats LLM tools | Configured; not invoked in the LLM-disabled baseline | **Untested** | Keep disabled | Tool choice, caching, look-ahead guard, and returned text were not exercised in the graph | Fixture tool-call transcript, then one authorized live canary |
| Yahoo fundamentals/statements | Configured; not invoked in graph | **Untested** | Keep disabled | Errors are returned as ordinary strings, which an LLM could mistake for evidence | Return typed envelopes: status, as-of, fields present/missing, source, error |
| Yahoo ticker/global news | Configured; direct adapters invoked, graph not invoked | **Untested** for full graph | Direct research support only | Ticker news and one-query global news each passed twice; no typed provenance/completeness contract | Emit structured article records, query coverage, dates, URLs, and typed failure |
| Yahoo insider transactions | Registered in `tools_news`, not bound by current News Analyst | **Unreliable** | Do not rely on it | Advertised tool-node membership is not reachable from the current analyst tools | Bind it explicitly or remove it from the advertised node |
| StockTwits | Configured; direct adapter passed twice, graph not invoked | **Untested** for full graph | Direct qualitative support only | 162–233 ms; returns placeholder text rather than typed failure | Typed status, sample size, freshness, HTTP/error class, and minimum-evidence gate |
| Reddit | Configured; direct adapter returned empty twice, graph not invoked | **Untested** | Do not treat empty as verified absence | 182–330 ms; true zero and swallowed per-subreddit failure are not distinguishable | Preserve per-subreddit HTTP status, error, latency, and result count |
| Alpha Vantage fallback | Registered; fixture branch invoked, live API not invoked | **Unreliable** as resilience | Do not rely on as yfinance backup | It falls back after `AlphaVantageRateLimitError` only when Alpha Vantage is primary; yfinance failures do not advance to Alpha Vantage | Normalize retryable failures and apply symmetric fallback to every vendor |
| OpenRouter / Gemma 4 26B A4B | Configured; not invoked | **Untested** | Keep disabled | Credential absent; official availability is not local reliability evidence | No embedded-model canary in the current scope; retain disabled unless a future separate task explicitly authorizes it |
| Research/Trader/Portfolio schemas | Configured; fixture parser/fallback invoked, live model not invoked | **Conditionally reliable** | Typed results usable; fallback requires review | Pydantic path passed; structured failure retries once as unvalidated free text | Mark fallback explicitly and reject/hold when required fields are absent |
| Per-ticker hard timeout | Configured; deterministic timeout invoked | **Conditionally reliable** | Fault containment | Test process exceeded the bound and was terminated; no stage attribution | Add monotonic timings and provider/tool/parse stage identifiers |
| `status.json` and report index | Configured and baseline invoked | **Unreliable** as reliability proof | Navigation only | Silent screen loss still produces `ok`; skipped LLM is labeled `ok` | Add `invoked/skipped`, attempted/succeeded/failed counts, coverage gate, and typed degraded state |

## Measured evidence

### Test execution

- Focused command: `uv run --frozen --with pytest pytest -q tests/test_value_discover_reliability.py`
- Result: `11 passed` in 0.40 seconds.
- Full command: `uv run --frozen --with pytest pytest -q`
- Result: `267 passed, 1 skipped, 7 warnings, 76 subtests passed` in 2.93 seconds. The single skip is the existing live DeepSeek test because `DEEPSEEK_API_KEY` is unavailable; warnings concern accepted future/unknown Anthropic model names.
- The live harness completed successfully after the tests and rewrote the machine-readable evidence file.

### Deterministic harness

- Two runs over normal, anomalous, and missing-field fixtures: `3/3` candidates each.
- Cross-run digest: identical.
- ValueCandidate field schema: valid.
- CSV schema, Markdown creation, and Public Equity dashboard schema: valid twice.
- Alpha Vantage rate-limit fixture: `alpha_vantage -> yfinance` fallback executed.
- Structured-output failure fixture: exactly one free-text fallback executed; the fallback is not schema validated.
- Timeout fixture: worker exceeded the 0.1-second bound and was terminated in about 105 ms.

### Bounded public samples

| Sample | Repeats | Status | Latency | Schema / missing fields | Cross-run |
| --- | ---: | --- | --- | --- | --- |
| AAPL info + 1y history | 2 | 2 `ok` | 1,335 ms, 677 ms | Valid; none missing | Identical candidate digest |
| MSFT info + 1y history | 2 | 2 `ok` | 729 ms, 686 ms | Valid; none missing | Identical candidate digest |
| StockTwits AAPL, limit 5 | 2 | 2 `ok` | 192 ms, 307 ms | Untyped text | Identical output digest |
| Reddit AAPL in r/stocks, limit 2 | 2 | 2 `empty` | 169 ms, 163 ms | Untyped text | Identical output digest, but empty is ambiguous |
| Yahoo AAPL ticker news | 2 | 2 `ok` | 494 ms, 373 ms | Untyped text | Identical output digest |
| Yahoo global news, one query/limit 2 | 2 | 2 `ok` | 479 ms, 364 ms | Untyped text | Identical output digest |

Every live sample ran in a separate bounded process. There were zero timeouts and zero worker failures in the final run.

## Anomalous market-data findings

1. **Coverage collapse:** the three existing 2026-08-02 CSVs contained `1`, `10`, and `10` candidates. A single-candidate "successful" run is materially inconsistent with the two immediately following runs. Because per-symbol exceptions are swallowed, the evidence cannot distinguish rate limiting, partial network failure, parser failure, or legitimate filtering. Treat this as a production reliability defect.
2. **Extreme target upside:** MU carried approximately `85.0%` target upside in all three existing reports. Price and market capitalization were internally plausible under the broad implied-share sanity check, but the target signal is extreme and should not earn an uncapped score bonus without independent source/date/coverage confirmation.
3. **Fixture detectors:** the harness correctly detected non-positive/missing market values, an implausible market-cap-to-price implied share count, and a 9,900% target-upside fixture.
4. **No live AAPL/MSFT field anomaly** was detected in the four bounded screen calls. This small sample does not validate the full 100-symbol universe.

## Recommended operating posture

Keep the scheduled Value Discover workflow in its current `LLM_ENABLED=false` mode. Use results only as an evidence-gap queue for human research. Do not treat `status=ok`, score rank, target upside, or Public Equity routing as a trading recommendation.

Disable automatic downstream escalation whenever any of these occurs:

- attempted-symbol coverage is absent or below a defined threshold;
- price, market cap, or target-upside anomaly is critical;
- any required data source returns an untyped error/placeholder;
- the structured decision path falls back to free text;
- LLM execution is skipped, timed out, or lacks complete provenance.

Remediation order:

1. Add per-symbol outcome/latency/error records and a minimum coverage gate.
2. Correct status semantics (`skipped`, `degraded`, `failed`, `ok`) and include `llm_invoked`.
3. Add typed source envelopes and symmetric vendor fallback.
4. Add anomaly gates for target age/coverage and price/market-cap consistency.
5. Use the frozen ten-name output as a Codex-only idea-triage input, preserving scores and routing every candidate through source-backed human research rather than an embedded-model decision path.

## Current Codex-only trial resolution

No credential question is pending. The embedded OpenRouter/Gemma path remains **untested and disabled by design** under the current authorization.

The required completion evidence is the additive ten-candidate Public Equity idea-triage report at `reports/value_discover/2026-08-02/value_discover_idea_triage_20260802.html`, with its machine-readable 27-entry source ledger at `reports/value_discover/2026-08-02/value_discover_idea_triage_sources_20260802.json`. The ledger contains 10 SEC primary-filing anchors, 10 current-close market anchors, and 7 methodology/runtime/local-run sources. The trial preserves the frozen `*_173132.*` scores exactly, labels only research candidate/watchlist/deprioritized, and records Codex based on GPT-5 while stating that the exact deployment model ID/version is unavailable.

Playwright rendered QA passed at desktop `1440x1000` and mobile `390x844`: all six sections and all 27 source rows rendered, mobile document width matched the viewport (`390px`), and the only console error was an immaterial missing `favicon.ico` from the temporary local server.

## External documentation checked

- [yfinance documentation](https://ranaroussi.github.io/yfinance/) — describes yfinance as an unaffiliated research/education tool over Yahoo public APIs and points to Yahoo terms/personal-use limits.
- [OpenRouter API reference](https://openrouter.ai/docs/api/reference/overview) — documents normalized request/response schemas and model-dependent structured outputs.
- [OpenRouter Gemma 4 26B A4B listing](https://openrouter.ai/google/gemma-4-26b-a4b-it) — currently lists function calling and structured output support; this is availability information, not evidence that TradingAgents invoked it successfully.
