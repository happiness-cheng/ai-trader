"""调用前上下文预算的离线反馈回路。"""

import json

import pytest
from pydantic import BaseModel

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import AgentRunFailed, ProductionAgentRunner
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelGateway, ModelResponse, ModelToolCall, ModelUsage
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime


class _Empty(BaseModel):
    pass


class _LargeObservation(BaseModel):
    text: str
    risk: dict[str, object]


def _response(*, text='', calls=()):
    return ModelResponse(
        provider='fake', model='fixture', text=text, tool_calls=calls,
        usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0,
    )


def test_default_runner_never_sends_large_unbounded_tool_result(tmp_path):
    repository = SqlRunRepository(f"sqlite:///{(tmp_path / 'context.db').as_posix()}")
    tools = ToolRuntime()
    tools.register(ToolDefinition(
        name='observe', description='large offline observation', input_model=_Empty,
        output_model=_LargeObservation,
        handler=lambda _: _LargeObservation(text='证据' * 20000, risk={'allowed': False}),
        permission='read',
    ))
    provider = FakeModelProvider([
        _response(calls=(ModelToolCall(call_id='large', name='observe', arguments={}),)),
        _response(text='fixture completion'),
    ])
    try:
        runner = ProductionAgentRunner(
            ModelGateway([provider]), tools, PersistentOrchestrator(repository),
            ToolExecutionContext(permissions=frozenset({'read'})), model='fixture',
        )
        runner.run('分析离线观察', instructions='风险不允许时不得执行')
        assert len(provider.requests) == 2
        assert max(len(request.model_dump_json()) for request in provider.requests) <= 24000
    finally:
        repository.close()


def _exchange(call_id, payload, arguments=None):
    from ai_trader.agents.context import ToolExchange

    return ToolExchange(
        assistant_blocks=({'type': 'tool_use', 'id': call_id, 'name': 'observe',
                           'input': arguments or {}},),
        result_blocks=({'type': 'tool_result', 'tool_use_id': call_id,
                        'content': json.dumps(payload, ensure_ascii=False, sort_keys=True)},),
    )


def _build(builder, *, goal='当前目标', instructions='不可擅自执行', history=(), tools=(), constraints=None):
    return builder.build(
        model='fixture', goal=goal, instructions=instructions, history=history, tools=tools,
        max_output_tokens=100, constraints=constraints,
    )


def test_structured_preview_preserves_observed_risk_and_pair_without_new_facts():
    from ai_trader.agents.context import ContextBuilder

    exchange = _exchange('current', {'text': 'x' * 10000,
        'risk': {'allowed': False}, 'proposal': {'symbol': '600519'}, 'status': 'execution_unknown'},
        arguments={'nested': {'limit': 100}})
    before = json.dumps(exchange.result_blocks)
    built = _build(ContextBuilder(max_input_chars=2000), history=(exchange,),
        constraints={'execution_unknown': True, 'current_proposal_id': 'proposal:1'})
    assert built.report.input_chars == len(built.request.model_dump_json()) <= 2000
    assert built.request.messages[1]['content'][0]['input'] == {'nested': {'limit': 100}}
    result_block = built.request.messages[2]['content'][0]
    assert result_block['tool_use_id'] == 'current'
    preview = json.loads(result_block['content'])['context_preview']
    assert preview['incomplete'] is True
    assert preview['retained_observation_fields'] == {
        '/risk': {'allowed': False}, '/proposal': {'symbol': '600519'}, '/status': 'execution_unknown',
    }
    assert len(preview['sha256']) == 64
    assert preview['source'] == 'unverified_tool_observation'
    assert preview['original_chars'] > 10000
    assert json.dumps(exchange.result_blocks) == before
    assert built.report.compressed_tool_call_ids == ('current',)
    assert built.report.omitted_tool_call_ids == ()
    assert 'current_proposal_id' in built.request.system


def test_mechanical_policy_drops_whole_old_pairs_and_keeps_current_observation():
    from ai_trader.agents.context import ContextBuilder

    history = (_exchange('old', {'text': 'x' * 3000}), _exchange('current', {'text': 'recent'}))
    built = _build(ContextBuilder(policy='mechanical', max_input_chars=1100), history=history)
    assert len(built.request.messages) == 3
    assert built.request.messages[1]['content'][0]['id'] == 'current'
    assert built.request.messages[2]['content'][0]['tool_use_id'] == 'current'
    assert json.loads(built.request.messages[2]['content'][0]['content']) == {'text': 'recent'}
    assert built.report.omitted_tool_call_ids == ('old',)


