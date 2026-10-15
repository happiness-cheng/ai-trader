from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import AgentRunFailed, ProductionAgentRunner
from ai_trader.agents.state import RunState
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelGateway, ModelResponse, ModelToolCall, ModelUsage
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime


class EmptyInput(BaseModel):
    pass


class QuoteOutput(BaseModel):
    price: Decimal


@pytest.mark.parametrize('failure', ['tokens', 'time', 'nested', 'permission'])
def test_runner_failure_preserves_actual_observation(tmp_path, failure):
    from ai_trader.evals.trace_executor import AgentTraceScenarioExecutor
    from ai_trader.evals.contracts import EvalScenario
    repository = SqlRunRepository(f"sqlite:///{tmp_path / 'budget.db'}")
    runtime = ToolRuntime()
    calls = []
    class NestedInput(BaseModel):
        payload: dict[str, list[int]]
    runtime.register(ToolDefinition(name='nested', description='nested fixture',
        input_model=NestedInput, output_model=QuoteOutput,
        handler=lambda data: calls.append(data) or QuoteOutput(price=Decimal(100)), permission='read'))
    response = ModelResponse(provider='fake', model='fake', tool_calls=(
        ModelToolCall(call_id='n', name='nested', arguments={'payload': {'values': [1, 2]}}),),
        usage=ModelUsage(input_tokens=10, output_tokens=5), attempts=1, elapsed_ms=0)
    from itertools import chain, repeat
    ticks = chain([0., 0.], repeat(2.))
    runner = ProductionAgentRunner(ModelGateway([FakeModelProvider([response] * 3)]), runtime,
        PersistentOrchestrator(repository),
        ToolExecutionContext(permissions=frozenset() if failure == 'permission' else frozenset({'read'})),
        model='fake', max_total_tokens=12 if failure == 'tokens' else 1000,
        max_wall_seconds=1 if failure == 'time' else 60,
        monotonic=lambda: next(ticks))
    try:
        executor = AgentTraceScenarioExecutor(lambda scenario: runner.run(scenario.prompt))
        observed = executor.execute(EvalScenario(scenario_id='test', category='runtime',
            prompt='exercise failure', expected_outcome='failed'))
        assert observed.final_outcome == 'failed'
        assert observed.scenario_id == 'test'
        assert observed.input_tokens >= 10
        assert bool(observed.tool_calls) == (failure in {'nested', 'permission'})
        assert len(calls) == (2 if failure == 'nested' else 0)
        assert observed.failure_reason
    finally:
        repository.close()


def test_persistent_runner_executes_structured_tool_and_completes(tmp_path):
    repository = SqlRunRepository(f"sqlite:///{tmp_path / 'runner.db'}")
    orchestrator = PersistentOrchestrator(
        repository, clock=lambda: datetime(2026, 7, 11, 5, 0, tzinfo=UTC)
    )
    runtime = ToolRuntime()
    runtime.register(
        ToolDefinition(
            name="get_quote", description="fixture quote", input_model=EmptyInput,
            output_model=QuoteOutput,
            handler=lambda _data: QuoteOutput(price=Decimal(100)),
            permission="market:read",
        )
    )
    provider = FakeModelProvider([
        ModelResponse(
            provider="fake", model="fake", text="",
            tool_calls=(ModelToolCall(call_id="call-1", name="get_quote", arguments={}),),
            usage=ModelUsage(input_tokens=10, output_tokens=5), attempts=1, elapsed_ms=0,
        ),
        ModelResponse(
            provider="fake", model="fake", text="final grounded answer",
            usage=ModelUsage(input_tokens=20, output_tokens=10), attempts=1, elapsed_ms=0,
        ),
    ])
    runner = ProductionAgentRunner(
        ModelGateway([provider]), runtime, orchestrator,
        ToolExecutionContext(permissions=frozenset({"market:read"})),
        model="fake", max_turns=4,
    )
    result = runner.run("get fixture quote")
    assert result.final_text == "final grounded answer"
    assert repository.get(result.run_id).state is RunState.SUCCEEDED
    assert result.observation.tool_calls[0].name == "get_quote"
    repository.close()


def test_runner_detects_dead_loop(tmp_path):
    """3 轮相同工具调用 → 死循环检测触发"""
    repository = SqlRunRepository(f"sqlite:///{tmp_path / 'loop.db'}")
    runtime = ToolRuntime()
    runtime.register(
        ToolDefinition(
            name="get_quote", description="fixture", input_model=EmptyInput,
            output_model=QuoteOutput,
            handler=lambda _data: QuoteOutput(price=Decimal(100)),
            permission="market:read",
        )
    )
    responses = [
        ModelResponse(
            provider="fake", model="fake", text="",
            tool_calls=(ModelToolCall(call_id=str(i), name="get_quote", arguments={}),),
            usage=ModelUsage(input_tokens=10, output_tokens=5), attempts=1, elapsed_ms=0,
        )
        for i in range(3)
    ]
    runner = ProductionAgentRunner(
        ModelGateway([FakeModelProvider(responses)]), runtime,
        PersistentOrchestrator(repository),
        ToolExecutionContext(permissions=frozenset({"market:read"})),
        model="fake", max_turns=5,
    )
    with pytest.raises(AgentRunFailed, match="loop detected"):
        runner.run("loop test")
    repository.close()
