"""Eval 执行器边界与确定性契约基线。"""

from typing import Protocol

from ai_trader.evals.contracts import (
    EvalObservation,
    EvalReport,
    EvalScenario,
    ObservedToolCall,
)
from ai_trader.evals.grading import grade_scenario
from ai_trader.evals.loader import EvalDataset


class ScenarioExecutor(Protocol):
    def execute(self, scenario: EvalScenario) -> EvalObservation: ...


class ExpectedTraceExecutor:
    """
    用期望轨迹验证数据集和评分器的契约基线。

    该结果不代表真实模型质量。
    """

    def execute(self, scenario: EvalScenario) -> EvalObservation:
        calls = tuple(
            ObservedToolCall(
                name=name,
                arguments=scenario.expected_arguments.get(name, {}),
            )
            for name in scenario.required_tools
        )
        return EvalObservation(
            scenario_id=scenario.scenario_id,
            tool_calls=calls,
            final_outcome=scenario.expected_outcome,
            evidence_refs=scenario.required_evidence,
            recovered=scenario.expects_recovery,
            steps=max(1, len(calls)),
        )


def run_dataset(dataset: EvalDataset, executor: ScenarioExecutor) -> EvalReport:
    scores = tuple(
        grade_scenario(scenario, executor.execute(scenario))
        for scenario in dataset.scenarios
    )
    count = len(scores)
    passed = sum(score.passed for score in scores)
    metric_names = (
        "tool_selection",
        "argument_accuracy",
        "policy_compliance",
        "outcome_accuracy",
        "recovery",
        "step_efficiency",
        "groundedness",
    )
    metrics = {
        name: sum(float(getattr(score, name)) for score in scores) / count
        for name in metric_names
    }
    return EvalReport(
        dataset=dataset.name,
        scenario_count=count,
        passed_count=passed,
        pass_rate=passed / count,
        metrics=metrics,
        total_input_tokens=sum(score.input_tokens for score in scores),
        total_output_tokens=sum(score.output_tokens for score in scores),
        total_estimated_cost=sum(score.estimated_cost for score in scores),
        scores=scores,
    )
