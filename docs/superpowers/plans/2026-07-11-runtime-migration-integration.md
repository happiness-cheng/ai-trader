# Runtime Migration and Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the production runtime to an official Anthropic SDK provider, lazily adapted legacy read tools and a persistent manual Agent loop while preserving the legacy entrypoint as rollback.

**Architecture:** `AnthropicProvider` translates provider-neutral requests to official SDK Messages calls with prompt caching and structured tools. A lazy legacy adapter imports market modules only when handlers execute; `ProductionAgentRunner` manually controls model/tool turns, persists every state transition and stops at approval boundaries.

**Tech Stack:** Python 3.13, official `anthropic` SDK, Pydantic 2, existing Model Gateway/Tool Runtime/SQLite Orchestrator, pytest, Mypy and Ruff.

---

## Task 1: Official Anthropic SDK provider

**Files:**
- Create: `src/ai_trader/models/anthropic_provider.py`
- Create: `tests/unit/test_anthropic_provider.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] Test request translation, deterministic tool ordering, `strict: true`, prompt caching, text/tool-use response normalization, token/cache usage and typed SDK error mapping with a fake SDK client.
- [ ] Use `anthropic.Anthropic`, SDK `messages.create`, SDK content block attributes and SDK exception classes; do not add raw HTTP or OpenAI-compatible shims.
- [ ] Set SDK retries to zero because Model Gateway owns provider fallback/retry policy.
- [ ] Preserve configured MiMo-compatible `base_url` and model; do not overwrite a user-selected model with a Claude model.

## Task 2: Lazy legacy read-tool adapters

**Files:**
- Create: `src/ai_trader/tools/legacy_market.py`
- Create: `tests/unit/test_legacy_market_tools.py`

- [ ] Register only read/compute tools in the first migration slice: market overview, quote, technical indicators, positions and RAG search.
- [ ] Test imports are lazy, Pydantic rejects malformed symbols, permissions hide unavailable tools and fixture handlers return typed outputs.
- [ ] Do not expose notification, price-alert, buy, sell or cancel operations to the model.

## Task 3: Persistent production Agent loop

**Files:**
- Create: `src/ai_trader/agents/runner.py`
- Create: `tests/integration/test_production_agent_runner.py`

- [ ] Use a manual loop so tool calls pass Tool Runtime permission/approval gates and all turns are persisted.
- [ ] Transition `CREATED -> PLANNING -> TOOL_RUNNING -> PLANNING -> SUCCEEDED`, persist intent before tools, enforce maximum turns and mark provider/tool failure as `FAILED` without retrying side effects.
- [ ] Stop at `WAITING_APPROVAL` for any approval-required tool; never auto-approve.
- [ ] Preserve full structured model/tool content needed by the next request and emit an `EvalObservation` from the actual run trace.

## Task 4: New opt-in entrypoint and real-trace eval adapter

**Files:**
- Create: `src/ai_trader/runtime.py`
- Create: `tests/integration/test_runtime_entrypoint.py`
- Modify: `README.md`

- [ ] Build runtime dependencies from Settings, with `AI_TRADER_RUNTIME_MODE=production` as an explicit opt-in and legacy mode as default rollback.
- [ ] Add `--dry-run` using Fake Provider and fixture tools; no API/network/THS access.
- [ ] Add a `ScenarioExecutor` adapter that converts completed persistent run traces into real `EvalObservation` records.
- [ ] Document that real-model eval results, not the contract baseline, are the only model-quality evidence.

## Task 5: Full verification

- [ ] Run Build, Mypy strict, Ruff, all tests with warnings as errors and at least 85% aggregate coverage.
- [ ] Run dry-run entrypoint and deterministic evals; verify no network or trading imports.
- [ ] Scan credentials, direct trading exposure and diff scope.
- [ ] Do not call a real model, start THS, stage or commit.
