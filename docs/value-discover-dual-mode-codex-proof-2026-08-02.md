# Value Discover dual-mode Codex feasibility proof

Date: 2026-08-02 America/Los_Angeles
Canonical task: `tasks/328e6994-277a-43a4-870b-631fd4a6be78`
Workspace baseline: commit `3824aa824eb713a6bc9684a660d0fdf856afac7f` with pre-existing dirty/untracked work preserved

## Decision

**Feasible as an orchestration-stage alternative; not feasible as a drop-in LangChain model client.**

The installed Codex CLI can run non-interactively, reuse the current ChatGPT login, accept a JSON Schema with `--output-schema`, write its final response separately with `-o`, emit JSONL lifecycle events with `--json`, run in a read-only sandbox, and avoid persisting session rollout files with `--ephemeral`. Those capabilities are sufficient for a bounded candidate-analysis adapter after the deterministic shortlist is produced.

The existing TradingAgents graph expects Python LangChain chat-model behavior: it constructs quick and deep clients, binds tools, invokes graph nodes repeatedly, and uses `with_structured_output` with a free-text fallback in the portfolio manager. `codex exec` is a process-level orchestration surface, not a Python `BaseChatModel`; adapting it as a graph client would require emulating LangChain messages, tool binding, streaming/invocation, callbacks, and structured-output behavior. That is neither a drop-in change nor the smallest safe design.

Primary official evidence:

- [OpenAI Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode.md): `codex exec`, read-only sandboxing, JSONL events, `--output-schema`, `-o`, authentication reuse, `--ephemeral`, and session resume.
- [OpenAI Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-exec): current command and flag contract.

Local implementation evidence:

- `tradingagents/value_discover.py:105` retains the current `TradingAgentsGraph` candidate runner.
- `tradingagents/graph/trading_graph.py:86` creates provider-specific quick/deep LangChain clients.
- `tradingagents/agents/managers/portfolio_manager.py:24` binds LangChain structured output and retains a free-text fallback.
- `tradingagents/value_discover_analysis.py` implements the isolated dual-mode proof contract.

## Dual-mode architecture

```text
deterministic shortlist
        |
        v
candidate_analysis_input.v1 + source IDs + research-only constraints
        |
        +--> mode=embedded --> existing TradingAgentsGraph/OpenRouter/Gemma
        |                       --> normalize legacy result
        |
        +--> mode=codex ------> codex exec (ephemeral, read-only, schema-bound)
                                --> validate result + execution receipt
        |
        v
candidate_analysis_output.v1 + batch status + JSON ledger + Markdown summary
```

There is no fallback edge between the two active modes. A Codex timeout, CLI failure, or invalid output remains observable as that Codex failure and does not silently consume OpenRouter tokens. The direct-model path remains intact.

## Configuration contract

The proof module and `value_discover.main()` implement these contracts. The daily automation retains its existing research-only disabled behavior until Tony separately authorizes a production rollout.

| Setting | Values | Behavior |
| --- | --- | --- |
| `TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_MODE` | `disabled`, `embedded`, `codex` | Explicit backend. Any other value is a configuration error. |
| `TRADINGAGENTS_VALUE_DISCOVER_ANALYSIS_TIMEOUT_SECONDS` | positive integer; default `180` | Per-candidate hard timeout shared by either active adapter. |
| legacy `TRADINGAGENTS_VALUE_DISCOVER_LLM_ENABLED` | true-like / false-like | Used only when explicitly present and explicit mode is absent: true-like maps to `embedded`; false-like maps to `disabled`. |

Default: `codex` when neither setting is present. Tony selected this default explicitly. Explicit mode always wins. No `auto` value exists, because `auto` would make cost and provider selection unobservable.

## Shared contracts

Input version: `value_discover.candidate_analysis_input.v1`

- stable analysis ID, date and rank;
- complete frozen `ValueCandidate` fields plus a `financial_facts` map that gives every numeric field a canonical source ID and as-of date, or an explicit unknown marker;
- source ledger entries with `source_id`, source type, location, as-of date and retrieval time;
- research-only constraints and allowed triage labels.

Output version: `value_discover.candidate_analysis_output.v1`

