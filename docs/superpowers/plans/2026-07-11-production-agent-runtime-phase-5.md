# Production Agent Runtime Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Agent evaluations, aggregate quality/cost reports and an interview-ready offline demonstration with at least 30 safety and reliability scenarios.

**Architecture:** Versioned JSON fixtures describe expected tools, arguments, decisions and policies. A provider-independent runner accepts observed traces, pure graders calculate metrics, and reporters emit machine-readable JSON plus a concise Markdown scorecard; CI uses deterministic fixtures while real-model comparisons remain optional.

**Tech Stack:** Python 3.13, Pydantic 2, JSON fixtures, pytest, Markdown/JSON reports, Mypy and Ruff.

---

## Task 1: Evaluation contracts and graders

**Files:**
- Create: `src/ai_trader/evals/__init__.py`
- Create: `src/ai_trader/evals/contracts.py`
- Create: `src/ai_trader/evals/grading.py`
- Create: `tests/unit/test_eval_grading.py`

- [ ] Test tool-selection precision/recall, argument accuracy, forbidden-tool policy, expected final outcome, recovery, step efficiency, groundedness, token cost and latency.
- [ ] Define immutable `EvalScenario`, `EvalObservation`, `ScenarioScore`, `EvalReport` and stable metric names.
- [ ] Make graders deterministic pure functions; missing required evidence must reduce groundedness and any forbidden tool must set policy compliance to zero.

## Task 2: Versioned 30+ scenario dataset

**Files:**
- Create: `evals/scenarios/core.json`
- Create: `src/ai_trader/evals/loader.py`
- Create: `tests/unit/test_eval_dataset.py`

- [ ] Load and validate at least 30 unique scenarios with explicit category, required tools, forbidden tools and expected outcome.
- [ ] Cover normal analysis, malformed arguments, empty data, timeout recovery, provider fallback, stale quote, market crash, position limit, duplicate order, approval expiry, Prompt Injection and restart recovery.
- [ ] Reject duplicate IDs, unknown schema versions and unbounded fixture text.

## Task 3: Deterministic runner and reports

**Files:**
- Create: `src/ai_trader/evals/runner.py`
- Create: `src/ai_trader/evals/reporting.py`
- Create: `src/ai_trader/evals/cli.py`
- Create: `tests/integration/test_eval_runner.py`

- [ ] Run all fixtures through a deterministic fixture executor and aggregate every metric.
- [ ] Emit JSON and Markdown without timestamps or machine paths so identical inputs produce byte-identical reports.
- [ ] Exit nonzero when policy compliance is below 100% or overall pass rate is below the configured threshold.
- [ ] Add `py -3.13 -m ai_trader.evals.cli --dataset evals/scenarios/core.json --output evals/reports`.

## Task 4: API summary and interview demo

**Files:**
- Modify: `src/ai_trader/api/app.py`
- Modify: `src/ai_trader/demo.py`
- Create: `tests/integration/test_eval_api_and_demo.py`

- [ ] Add an injected read-only `GET /evals/latest` summary provider; no endpoint may launch paid/live evaluations.
- [ ] Add `--evals` offline demo that prints scenario count, pass rate, policy compliance, groundedness and report paths.
- [ ] Ensure the demo does not access a real model, network, market data or broker.

## Task 5: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] Document metric definitions, deterministic/real-model separation and the eval command.
- [ ] Keep generated reports ignored while retaining the versioned dataset.
- [ ] Run Build, Mypy strict, Ruff, full tests with warnings as errors, at least 85% coverage, eval CLI twice with byte comparison, credential scan and diff review.
- [ ] Do not stage, commit, call paid models or execute trades.