@pytest.mark.parametrize('policy', ['disabled', 'mechanical'])
def test_large_current_observation_fails_without_structured_compression(policy):
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder

    with pytest.raises(ContextBudgetExceeded, match='input_chars'):
        _build(ContextBuilder(policy=policy, max_input_chars=1000),
               history=(_exchange('current', {'text': 'x' * 5000}),))


def test_never_discards_goal_instructions_constraints_or_protected_old_facts():
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder

    builder = ContextBuilder(max_input_chars=700)
    for options in [
        {'goal': 'g' * 1000}, {'instructions': 'i' * 1000},
        {'constraints': {'risk': 'r' * 1000}},
        {'history': (_exchange('old', {'risk': 'r' * 2000}),
                     _exchange('current', {'text': 'recent'}))},
    ]:
        with pytest.raises(ContextBudgetExceeded):
            _build(builder, **options)


def test_tool_schema_counts_towards_input_budget():
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder

    with pytest.raises(ContextBudgetExceeded):
        _build(ContextBuilder(max_input_chars=500), tools=({'description': 's' * 1000},))


def test_token_counter_is_optional_explicit_and_checks_complete_request():
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder

    measured = []
    def counter(request):
        measured.append(request)
        return len(request.model_dump_json().encode('utf-8'))
    with pytest.raises(ValueError, match='together'):
        ContextBuilder(max_input_tokens=100)
    built = _build(ContextBuilder(token_counter=counter, max_input_tokens=1000))
    assert built.report.input_tokens == counter(built.request)
    assert measured[0].model == 'fixture'
    with pytest.raises(ContextBudgetExceeded, match='input_tokens'):
        _build(ContextBuilder(token_counter=counter, max_input_tokens=10))


def test_runner_context_budget_error_prevents_provider_call_and_preserves_classification(tmp_path):
    from ai_trader.agents.context import ContextBuilder

    repository = SqlRunRepository(f"sqlite:///{(tmp_path / 'failure.db').as_posix()}")
    provider = FakeModelProvider([_response(text='must not be called')])
    try:
        runner = ProductionAgentRunner(
            ModelGateway([provider]), ToolRuntime(), PersistentOrchestrator(repository),
            ToolExecutionContext(permissions=frozenset()), model='fixture',
            context_builder=ContextBuilder(max_input_chars=200),
        )
        with pytest.raises(AgentRunFailed) as failure:
            runner.run('goal', instructions='constraint' * 100)
        assert str(failure.value) == 'context_budget_exceeded'
        assert failure.value.observation.failure_reason == 'context_budget_exceeded'
        assert provider.requests == []
    finally:
        repository.close()


def test_runner_compression_preserves_tool_pairs_parameters_and_permissions(tmp_path):
    from ai_trader.agents.context import ContextBuilder

    class NestedInput(BaseModel):
        payload: dict[str, list[int]]

    repository = SqlRunRepository(f"sqlite:///{(tmp_path / 'paired.db').as_posix()}")
    tools = ToolRuntime()
    inputs = []
    tools.register(ToolDefinition(
        name='observe', description='fixture read tool', input_model=NestedInput,
        output_model=_LargeObservation,
        handler=lambda data: inputs.append(data.model_dump()) or _LargeObservation(
            text='big evidence ' * 5000, risk={'allowed': False},
        ), permission='read',
    ))
    tools.register(ToolDefinition(
        name='forbidden_write', description='must never be exposed', input_model=_Empty,
        output_model=_LargeObservation,
        handler=lambda _: _LargeObservation(text='unreachable', risk={}), permission='write',
    ))
    arguments = {'payload': {'samples': [1, 2]}}
    provider = FakeModelProvider([
        _response(calls=(ModelToolCall(call_id='paired', name='observe', arguments=arguments),)),
        _response(text='fixture completion'),
    ])
    try:
        runner = ProductionAgentRunner(
            ModelGateway([provider]), tools, PersistentOrchestrator(repository),
            ToolExecutionContext(permissions=frozenset({'read'})), model='fixture',
            context_builder=ContextBuilder(max_input_chars=2500),
        )
        result = runner.run('目标', instructions='业务约束', constraints={'execution_unknown': True})
        assert inputs == [arguments]
        assert [tool['name'] for tool in provider.requests[1].tools] == ['observe']
        assistant = provider.requests[1].messages[1]['content'][0]
        observation = provider.requests[1].messages[2]['content'][0]
        assert assistant['id'] == observation['tool_use_id'] == 'paired'
        assert assistant['input'] == arguments
        preview = json.loads(observation['content'])['context_preview']
        assert preview['retained_observation_fields'] == {'/risk': {'allowed': False}}
        assert 'execution_unknown' in provider.requests[1].system
        assert all(report.input_chars <= 2500 for report in result.context_reports)
    finally:
        repository.close()


