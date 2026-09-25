"""Agent Eval 契约基线 CLI。"""

import argparse
from pathlib import Path

from ai_trader.evals.loader import load_dataset
from ai_trader.evals.reporting import render_json, render_markdown
from ai_trader.evals.runner import ExpectedTraceExecutor, run_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Trader Agent Evals")
    parser.add_argument("--dataset", default="evals/scenarios/core.json")
    parser.add_argument("--output", default="evals/reports")
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    args = parser.parse_args(argv)

    report = run_dataset(load_dataset(args.dataset), ExpectedTraceExecutor())
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "eval-report.json"
    markdown_path = output / "eval-report.md"
    json_path.write_text(render_json(report), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"scenarios={report.scenario_count}")
    print(f"pass_rate={report.pass_rate:.1%}")
    print(f"policy_compliance={report.metrics['policy_compliance']:.1%}")
    print(f"groundedness={report.metrics['groundedness']:.1%}")
    print(f"json_report={json_path.as_posix()}")
    print(f"markdown_report={markdown_path.as_posix()}")
    return int(
        report.pass_rate < args.min_pass_rate
        or report.metrics["policy_compliance"] < 1.0
    )


if __name__ == "__main__":
    raise SystemExit(main())
