"""将 Model Gateway、Tool Runtime 和持久化状态机组合的手动 Agent 循环。"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter

from ai_trader.agents.context import (
    ContextBudgetExceeded,
    ContextBuilder,
    ContextReport,
    ToolExchange,
)
from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.state import RunState
from ai_trader.evals.contracts import EvalObservation, ObservedToolCall
from ai_trader.models.gateway import ModelGateway, ModelGatewayError
from ai_trader.tools.runtime import (
    ToolCall,
    ToolErrorCode,
    ToolExecutionContext,
    ToolRuntime,
)


class AgentRunFailed(RuntimeError):
    def __init__(self, message: str, *, observation: EvalObservation | None = None) -> None:
        super().__init__(message)
        self.observation = observation


# 一轮工具调用的签名：工具名 + 排序后的参数，用于死循环检测
TurnSignature = frozenset[tuple[str, str]]


def _detect_loop(turn_signatures: list[TurnSignature], *, window: int = 3) -> bool:
    """检测死循环：连续 window 次完全相同的工具调用集合"""
    if len(turn_signatures) < window:
        return False
    return len(set(turn_signatures[-window:])) == 1


@dataclass(frozen=True)
class RunnerResult:
    run_id: str
    final_text: str
    observation: EvalObservation
    context_reports: tuple[ContextReport, ...] = ()


class ProductionAgentRunner:
    def __init__(
        self,
        gateway: ModelGateway,
        tools: ToolRuntime,
        orchestrator: PersistentOrchestrator,
        context: ToolExecutionContext,
        *,
        model: str,
        max_turns: int = 8,
        max_total_tokens: int = 32000,
        max_wall_seconds: float = 120,
        monotonic: Callable[[], float] = perf_counter,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max turns must be positive")
        if max_total_tokens < 1 or not 0 < max_wall_seconds < float('inf'):
            raise ValueError('budgets must be positive and finite')
        self._gateway = gateway
        self._tools = tools
        self._orchestrator = orchestrator
        self._context = context
        self._model = model
        self._max_turns = max_turns
        self._max_total_tokens = max_total_tokens
        self._max_wall_seconds = max_wall_seconds
        self._monotonic = monotonic
        self._context_builder = context_builder or ContextBuilder()

    def run(
        self, goal: str, *, complete_run: bool = True, run_id: str | None = None,
        instructions: str = '', constraints: dict[str, object] | None = None,
    ) -> RunnerResult:
        run = (
            self._orchestrator.create_run(goal)
            if run_id is None else self._orchestrator.get_run(run_id)
        )
        self._orchestrator.start(run.run_id)
        started = self._monotonic()
        history: tuple[ToolExchange, ...] = ()
        context_reports: list[ContextReport] = []
        observed_calls: list[ObservedToolCall] = []
        evidence: list[str] = []
        input_tokens = 0
        output_tokens = 0
        turn_signatures: list[TurnSignature] = []

        def fail(reason: str, steps: int, *, waiting: bool = False) -> AgentRunFailed:
            if not waiting:
                self._orchestrator.transition(run.run_id, RunState.FAILED, {'error': reason})
            return AgentRunFailed(reason, observation=EvalObservation(
                scenario_id=run.run_id, tool_calls=tuple(observed_calls),
                final_outcome='waiting_approval' if waiting else 'failed',
                evidence_refs=tuple(evidence), recovered=False, steps=steps,
                input_tokens=input_tokens, output_tokens=output_tokens,
                latency_ms=max(0., (self._monotonic() - started) * 1000), failure_reason=reason,
            ))

        def check_time(steps: int) -> None:
            # 合作式期限：不强杀已开始的 provider / handler；返回后禁止继续调用。
            if self._monotonic() - started >= self._max_wall_seconds:
                raise fail('wall_clock_budget_exceeded', steps)

        for turn in range(1, self._max_turns + 1):
            check_time(turn - 1)
            try:
                built = self._context_builder.build(
                    model=self._model, goal=goal, instructions=instructions, history=history,
                    tools=self._tools.schemas(self._context), constraints=constraints,
                    max_output_tokens=min(4000, max(1, self._max_total_tokens - input_tokens - output_tokens)),
                )
            except ContextBudgetExceeded as exc:
                raise fail('context_budget_exceeded', turn - 1) from exc
            context_reports.append(built.report)
            try:
                response = self._gateway.complete(built.request)
            except ModelGatewayError as exc:
                raise fail('model provider failed', turn) from exc

            input_tokens += (response.usage.input_tokens + response.usage.cache_creation_input_tokens
                             + response.usage.cache_read_input_tokens)
            output_tokens += response.usage.output_tokens
            if input_tokens + output_tokens > self._max_total_tokens:
                raise fail('token_budget_exceeded', turn)
            check_time(turn)
            if not response.tool_calls:
                if complete_run:
                    self._orchestrator.transition(run.run_id, RunState.SUCCEEDED)
                return RunnerResult(
                    run_id=run.run_id,
                    final_text=response.text,
                    context_reports=tuple(context_reports),
                    observation=EvalObservation(
                        scenario_id=run.run_id,
                        tool_calls=tuple(observed_calls),
                        final_outcome="succeeded" if complete_run else "planning",
                        evidence_refs=tuple(evidence),
                        recovered=False,
                        steps=turn,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=max(0., (self._monotonic() - started) * 1000),
                    ),
                )

            # 死循环检测：连续 3 轮完全相同的工具调用
            turn_sig = frozenset(
                (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))
                for call in response.tool_calls
            )
            turn_signatures.append(turn_sig)
            if _detect_loop(turn_signatures):
                raise fail('loop detected: repeated identical tool calls', turn)

            assistant_blocks: list[dict[str, object]] = []
            if response.text:
                assistant_blocks.append({"type": "text", "text": response.text})
            tool_results: list[dict[str, object]] = []
            for model_call in response.tool_calls:
                check_time(turn)
                observed_calls.append(
                    ObservedToolCall(
                        name=model_call.name, arguments=model_call.arguments
                    )
                )
                self._orchestrator.transition(
                    run.run_id,
                    RunState.TOOL_RUNNING,
                    {"call_id": model_call.call_id, "tool": model_call.name},
                )
                result = self._tools.execute(
                    ToolCall(
                        call_id=model_call.call_id,
                        name=model_call.name,
                        arguments=model_call.arguments,
                    ),
                    self._context,
                )
                digest = sha256(json.dumps(result.output, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if result.ok else None
                observed_calls[-1] = observed_calls[-1].model_copy(update={
                    'ok': result.ok, 'error_code': result.error_code.value if result.error_code else None,
                    'elapsed_ms': result.elapsed_ms, 'output_sha256': digest,
                })
                if result.error_code is ToolErrorCode.APPROVAL_REQUIRED:
                    self._orchestrator.request_approval(
                        run.run_id,
                        {"call_id": model_call.call_id, "tool": model_call.name},
                    )
                    raise fail('tool call is waiting for approval', turn, waiting=True)
                if not result.ok:
                    raise fail(f'tool failed: {result.error_code}', turn)
                self._orchestrator.transition(run.run_id, RunState.PLANNING, {
                    'call_id': model_call.call_id, 'tool': model_call.name,
                    'elapsed_ms': result.elapsed_ms, 'output_sha256': digest,
                })
                evidence.append(model_call.name)
                check_time(turn)
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": model_call.call_id,
                        "name": model_call.name,
                        "input": model_call.arguments,
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": model_call.call_id,
                        "content": json.dumps(
                            result.output, ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
            history += (ToolExchange(tuple(assistant_blocks), tuple(tool_results)),)
            if input_tokens + output_tokens >= self._max_total_tokens:
                raise fail('token_budget_exceeded', turn)

        raise fail('maximum turns exceeded', self._max_turns)
