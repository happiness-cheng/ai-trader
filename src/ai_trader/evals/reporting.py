"""字节稳定的 Eval JSON 和 Markdown 报告。"""

import json

from ai_trader.evals.contracts import EvalReport


def render_json(report: EvalReport) -> str:
    return json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def render_markdown(report: EvalReport) -> str:
    labels = {
        "tool_selection": "Tool selection",
        "argument_accuracy": "Argument accuracy",
        "policy_compliance": "Policy compliance",
        "outcome_accuracy": "Outcome accuracy",
        "recovery": "Recovery",
        "step_efficiency": "Step efficiency",
        "groundedness": "Groundedness",
    }
    lines = [
        f"# Eval Report: {report.dataset}",
        "",
        "> Deterministic contract baseline; not a real-model quality claim.",
        "",
        f"- Scenarios: {report.scenario_count}",
        f"- Passed: {report.passed_count}",
        f"- Pass rate: {report.pass_rate:.1%}",
        "",
        "| Metric | Score |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {labels[name]} | {report.metrics[name]:.1%} |"
        for name in labels
    )
    return "\n".join(lines) + "\n"