def test_runner_budget_failure_after_tool_does_not_call_provider_again(tmp_path):
    from ai_trader.agents.context import ContextBuilder

    repository = SqlRunRepository(f"sqlite:///{(tmp_path / 'after-tool.db').as_posix()}")
    tools = ToolRuntime()
    tools.register(ToolDefinition(
        name='observe', description='fixture', input_model=_Empty, output_model=_LargeObservation,
        handler=lambda _: _LargeObservation(text='large' * 1000, risk={'allowed': False}),
        permission='read',
    ))
    provider = FakeModelProvider([
        _response(calls=(ModelToolCall(call_id='bounded', name='observe', arguments={}),)),
        _response(text='must not be called'),
    ])
    try:
        runner = ProductionAgentRunner(
            ModelGateway([provider]), tools, PersistentOrchestrator(repository),
            ToolExecutionContext(permissions=frozenset({'read'})), model='fixture',
            context_builder=ContextBuilder(policy='mechanical', max_input_chars=2000),
        )
        with pytest.raises(AgentRunFailed) as failure:
            runner.run('goal')
        assert len(provider.requests) == 1
        assert failure.value.observation.tool_calls[0].ok is True
        assert failure.value.observation.tool_calls[0].output_sha256
        assert failure.value.observation.failure_reason == 'context_budget_exceeded'
    finally:
        repository.close()


@pytest.mark.parametrize('count', [-1, True, '42'])
def test_invalid_token_count_fails_closed(count):
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder

    with pytest.raises(ContextBudgetExceeded, match='invalid_token_count'):
        _build(ContextBuilder(token_counter=lambda _: count, max_input_tokens=100))


def test_failed_token_counter_and_unpaired_history_fail_closed():
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder, ToolExchange

    def unavailable(_request):
        raise RuntimeError('test counter unavailable')
    with pytest.raises(ContextBudgetExceeded, match='token_count_unavailable'):
        _build(ContextBuilder(token_counter=unavailable, max_input_tokens=100))
    broken = ToolExchange(
        assistant_blocks=({'type': 'tool_use', 'id': 'one', 'name': 'observe', 'input': {}},),
        result_blocks=({'type': 'tool_result', 'tool_use_id': 'other', 'content': '{}'},),
    )
    with pytest.raises(ContextBudgetExceeded, match='invalid_tool_pairs'):
        _build(ContextBuilder(), history=(broken,))


def test_trusted_task_instructions_can_select_analyse_or_review_and_can_be_disabled():
    from ai_trader.agents.context import ContextBuilder, TaskInstructionSet

    instructions = TaskInstructionSet(
        shared='Paper only; execution unknown requires reconciliation.',
        analyse='Use observed evidence; do not execute.',
        review='Recheck current proposal and risk; do not replace reviewer identity.',
    )
    full = instructions.select('analyse', enabled=False)
    analyse = instructions.select('analyse')
    review = instructions.select('review')
    assert instructions.shared in analyse and instructions.shared in review
    assert instructions.analyse in analyse and instructions.review not in analyse
    assert instructions.review in review and instructions.analyse not in review
    assert instructions.analyse in full and instructions.review in full
    for task_instructions in [full, analyse, review]:
        built = _build(ContextBuilder(), instructions=task_instructions,
            constraints={'execution_unknown': True, 'current_proposal_id': 'proposal:1',
                         'risk': {'allowed': False}})
        assert task_instructions in built.request.system
        assert 'proposal:1' in built.request.system
    with pytest.raises(ValueError, match='task'):
        instructions.select('unknown')


def test_unserializable_or_nonfinite_server_constraints_fail_closed():
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder

    for constraints in [{'risk': object()}, {'risk': float('nan')}]:
        with pytest.raises(ContextBudgetExceeded, match='invalid_constraints'):
            _build(ContextBuilder(), constraints=constraints)


