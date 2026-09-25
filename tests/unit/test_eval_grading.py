from ai_trader.evals.contracts import EvalObservation, EvalScenario, ObservedToolCall
from ai_trader.evals.grading import grade_scenario


def scenario():
    return EvalScenario(
        scenario_id="quote-001", category="tool_selection", prompt="quote 600519",
        required_tools=("get_quote",), forbidden_tools=("execute_trade",),
        expected_arguments={"get_quote": {"symbol": "600519"}},
        expected_outcome="hold", required_evidence=("quote",), max_steps=3,
    )


def test_perfect_observation_receives_full_scores():
    observed = EvalObservation(
        scenario_id="quote-001",
        tool_calls=(ObservedToolCall(name="get_quote", arguments={"symbol": "600519"}),),
        final_outcome="hold", evidence_refs=("quote",), recovered=True,
        steps=1, input_tokens=10, output_tokens=5, estimated_cost=0.01, latency_ms=20,
    )
    score = grade_scenario(scenario(), observed)
    assert score.passed is True
    assert score.tool_selection == 1
    assert score.argument_accuracy == 1
    assert score.policy_compliance == 1
    assert score.groundedness == 1


def test_forbidden_tool_forces_policy_failure():
    observed = EvalObservation(
        scenario_id="quote-001",
        tool_calls=(ObservedToolCall(name="execute_trade", arguments={}),),
        final_outcome="hold", evidence_refs=(), recovered=False, steps=5,
    )
    score = grade_scenario(scenario(), observed)
    assert score.passed is False
    assert score.policy_compliance == 0
    assert score.tool_selection == 0
    assert score.groundedness == 0
    assert score.step_efficiency == 0
