"""显式装配只读 Agent、人工审批与 Paper Broker 的本地服务。"""

import argparse
import json
import sqlite3
import sys
from asyncio import Lock
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.routing import Mount

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.runner import ProductionAgentRunner
from ai_trader.agents.state import RunState
from ai_trader.api.app import ApiDependencies, create_app
from ai_trader.domain.trading import (
    AccountSnapshot,
    Order,
    OrderSide,
    Position,
    Quote,
    TradeProposal,
)
from ai_trader.execution.coordinator import ExecutionCoordinator
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.execution.workflow import TradingWorkflow
from ai_trader.models.gateway import (
    ModelGateway,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.persistence.trading import SqlTradingRepository
from ai_trader.risk.gate import RiskContext, RiskGate, RiskPolicy
from ai_trader.state_health import check_order_consistency, check_sql, read_ledger
from ai_trader.tools.runtime import ToolDefinition, ToolExecutionContext, ToolRuntime

_SYMBOL = '600519'
_PRICE = Decimal(100)

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


@contextmanager
def _single_writer(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.paper-service.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('state directory already has a Paper service writer') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class _QuoteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(pattern=r'^\d{6}$')


class _OfflinePaperProvider:
    """可重复运行的固定脚本；usage 是测试数值，不是模型效果证据。"""

    name = 'offline-fixture'

    def complete(self, request: ModelRequest) -> ModelResponse:
        if isinstance(request.messages[-1].get('content'), str):
            return ModelResponse(
                provider=self.name, model=request.model,
                tool_calls=(ModelToolCall(
                    call_id='fixture-quote', name='get_quote', arguments={'symbol': _SYMBOL},
                ),),
                usage=ModelUsage(input_tokens=15, output_tokens=5), attempts=1, elapsed_ms=0,
            )
        return ModelResponse(
            provider=self.name, model=request.model,
            text=json.dumps({'action': 'propose', 'trade': {
                'symbol': _SYMBOL, 'side': 'buy', 'quantity': 100, 'limit_price': '100',
                'stop_loss': '95', 'take_profit': '115', 'evidence_refs': ['get_quote'],
            }}),
            usage=ModelUsage(input_tokens=20, output_tokens=10), attempts=1, elapsed_ms=0,
        )


def create_paper_app(
    *, state_directory: Path, provider: ModelProvider | None = None,
    model: str | None = None, clock: Callable[[], datetime] | None = None,
    confirmed_event_sink: Callable[[str, Order], None] | None = None,
    production: bool = False, operator_token: str | None = None,
) -> FastAPI:
    """调用者显式选择状态目录；默认不读取配置、凭据或访问网络。"""
    if not state_directory.is_absolute():
        raise ValueError('absolute state directory required')
    if (provider is None) != (model is None) or (model is not None and not model.strip()):
        raise ValueError('provider and nonempty model must be supplied together')
    if production and (operator_token is None or len(operator_token) < 32):
        raise ValueError('production requires explicit operator token of at least 32 characters')
    if operator_token is not None and (len(operator_token) > 4096 or any(c.isspace() for c in operator_token)):
        raise ValueError('invalid operator token')
    token_digest = sha256(operator_token.encode()).digest() if operator_token else None
    now = clock or (lambda: datetime.now(UTC))
    write_lock: Lock | None = None
    active_broker: PaperBrokerAdapter | None = None

    def runtime_checks() -> dict[str, str]:
        checks = {'sql': 'ok', 'ledger': 'ok'}
        try:
            check_sql(state_directory)
        except (OSError, sqlite3.Error):
            checks['sql'] = 'unavailable'
        try:
            orders = read_ledger(state_directory)
            if active_broker is None or orders != active_broker.list_orders():
                raise ValueError('ledger differs from active Paper state')
            check_order_consistency(state_directory, orders)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            checks['ledger'] = 'unavailable'
        return checks

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal write_lock, active_broker
        # 先取得进程文件锁，再打开状态；工厂本身没有磁盘写入。
        with _single_writer(state_directory), ExitStack() as resources:
            url = f"sqlite:///{(state_directory / 'workflow.db').as_posix()}"
            runs = SqlRunRepository(url)
            resources.callback(runs.close)
            trades = SqlTradingRepository(url)
            resources.callback(trades.close)
            orchestration = PersistentOrchestrator(runs, clock=now)
            broker = PaperBrokerAdapter(clock=now, ledger_path=state_directory / 'paper-ledger.json')
            active_broker = broker
            policy = RiskPolicy()
            coordinator = ExecutionCoordinator(trades, broker, clock=now, risk_gate=RiskGate(policy))

            def quote(data: _QuoteInput) -> Quote:
                if data.symbol != _SYMBOL:
                    raise ValueError('offline fixture only supports 600519')
                return Quote(symbol=_SYMBOL, price=_PRICE, as_of=now())

            tools = ToolRuntime()
            tools.register(ToolDefinition(
                name='get_quote', description='Offline fixture quote for 600519 at price 100',
                input_model=_QuoteInput, output_model=Quote, handler=quote, permission='market:read',
            ))
            runner = ProductionAgentRunner(
                ModelGateway([provider if provider is not None else _OfflinePaperProvider()]),
                tools, orchestration, ToolExecutionContext(permissions=frozenset({'market:read'})),
                model=model or 'fixture',
            )

            def account_snapshot() -> tuple[AccountSnapshot, tuple[Position, ...]]:
                quantity = 0
                cash = Decimal(50000)
                for order in broker.list_orders():
                    sign = 1 if order.side is OrderSide.BUY else -1
                    quantity += sign * order.quantity
                    cash -= sign * order.quantity * order.price
                if quantity < 0 or cash < 0:
                    raise ValueError('invalid Paper fixture account')
                positions = (Position(
                    symbol=_SYMBOL, quantity=quantity, average_price=_PRICE, current_price=_PRICE,
                ),) if quantity else ()
                return AccountSnapshot(
                    total_equity=Decimal(50000) + cash + quantity * _PRICE, available_cash=cash,
                ), positions

            def context(proposal: TradeProposal) -> RiskContext:
                if proposal.symbol != _SYMBOL or proposal.limit_price != _PRICE:
                    raise ValueError('Paper fixture only supports 600519 at limit price 100')
                account, positions = account_snapshot()
                held = sum(position.quantity for position in positions)
                if proposal.side is OrderSide.SELL and proposal.quantity > held:
                    raise ValueError('Paper fixture cannot sell unheld shares')
                current = now()
                return RiskContext(
                    proposal=proposal, quote=Quote(symbol=_SYMBOL, price=_PRICE, as_of=current),
                    account=account,
                    positions=positions, open_order_symbols=frozenset(),
                    trading_enabled=True, now=current,
                )

            def constraints_source(run_id: str | None) -> dict[str, object]:
                account, positions = account_snapshot()
                current_run = runs.get(run_id) if run_id is not None else None
                return {
                    'execution_mode': 'paper', 'market_data': 'fixture', 'trading_enabled': True,
                    'run_id': run_id,
                    'run_state_at_snapshot': current_run.state.value if current_run else None,
                    'execution_unknown': current_run is not None and current_run.state in {
                        RunState.EXECUTING, RunState.VERIFYING,
                    },
                    'current_proposal': None, 'risk_status': 'not_evaluated',
                    'account': account.model_dump(mode='json'),
                    'positions': [position.model_dump(mode='json') for position in positions],
                    'supported_symbol': _SYMBOL, 'fixture_quote_price': str(_PRICE),
                    'risk_policy': {'max_position_ratio': str(policy.max_position_ratio),
                                    'require_human_approval': policy.require_human_approval},
                    'snapshot_at': now().isoformat(),
                }

            workflow = TradingWorkflow(
                runner, orchestration, runs, trades, coordinator, context, clock=now,
                constraints_source=constraints_source,
                confirmed_event_sink=confirmed_event_sink,
            )
            child = create_app(ApiDependencies(orchestration, runs, coordinator, workflow=workflow))
            mount = Mount('/', app=child)
            app.router.routes.append(mount)
            write_lock = Lock()
            try:
                yield
            finally:
                write_lock = None
                active_broker = None
                app.router.routes.remove(mount)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def local_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            local = request.client is not None and ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if not local:
            return JSONResponse(status_code=403, content={'detail': 'trusted loopback clients only'})
        if (production or token_digest is not None) and request.url.path not in {'/health', '/ready'}:
            supplied = request.headers.get('Authorization', '')
            valid = supplied.startswith('Bearer ') and token_digest is not None and compare_digest(
                sha256(supplied[7:].encode()).digest(), token_digest,
            )
            if not valid:
                return JSONResponse(status_code=401, content={'detail': 'operator authentication required'},
                                    headers={'WWW-Authenticate': 'Bearer'})
        if request.method == 'POST':
            if request.url.path.startswith('/runs/') and request.url.path.rsplit('/', 1)[-1] in {
                'analyze', 'approve', 'reconcile',
            } and await request.body():
                return JSONResponse(status_code=422, content={'detail': 'endpoint accepts no body'})
            if write_lock is None:
                return JSONResponse(status_code=503, content={'detail': 'Paper service not started'})
            # 串行写请求使风控复查和成交在本进程内不会跨 run 交错。
            async with write_lock:
                if any(value != 'ok' for value in runtime_checks().values()):
                    return JSONResponse(status_code=503, content={'detail': 'Paper state unavailable; restore or reconcile required'})
                return await call_next(request)
        return await call_next(request)

    @app.get('/ready')
    def readiness() -> JSONResponse:
        if active_broker is None:
            return JSONResponse(status_code=503, content={'status': 'not_ready', 'checks': {'service': 'not_started'}})
        checks = runtime_checks()
        available = all(value == 'ok' for value in checks.values())
        return JSONResponse(status_code=200 if available else 503,
                            content={'status': 'ready' if available else 'not_ready', 'checks': checks})

    return app


def _loopback_host(value: str) -> str:
    try:
        address = ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('literal loopback IP address required') from exc
    if not address.is_loopback:
        raise argparse.ArgumentTypeError('loopback IP address required')
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-directory', type=Path, required=True, help='独占的绝对状态目录')
    parser.add_argument('--host', type=_loopback_host, default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--operator-token-file', type=Path, help='读取已有凭据文件，不生成或修改它')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        token = args.operator_token_file.read_text(encoding='utf-8').strip() if args.operator_token_file else None
        app = create_paper_app(state_directory=args.state_directory,
                               production=args.production, operator_token=token)
    except OSError:
        parser.error('operator token file cannot be read')
    except ValueError as exc:
        parser.error(str(exc))
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
