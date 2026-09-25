from ai_trader.evals.loader import load_dataset
from ai_trader.evals.cli import main as eval_main
from ai_trader.evals.contracts import EvalObservation
from ai_trader.evals.reporting import render_json, render_markdown
from ai_trader.evals.runner import ExpectedTraceExecutor, run_dataset
from ai_trader.evals.trace_executor import AgentTraceScenarioExecutor
from ai_trader.agents.runner import RunnerResult


def test_core_contract_baseline_is_deterministic_and_complete():
    dataset = load_dataset("evals/scenarios/core.json")
    first = run_dataset(dataset, ExpectedTraceExecutor())
    second = run_dataset(dataset, ExpectedTraceExecutor())
    assert first == second
    assert first.scenario_count >= 30
    assert first.pass_rate == 1
    assert first.metrics["policy_compliance"] == 1
    assert render_json(first) == render_json(second)
    assert "Policy compliance | 100.0%" in render_markdown(first)


def test_eval_cli_writes_reports(tmp_path):
    assert eval_main([
        "--dataset", "evals/scenarios/core.json",
        "--output", str(tmp_path),
    ]) == 0
    assert (tmp_path / "eval-report.json").exists()
    assert (tmp_path / "eval-report.md").exists()


def test_actual_trace_adapter_rebinds_scenario_id():
    dataset = load_dataset("evals/scenarios/core.json")
    observation = EvalObservation(
        scenario_id="run-id", final_outcome="answer", steps=1
    )
    executor = AgentTraceScenarioExecutor(
        lambda _scenario: RunnerResult("run-id", "answer", observation)
    )
    result = executor.execute(dataset.scenarios[0])
    assert result.scenario_id == dataset.scenarios[0].scenario_id
