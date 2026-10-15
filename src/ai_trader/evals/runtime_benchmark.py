"""独立于 golden 的固定环境驱动实际 Agent；测工程机制，不测真实模型能力。"""

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter
from uuid import uuid4

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner
from ai_trader.evals.contracts import EvalObservation, EvalScenario
from ai_trader.evals.loader import load_dataset
from ai_trader.evals.reporting import render_markdown
from ai_trader.evals.runner import run_dataset
from ai_trader.evals.trace_executor import AgentTraceScenarioExecutor
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelGateway, ModelToolCall
from ai_trader.paper_demo import QuoteInput, QuoteOutput, fixture_response
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime


class FixtureRuntimeExecutor:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.observations: list[EvalObservation] = []

    def execute(self, scenario: EvalScenario) -> EvalObservation:
        # 仅 prompt 选择固定环境；不读取 required_tools / expected_* / evidence 等评分字段。
        mode = scenario.prompt
        if mode not in {'quote', 'invalid_argument', 'unknown_tool', 'permission_denied',
                        'tool_error', 'loop', 'token_budget', 'max_turns',
                        'provider_error', 'wall_clock_budget'}:
            raise ValueError('unsupported fixture environment')
        repository = SqlRunRepository(f"sqlite:///{(self.directory / (str(uuid4()) + '.db')).as_posix()}")
        tools = ToolRuntime()

        def handler(data: QuoteInput) -> QuoteOutput:
            if mode == 'tool_error':
                raise RuntimeError('fixture tool outage')
            return QuoteOutput()

        tools.register(ToolDefinition(name='get_quote', description='fixture quote', input_model=QuoteInput,
            output_model=QuoteOutput, handler=handler, permission='read'))
        call = ModelToolCall(call_id='quote', name='missing' if mode == 'unknown_tool' else 'get_quote',
            arguments={} if mode == 'invalid_argument' else {'symbol': '600519'})
        first = fixture_response(calls=(call,))
        responses = [first] * 3 if mode in {'loop', 'max_turns'} else [first, fixture_response(text='fixture quote=100')]
        if mode == 'provider_error':
            responses = []  # 固定 provider 队列耗尽，实际 gateway 产生失败。
        # 时间边界采用确定性虚拟时钟，不等待两分钟，也不冒充实测延迟。
        clock = iter((0., 0., 121., 121.)).__next__ if mode == 'wall_clock_budget' else perf_counter
        runner = ProductionAgentRunner(ModelGateway([FakeModelProvider(responses)]), tools,
            PersistentOrchestrator(repository),
            ToolExecutionContext(permissions=frozenset() if mode == 'permission_denied' else frozenset({'read'})),
            model='fixture', max_turns=1 if mode == 'max_turns' else 8,
            max_total_tokens=10 if mode == 'token_budget' else 32000,
            monotonic=clock)
        try:
            observation = AgentTraceScenarioExecutor(lambda item: runner.run(item.prompt)).execute(scenario)
            self.observations.append(observation)
            return observation
        finally:
            repository.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('evals/scenarios/runtime-fixture.json'))
    parser.add_argument('--output', type=Path, default=Path(mkdtemp(prefix='trader-runtime-eval-')))
    args = parser.parse_args(argv)
    executor = FixtureRuntimeExecutor(args.output / 'runs')
    report = run_dataset(load_dataset(args.dataset), executor)
    payload = report.model_dump(mode='json')
    source_root = Path(__file__).resolve().parents[1]
    hashes = {name: sha256((source_root / name).read_bytes()).hexdigest() for name in (
        'agents/runner.py', 'tools/runtime.py', 'evals/runtime_benchmark.py', 'evals/grading.py')}
    payload.update(metadata={'mode': 'actual_runtime_fixture', 'real_model_quality': False,
        'application_hashes': hashes,
        'dataset_sha256': sha256(args.dataset.read_bytes()).hexdigest(),
        'timestamp_utc': datetime.now(UTC).isoformat(), 'provider': 'scripted_fake',
        'token_usage': 'synthetic fixture', 'max_total_tokens': 32000,
        'virtual_clock_scenario': 'wall_clock_budget; latency is synthetic and not a measured performance result',
        'limitations': 'fixed fixture; not independent holdout; latency includes local persistence only'},
        observations=[item.model_dump(mode='json') for item in executor.observations])
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    path = args.output / f'runtime-fixture-{stamp}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    path.with_suffix('.md').write_text(
        'mode=actual_runtime_fixture; real_model_quality=false\n'
        'wall_clock_budget 使用虚拟时钟，延迟是合成值；整体延迟指标不可作性能成绩。\n\n'
        + render_markdown(report), encoding='utf-8')
    print(f'mode=actual_runtime_fixture scenarios={report.scenario_count} pass_rate={report.pass_rate:.1%} report={path}')
    return int(report.pass_rate < 1)


if __name__ == '__main__':
    raise SystemExit(main())
