"""将 Model Gateway、Tool Runtime 和持久化状态机组合的手动 Agent 循环。"""

import json
from dataclasses import dataclass

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.state import RunState
from ai_trader.evals.contracts import EvalObservation, ObservedToolCall
from ai_trader.models.gateway import ModelGateway, ModelGatewayError, ModelRequest
from ai_trader.tools.runtime import (
    ToolCall,
    ToolErrorCode,
    ToolExecutionContext,
    ToolRuntime,
)


class AgentRunFailed(RuntimeError):
    pass


def _detect_loop(turn_signatures: list[frozenset], *, window: int = 3) -> bool:
    """检测死循环：连续 window 次完全相同的工具调用集合"""
    if len(turn_signatures) < window:
        return False
    return len(set(turn_signatures[-window:])) == 1


@dataclass(frozen=True)
class RunnerResult:
    run_id: str
    final_text: str
    observation: EvalObservation


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
    ) -> None:
        if max_turns < 1:
            raise ValueError("max turns must be positive")
        self._gateway = gateway
        self._tools = tools
        self._orchestrator = orchestrator
        self._context = context
        self._model = model
        self._max_turns = max_turns

    def run(self, goal: str) -> RunnerResult:
        run = self._orchestrator.create_run(goal)
        self._orchestrator.start(run.run_id)
        messages: tuple[dict[str, object], ...] = (
            {"role": "user", "content": goal},
        )
        observed_calls: list[ObservedToolCall] = []
        evidence: list[str] = []
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0.0
        turn_signatures: list[frozenset] = []

        for turn in range(1, self._max_turns + 1):
            try:
                response = self._gateway.complete(
                    ModelRequest(
                        model=self._model,
                        messages=messages,
                        tools=self._tools.schemas(self._context),
                        max_tokens=4000,
                    )
                )
            except ModelGatewayError as exc:
                self._orchestrator.transition(
                    run.run_id, RunState.FAILED, {"error": str(exc)}
                )
                raise AgentRunFailed("model provider failed") from exc

            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            latency_ms += response.elapsed_ms
            if not response.tool_calls:
                self._orchestrator.transition(run.run_id, RunState.SUCCEEDED)
                return RunnerResult(
                    run_id=run.run_id,
                    final_text=response.text,
                    observation=EvalObservation(
                        scenario_id=run.run_id,
                        tool_calls=tuple(observed_calls),
                        final_outcome="succeeded",
                        evidence_refs=tuple(evidence),
                        recovered=False,
                        steps=turn,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=latency_ms,
                    ),
                )

            # 死循环检测：连续 3 轮完全相同的工具调用
            turn_sig = frozenset(
                (call.name, tuple(sorted(call.arguments.items())))
                for call in response.tool_calls
            )
            turn_signatures.append(turn_sig)
            if _detect_loop(turn_signatures):
                self._orchestrator.transition(
                    run.run_id, RunState.FAILED,
                    {"error": "loop detected", "turn": turn},
                )
                raise AgentRunFailed("loop detected: repeated identical tool calls")

            assistant_blocks: list[dict[str, object]] = []
            if response.text:
                assistant_blocks.append({"type": "text", "text": response.text})
            tool_results: list[dict[str, object]] = []
            for model_call in response.tool_calls:
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
                if result.error_code is ToolErrorCode.APPROVAL_REQUIRED:
                    self._orchestrator.request_approval(
                        run.run_id,
                        {"call_id": model_call.call_id, "tool": model_call.name},
                    )
                    raise AgentRunFailed("tool call is waiting for approval")
                if not result.ok:
                    self._orchestrator.transition(
                        run.run_id,
                        RunState.FAILED,
                        {"call_id": model_call.call_id, "error": result.error_code},
                    )
                    raise AgentRunFailed(f"tool failed: {result.error_code}")
                self._orchestrator.transition(run.run_id, RunState.PLANNING)
                evidence.append(model_call.name)
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
            messages += (
                {"role": "assistant", "content": assistant_blocks},
                {"role": "user", "content": tool_results},
            )

        self._orchestrator.transition(
            run.run_id, RunState.FAILED, {"error": "max_turns_exceeded"}
        )
        raise AgentRunFailed("maximum turns exceeded")
