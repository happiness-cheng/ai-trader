"""将持久化 Agent Runner 的实际轨迹适配为 EvalObservation。"""

from collections.abc import Callable

from ai_trader.agents.runner import RunnerResult
from ai_trader.evals.contracts import EvalObservation, EvalScenario


class AgentTraceScenarioExecutor:
    def __init__(self, run_scenario: Callable[[EvalScenario], RunnerResult]) -> None:
        self._run_scenario = run_scenario

    def execute(self, scenario: EvalScenario) -> EvalObservation:
        result = self._run_scenario(scenario)
        return result.observation.model_copy(
            update={"scenario_id": scenario.scenario_id}
        )
