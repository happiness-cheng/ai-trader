# Production Agent Runtime Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Agent state and append-only Trace events so interrupted tasks can be inspected, cancelled and safely resumed without replaying completed steps.

**Architecture:** Immutable run/event contracts define the state machine. A SQLAlchemy repository stores the current snapshot and append-only events in one transaction with optimistic version checks; a small orchestrator validates transitions and persists intent before the next side effect.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy 2, SQLite, pytest, Mypy and Ruff.

---

## Task 1: Run and event contracts

**Files:**
- Create: `src/ai_trader/agents/__init__.py`
- Create: `src/ai_trader/agents/state.py`
- Create: `tests/unit/test_agent_state.py`

- [ ] Write failing tests for the states `CREATED`, `PLANNING`, `TOOL_RUNNING`, `WAITING_APPROVAL`, `EXECUTING`, `VERIFYING`, `SUCCEEDED`, `FAILED`, `CANCELLED`.
- [ ] Verify legal forward transitions pass; terminal states and skipped transitions fail with `InvalidTransition`.
- [ ] Define immutable `AgentRun`, `RunEvent`, `RunState`, `EventType` and `transition(run, target, now)`.
- [ ] Require timezone-aware timestamps, monotonically increasing versions and bounded event payloads.

## Task 2: Transactional SQLite event repository

**Files:**
- Create: `src/ai_trader/persistence/__init__.py`
- Create: `src/ai_trader/persistence/runs.py`
- Create: `tests/integration/test_run_repository.py`

- [ ] Write failing tests for run creation, atomic state/event append, ordered Trace loading, duplicate run rejection and stale-version conflict.
- [ ] Implement SQLAlchemy tables `agent_runs` and `run_events`; store timestamps in normalized UTC ISO format and payloads as JSON.
- [ ] Implement `create`, `get`, `append_transition`, `events` and `list_recoverable` on `SqlRunRepository`.
- [ ] In `append_transition`, update with `WHERE version = expected_version`; zero updated rows must raise `VersionConflict` and append no event.

## Task 3: Persistent orchestrator, cancellation and recovery

**Files:**
- Create: `src/ai_trader/agents/orchestrator.py`
- Create: `tests/integration/test_persistent_orchestrator.py`

- [ ] Write failing tests for create/start, pause for approval, resume, cancel, terminal cancellation rejection and reconstruction with a second repository instance.
- [ ] Implement `PersistentOrchestrator.create_run`, `transition`, `request_approval`, `resume_approved`, `cancel` and `recoverable_runs`.
- [ ] Persist `INTENT_RECORDED` before entering `TOOL_RUNNING` or `EXECUTING`; no executor or model call belongs in this phase.
- [ ] Ensure a restart reads the latest version and never synthesizes missing events.

## Task 4: Trace redaction and offline recovery demo

**Files:**
- Create: `src/ai_trader/observability/__init__.py`
- Create: `src/ai_trader/observability/redaction.py`
- Create: `tests/unit/test_trace_redaction.py`
- Modify: `src/ai_trader/demo.py`

- [ ] Write failing tests that redact API keys, bearer tokens, Feishu webhook paths and nested sensitive dictionary fields without mutating input.
- [ ] Implement recursive `redact_payload` with bounded strings and stable `[REDACTED]` markers.
- [ ] Add `--recovery` demo: create run, persist transitions, close repository, reopen database, print recovered state/version/event count, then remove only its temporary database.

## Task 5: Dependencies, documentation and verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `README.md`

- [ ] Add `SQLAlchemy>=2.0,<3` to runtime dependencies and legacy requirements.
- [ ] Document state transitions, optimistic concurrency and `py -3.13 -m ai_trader.demo --recovery`.
- [ ] Run Build, Mypy strict, Ruff, pytest with at least 85% aggregate coverage, both offline demos, dependency check, credential scan and diff review.
- [ ] Do not stage, commit, migrate an external database or touch user data.

## Deferred to Phase 4

FastAPI control-plane endpoints, persisted trade proposals/approvals/orders, broker reconciliation and the disabled-by-default THS adapter are not part of this phase.