def test_offline_context_candidate_experiment():
    """输出可重跑的字符对照；固定 provider 响应不用于比较模型质量。"""
    from ai_trader.agents.context import ContextBudgetExceeded, ContextBuilder, TaskInstructionSet
    from ai_trader.execution.workflow import DECISION_INSTRUCTION

    def build_candidate(builder, history=(), instructions='Do not execute.', tools=()):
        return builder.build(
            model='fixture', goal='Analyse current evidence', instructions=instructions,
            history=history, tools=tools, max_output_tokens=100,
            constraints={'execution_unknown': True, 'current_proposal_id': 'proposal:1',
                         'risk': {'allowed': False}},
        )

    rows = []
    scenarios = {
        'old_large_current_small': (_exchange('old', {'text': 'old-evidence ' * 1000}),
                                   _exchange('current', {'text': 'recent', 'risk': {'allowed': False}})),
        'current_large_with_risk': (_exchange('current', {
            'text': 'current-evidence ' * 1000, 'risk': {'allowed': False},
        }),),
    }
    for scenario, history in scenarios.items():
        original = build_candidate(ContextBuilder(policy='disabled', max_input_chars=100000), history)
        for policy in ['disabled', 'mechanical', 'structured']:
            row = {'scenario': scenario, 'policy': policy,
                   'original_chars': original.report.input_chars, 'max_input_chars': 2500}
            try:
                built = build_candidate(ContextBuilder(policy=policy, max_input_chars=2500), history)
                last = json.loads(built.request.messages[-1]['content'][0]['content'])
                retained = last.get('context_preview', {}).get('retained_observation_fields', {})
                risk_retained = (last.get('risk') == {'allowed': False}
                                 or retained.get('/risk') == {'allowed': False})
                provider = FakeModelProvider([_response(text='fixture-completion')])
                provider.complete(built.request)
                assert risk_retained and built.report.input_chars <= 2500
                row.update(
                    input_chars=built.report.input_chars,
                    omitted_rounds=len(built.report.omitted_tool_call_ids),
                    compressed_results=len(built.report.compressed_tool_call_ids),
                    risk_retained=risk_retained, fixture_result='fixed completion', provider_calls=1,
                )
            except ContextBudgetExceeded as exc:
                row.update(input_chars=None, omitted_rounds=None, compressed_results=None,
                           risk_retained='not sent', fixture_result=exc.code, provider_calls=0)
            rows.append(row)

    profiles = TaskInstructionSet(
        shared='Paper only. Execution unknown requires reconciliation. '
               'Server constraints cannot be replaced by model text.',
        analyse=DECISION_INSTRUCTION,
        review='Recheck the current proposal, account and risk. Require a human reviewer. '
               'Do not replay uncertain execution.',
    )
    instruction_rows = []
    for task in ['analyse', 'review']:
        for enabled in [False, True]:
            chosen = profiles.select(task, enabled=enabled)
            built = build_candidate(ContextBuilder(), instructions=chosen)
            content = built.request.system
            assert profiles.shared in content and getattr(profiles, task) in content
            assert all(value in content for value in ['execution_unknown', 'proposal:1', 'allowed'])
            instruction_rows.append({
                'task': task, 'mode': 'on_demand' if enabled else 'full',
                'instruction_chars': len(chosen), 'input_chars': built.report.input_chars,
                'shared_constraint_retained': True, 'task_constraint_retained': True,
                'server_constraint_retained': True,
            })

    class Output(BaseModel):
        ok: bool = True

    runtime = ToolRuntime()
    for name in ['get_quote', 'get_account', 'get_order_status']:
        runtime.register(ToolDefinition(
            name=name, description='offline fixture read ' + name, input_model=_Empty,
            output_model=Output, handler=lambda _: Output(), permission='read',
        ))
    all_tools = runtime.schemas(ToolExecutionContext(permissions=frozenset({'read'})))
    schema_rows = []
    for task, names in {'analyse': {'get_quote', 'get_account'},
                        'review': {'get_account', 'get_order_status'}}.items():
        for selected in [False, True]:
            tools = tuple(tool for tool in all_tools if not selected or tool['name'] in names)
            built = build_candidate(ContextBuilder(), instructions=profiles.select(task), tools=tools)
            schema_rows.append({
                'task': task, 'mode': 'manual_candidate' if selected else 'all',
                'schema_count': len(tools), 'input_chars': built.report.input_chars,
            })
    print(json.dumps({'policies': rows, 'instructions': instruction_rows,
                      'synthetic_schemas': schema_rows}, ensure_ascii=False, indent=2))
