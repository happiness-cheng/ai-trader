"""运行时评测必须从实际工具 / 状态取值，并能发现错误期望。"""

from ai_trader.evals.contracts import EvalScenario
from ai_trader.evals.grading import grade_scenario
import pytest


def test_runtime_fixture_cannot_read_expected_answer(tmp_path):
    from ai_trader.evals.runtime_benchmark import FixtureRuntimeExecutor
    scenario = EvalScenario(scenario_id='quote', category='fixture', prompt='quote',
        required_tools=('get_quote',), expected_arguments={'get_quote': {'symbol': '600519'}},
        required_evidence=('get_quote',), expected_outcome='succeeded')
    executor = FixtureRuntimeExecutor(tmp_path)
    observation = executor.execute(scenario)
    assert grade_scenario(scenario, observation).passed
    wrong = scenario.model_copy(update={'expected_arguments': {'get_quote': {'symbol': '000001'}}})
    assert not grade_scenario(wrong, executor.execute(wrong)).passed
    assert observation.input_tokens == 30


def test_offline_paper_demo_normal_and_unknown_recovery(tmp_path):
    from ai_trader.paper_demo import run_demo
    for response_loss in (False, True):
        result = run_demo(tmp_path / str(response_loss), response_loss=response_loss)
        assert result['state'] == 'succeeded'
        assert result['orders'] == 1
        assert result['duplicate_same_order']
        assert result['mode'] == 'offline_fixture_paper'
        assert result['recovered'] == response_loss


@pytest.mark.parametrize(('mode', 'reason'), [
    ('provider_error', 'model provider failed'),
    ('wall_clock_budget', 'wall_clock_budget_exceeded'),
])
def test_runtime_failure_cases_preserve_reason_and_do_not_execute_tools(tmp_path, mode, reason):
    from ai_trader.evals.runtime_benchmark import FixtureRuntimeExecutor
    scenario = EvalScenario(scenario_id=mode, category='fixture', prompt=mode,
                            expected_outcome='failed', forbidden_tools=('get_quote',))
    executor = FixtureRuntimeExecutor(tmp_path)
    observation = executor.execute(scenario)
    assert grade_scenario(scenario, observation).passed
    assert observation.failure_reason == reason
    assert not observation.tool_calls
    wrong = scenario.model_copy(update={'expected_outcome': 'succeeded'})
    assert not grade_scenario(wrong, executor.execute(wrong)).passed