- exact identity: contract version, analysis ID, symbol and mode;
- status: `ok`, `error`, `timeout`, or `invalid_output`;
- classification: `research_candidate`, `watchlist`, `deprioritized`, or compatibility-only `unknown`;
- summary, typed claims (`Fact`, `Estimate`, `Inference`, `PM judgment`), source IDs, data gaps, runtime provenance, and nullable error;
- `additionalProperties: false`, exact key validation, source-ID validation, and consistent `status/error` semantics.

Batch status is `skipped` for disabled, `ok` for all successful, `partial` for mixed success/failure, or the single terminal failure class when all items fail identically.

## Bounded no-paid-model proof

The proof used the frozen MU row from `reports/value_discover/2026-08-02/value_discover_20260802_173132.csv` plus source IDs `shortlist-csv`, `M-MU`, and `S-MU`. It did not rerun the quantitative screen or modify its scores.

Invocation safety posture:

- installed runtime: `codex-cli 0.146.0-alpha.9.2`;
- authentication readback: `Logged in using ChatGPT`;
- `codex exec --ephemeral --ignore-user-config --json --sandbox read-only --output-schema ... -o ... -C ... -`;
- hard timeout: 180 seconds;
- no OpenRouter/Gemma call, key request, credential copy, browsing, account access, order preparation, transaction, commit, push, or production-mode automation rollout;
- the Codex subprocess environment removed `OPENAI_API_KEY`, the only listed provider credential present in the parent proof process; the value was never read or logged.

Verified result:

- return code `0`, `timed_out=false`;
- batch/result status `ok`;
- MU classification `watchlist` (research-priority label, not a recommendation);
- 6 typed claims, 8 explicit data gaps, and all provenance source IDs limited to the supplied three-entry source registry;
- runtime provenance overwritten by the orchestrator from the actual local CLI version, not trusted from model text;
- expected/attempted counts both `1`, proving the bounded one-candidate scope;
- transient execution trace ID `019fc5bc-7f65-77e0-ad1c-2bb696fbd025`. Because `--ephemeral` was used, this is an execution receipt rather than a persisted new top-level Codex task/thread.

Artifacts:

- `reports/value_discover_codex_proof/2026-08-02/analysis_output.schema.json`
- `reports/value_discover_codex_proof/2026-08-02/inputs/01_MU.json`
- `reports/value_discover_codex_proof/2026-08-02/outputs/01_MU.json`
- `reports/value_discover_codex_proof/2026-08-02/outputs/01_MU.events.jsonl`
- `reports/value_discover_codex_proof/2026-08-02/outputs/01_MU.receipt.json`
- `reports/value_discover_codex_proof/2026-08-02/analysis_results.json`
- `reports/value_discover_codex_proof/2026-08-02/summary.md`

## Embedded-mode compatibility

The existing OpenRouter/Gemma path is preserved without changing `value_discover.py` or `TradingAgentsGraph`. The adapter can normalize its current `LLMAnalysisResult` into the shared output schema while retaining `executor=TradingAgentsGraph` and the explicit provider/model runtime.

One semantic gap remains intentional: the legacy graph emits portfolio-style Buy/Hold/Sell decisions and can fall back from provider-native structured output to free text. The proof therefore maps legacy classification to `unknown`; it does not silently reinterpret a trading decision as a research-candidate label. A later activation patch should either add an embedded research-triage schema at the graph boundary or continue to expose this compatibility warning.

## Tests and verified failure semantics

`tests/test_value_discover_analysis.py` covers:

- safe explicit/default/legacy mode resolution;
- shared input and strict output contracts;
- positive timeout parsing and deterministic batch status;
- ephemeral, read-only, schema-bound Codex command construction;
- execution events and receipt capture;
- orchestrator-owned runtime provenance;
- terminal timeout with no embedded fallback;
- embedded-result normalization;
- contradictory `status=ok` plus non-null error rejected as `invalid_output`.

## Activation decision

The bounded proof and scheduled-module integration are complete, but **production automation rollout still needs Tony's separate approval**. The existing fixed automation has been rebound to the Agent Tammy - Value Discovery task without changing its research execution mode.

Recommended activation gate:

1. keep Tony's selected `codex` default and the fail-fast one-process-per-candidate boundary;
2. define an embedded research-triage schema or retain the explicit `unknown` compatibility label;
3. run the full suite and inspect the bounded proof receipt/ledger;
4. separately approve updating the existing automation's execution mode; do not create an overlapping scheduler;
5. after rollout, read back the automation and verify its first weekday run from `status.json` and `reports/index.json`.
