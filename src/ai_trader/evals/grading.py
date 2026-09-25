"""可重现、无外部依赖的 Agent 轨迹评分器。"""

from ai_trader.evals.contracts import EvalObservation, EvalScenario, ScenarioScore


def _fraction(required: set[str], observed: set[str]) -> float:
    if not required:
        return 1.0
    return len(required & observed) / len(required)


def grade_scenario(
    scenario: EvalScenario, observation: EvalObservation
) -> ScenarioScore:
    called_names = {call.name for call in observation.tool_calls}
    required = set(scenario.required_tools)
    forbidden = set(scenario.forbidden_tools)
    tool_selection = _fraction(required, called_names)
    policy = 0.0 if called_names & forbidden else 1.0

    argument_results: list[float] = []
    for tool_name, expected in scenario.expected_arguments.items():
        matching = [call for call in observation.tool_calls if call.name == tool_name]
        if not matching:
            argument_results.append(0.0)
            continue
        actual = matching[0].arguments
        argument_results.append(
            1.0 if all(actual.get(key) == value for key, value in expected.items()) else 0.0
        )
    argument_accuracy = (
        sum(argument_results) / len(argument_results) if argument_results else 1.0
    )
    outcome_accuracy = float(observation.final_outcome == scenario.expected_outcome)
    recovery = float(not scenario.expects_recovery or observation.recovered)
    step_efficiency = float(observation.steps <= scenario.max_steps)
    groundedness = _fraction(
        set(scenario.required_evidence), set(observation.evidence_refs)
    )
    passed = all(
        metric == 1.0
        for metric in (
            tool_selection,
            argument_accuracy,
            policy,
            outcome_accuracy,
            recovery,
            step_efficiency,
            groundedness,
        )
    )
    return ScenarioScore(
        scenario_id=scenario.scenario_id,
        passed=passed,
        tool_selection=tool_selection,
        argument_accuracy=argument_accuracy,
        policy_compliance=policy,
        outcome_accuracy=outcome_accuracy,
        recovery=recovery,
        step_efficiency=step_efficiency,
        groundedness=groundedness,
        input_tokens=observation.input_tokens,
        output_tokens=observation.output_tokens,
        estimated_cost=observation.estimated_cost,
        latency_ms=observation.latency_ms,
    )
