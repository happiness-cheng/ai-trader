# Scheduler Cutover and Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production Runtime selectable by the existing scheduler, add an explicitly gated online smoke command, and automate repeatable preflight verification without changing the default legacy behavior.

**Architecture:** The scheduler gets a third `production` branch that imports the new Runtime lazily per scheduled run. Online smoke testing requires both production mode and an explicit acknowledgement flag; CI runs only offline build/type/lint/test/eval checks on Windows.

**Tech Stack:** Python 3.13, existing runtime, pytest, GitHub Actions Windows runner.

---

## Task 1: Scheduler production branch

**Files:**
- Modify: `main.py`
- Modify: `src/ai_trader/runtime.py`
- Create: `tests/unit/test_runtime_mode.py`

- [ ] Add pure `select_runtime_mode` validation for `pipeline`, `agent` and `production`; reject unknown values.
- [ ] Add `analyze_and_trade_production` to the scheduler using lazy imports and guaranteed repository close.
- [ ] Keep `pipeline` as the default and preserve the existing `agent` branch.

## Task 2: Explicit online smoke gate

**Files:**
- Modify: `src/ai_trader/runtime.py`
- Create: `tests/unit/test_online_smoke_gate.py`

- [ ] Add `--smoke-online --allow-network` and refuse execution unless runtime mode is production, a credential exists and the explicit network flag is present.
- [ ] Limit smoke input to one model turn with no trading tools and print provider/model/token/cache metadata.
- [ ] Test all refusal paths with no network; do not run the allowed path in this task.

## Task 3: CI and preflight

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/preflight.ps1`
- Modify: `README.md`

- [ ] CI installs `.[test]`, runs compileall, Mypy, Ruff, pytest with ResourceWarnings as errors, dry-run Runtime and deterministic Eval CLI.
- [ ] PowerShell preflight performs the same commands and exits on the first failure.
- [ ] Document rollback, production opt-in and the fact that online smoke incurs an API request.

## Task 4: Final verification

- [ ] Run all offline checks, scheduler branch source assertion, dry-run, eval hash comparison and secret scan.
- [ ] Do not execute online smoke, trade, stage, commit or push.
