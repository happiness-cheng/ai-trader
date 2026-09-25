# Production Agent Runtime Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace prompt-parsed tool execution and direct model HTTP calls with typed, permission-aware Tool Runtime and a provider-neutral Model Gateway.

**Architecture:** New runtime modules live under `src/ai_trader` and remain independent from legacy market and GUI modules. Tools expose Pydantic input/output contracts plus risk metadata; the gateway consumes a provider protocol and returns normalized responses, usage and errors so the persistent orchestrator can be added in Phase 3.

**Tech Stack:** Python 3.13, Pydantic 2, protocols, dataclasses, concurrent futures, pytest, Mypy and Ruff.

---

## Task 1: Typed Tool Runtime contracts

**Files:**
- Create: `src/ai_trader/tools/__init__.py`
- Create: `src/ai_trader/tools/runtime.py`
- Create: `tests/unit/test_tool_runtime.py`

- [ ] Write failing tests proving valid input execution, unknown tool rejection, Pydantic argument validation, read/write permission enforcement, human-approval enforcement, structured handler errors and timeout errors.
- [ ] Run `py -3.13 -m pytest tests/unit/test_tool_runtime.py -v`; expect import failure.
- [ ] Implement `ToolRisk`, `ToolEffect`, `ToolErrorCode`, `ToolExecutionContext`, `ToolCall`, `ToolResult`, generic `ToolDefinition` and `ToolRuntime`.
- [ ] Ensure every result contains `call_id`, `tool_name`, `ok`, elapsed milliseconds and either serialized output or a stable error code; exception messages are bounded to 500 characters.
- [ ] Run the focused test and expect all cases to pass.

Required public contract:

```python
class ToolExecutionContext(BaseModel):
    permissions: frozenset[str]
    approved_call_ids: frozenset[str] = frozenset()


class ToolCall(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, object]


class ToolRuntime:
    def register(self, definition: ToolDefinition[InputT, OutputT]) -> None: ...
    def schemas(self, context: ToolExecutionContext) -> tuple[dict[str, object], ...]: ...
    def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult: ...
```

## Task 2: Provider-neutral Model Gateway

**Files:**
- Create: `src/ai_trader/models/__init__.py`
- Create: `src/ai_trader/models/gateway.py`
- Create: `tests/unit/test_model_gateway.py`

- [ ] Write failing tests for normalized text responses, structured tool calls, token usage, primary-provider retry, fallback provider, schema-invalid responses and bounded errors.
- [ ] Run `py -3.13 -m pytest tests/unit/test_model_gateway.py -v`; expect import failure.
- [ ] Implement immutable `ModelRequest`, `ModelToolCall`, `ModelUsage`, `ModelResponse`, `ProviderError`, `ModelProvider` protocol and `ModelGateway`.
- [ ] Retry only retryable provider failures, never retry schema/authorization failures, and record provider name, model, attempts and elapsed milliseconds.
- [ ] Run the focused test and expect all cases to pass.

Required provider boundary:

```python
class ModelProvider(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

## Task 3: Deterministic fake provider and structured Agent demo

**Files:**
- Create: `src/ai_trader/models/fake.py`
- Create: `tests/integration/test_structured_agent_flow.py`
- Modify: `src/ai_trader/demo.py`

- [ ] Write an integration test where `FakeModelProvider` returns a structured `get_quote` call, Tool Runtime validates and executes it, then the gateway returns a final text conclusion.
- [ ] Assert that no regex parser, network module, legacy `agent_tools`, model client or trading GUI is imported.
- [ ] Implement a deterministic queued-response fake provider and add `--agent` to the offline demo.
- [ ] Run `py -3.13 -m ai_trader.demo --agent`; expect a typed tool call, validated tool result and final grounded conclusion.

## Task 4: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`

- [ ] Document the typed Tool Runtime, permission model, Model Gateway and offline structured Agent command.
- [ ] Run Build, Mypy strict, Ruff, pytest with branch coverage, dependency check, credential scan, dynamic-code scan and `git diff --check`.
- [ ] Require zero test failures, zero type/lint issues, no credential-shaped values and at least 85% aggregate coverage for `src/ai_trader`.
- [ ] Do not stage or commit without explicit user authorization.

## Deferred to Phase 3

The gateway and tool runtime remain stateless in this phase. Run persistence, event Trace storage, cancellation, crash recovery and migration of `agent_planner.py` belong to the Phase 3 state-machine plan.
