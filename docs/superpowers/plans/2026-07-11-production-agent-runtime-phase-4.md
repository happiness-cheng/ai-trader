# Production Agent Runtime Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist proposals, approvals and orders, expose a safe approval control plane, and execute approved proposals exactly once through a paper broker while live THS execution remains fail-closed.

**Architecture:** A transactional trading repository owns proposal/approval/order records and idempotency keys. `ExecutionCoordinator` composes RiskGate, approval records, PersistentOrchestrator and BrokerAdapter; FastAPI endpoints call the coordinator but never import or invoke GUI trading code directly.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite, httpx TestClient, pytest, Mypy and Ruff.

---

## Task 1: Persist proposals, approvals and orders

**Files:**
- Create: `src/ai_trader/persistence/trading.py`
- Create: `tests/integration/test_trading_repository.py`

- [ ] Test proposal round-trip, one active approval per proposal, approval expiry, atomic idempotency reservation, order round-trip and duplicate reservation conflicts.
- [ ] Implement SQLAlchemy tables `trade_proposals`, `approvals` and `orders` using Decimal-as-string and UTC ISO timestamps.
- [ ] Expose `save_proposal`, `get_proposal`, `record_approval`, `get_valid_approval`, `reserve_order`, `save_order` and `get_order_by_proposal`.
- [ ] Redact approval metadata before persistence and reject approval whose `proposal_id` does not match.

## Task 2: Execution coordinator and reconciliation

**Files:**
- Create: `src/ai_trader/execution/coordinator.py`
- Create: `tests/integration/test_execution_coordinator.py`

- [ ] Test `submit_proposal` persists RiskGate outcome, `approve_and_execute` binds reviewer/proposal, duplicate execution returns one order, expired approval/proposal fails, and broker/result reconciliation updates order state.
- [ ] Implement coordinator methods `submit_proposal`, `approve`, `execute_approved`, `approve_and_execute` and `reconcile`.
- [ ] Require an `APPROVE` risk decision and valid persisted human approval before broker submission.
- [ ] Reserve `proposal_id` before broker side effects; on ambiguous failure leave reservation as `execution_unknown` for reconciliation instead of retrying.

## Task 3: Safe FastAPI control plane

**Files:**
- Create: `src/ai_trader/api/__init__.py`
- Create: `src/ai_trader/api/app.py`
- Create: `tests/integration/test_control_plane_api.py`

- [ ] Test health, create run, inspect run/Trace, submit proposal, approve, reject, cancel, duplicate approval and malformed request responses.
- [ ] Build `create_app(dependencies)` so tests inject temporary repositories and Paper Broker.
- [ ] Require `X-Reviewer-Id` for approval/rejection, return 409 for state/version conflicts and never return secrets or raw exception traces.
- [ ] Ensure no API endpoint imports `ths_trader` or exposes direct buy/sell/cancel-order operations.

## Task 4: Disabled-by-default THS adapter boundary

**Files:**
- Create: `src/ai_trader/execution/ths.py`
- Create: `tests/unit/test_ths_adapter.py`

- [ ] Test construction and submission fail when `live_execution_allowed` is false, and importing the adapter does not import legacy `ths_trader`.
- [ ] Lazily import `THSTrader` only inside an explicitly enabled factory.
- [ ] Mark acknowledgement as unverified until a broker-order query confirms it; do not treat a GUI click as `FILLED`.

## Task 5: Documentation and verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `README.md`

- [ ] Add FastAPI/httpx test dependencies, document the control-plane boundary and paper-only API example.
- [ ] Run Build, Mypy strict, Ruff, full pytest with ResourceWarning as errors and at least 85% aggregate coverage, API user-path tests, security scan and diff review.
- [ ] Do not start the legacy dashboard, connect to THS, migrate external databases, stage or commit.

## Deferred to Phase 5

Agent Eval fixtures, graders, aggregate quality/cost metrics and Trace UI remain Phase 5 work.
