"""只读检查 Paper 持久状态，不创建数据库或执行 schema 变更。"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ai_trader.domain.trading import Order, TradeProposal

TABLES = ('agent_runs', 'run_events', 'trade_proposals', 'approvals', 'order_reservations', 'orders')


def check_sql(directory: Path, *, integrity: bool = False) -> None:
    database = directory / 'workflow.db'
    with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as db:
        for table in TABLES:
            db.execute(f'SELECT 1 FROM {table} LIMIT 1')  # 表名仅来自固定白名单。
        if integrity and db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')


def read_ledger(directory: Path) -> tuple[Order, ...]:
    return read_ledger_file(directory / 'paper-ledger.json')


def read_ledger_file(path: Path) -> tuple[Order, ...]:
    if not path.exists():
        return ()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise TypeError('invalid Paper ledger')
    orders = tuple(Order.model_validate(raw) for raw in data)
    if len({order.proposal_id for order in orders}) != len(orders):
        raise ValueError('duplicate proposal in Paper ledger')
    if len({order.order_id for order in orders}) != len(orders):
        raise ValueError('duplicate order identity in Paper ledger')
    return orders


def check_order_consistency(directory: Path, orders: tuple[Order, ...]) -> None:
    """SQL已确认事实必须存在且匹配；未确认broker订单保留给reconcile。"""
    ledger = {order.proposal_id: order for order in orders}
    database = directory / 'workflow.db'
    with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as db:
        confirmed = set()
        for order_id, proposal_id, payload in db.execute('SELECT order_id, proposal_id, payload FROM orders'):
            order = Order.model_validate_json(payload)
            if order.order_id != order_id or order.proposal_id != proposal_id:
                raise ValueError('SQL order identity mismatch')
            if ledger.get(proposal_id) != order:
                raise ValueError('confirmed SQL order missing or different in Paper ledger')
            confirmed.add(proposal_id)
        for proposal_id in set(ledger) - confirmed:
            row = db.execute(
                'SELECT p.payload FROM trade_proposals p JOIN order_reservations r '
                'ON p.proposal_id = r.proposal_id WHERE p.proposal_id = ? '
                'AND r.status IN (?, ?)', (proposal_id, 'reserved', 'execution_unknown'),
            ).fetchone()
            if row is None:
                raise ValueError('unconfirmed Paper order has no reserved SQL proposal')
            proposal = TradeProposal.model_validate_json(row[0])
            order = ledger[proposal_id]
            if (order.symbol, order.side, order.quantity, order.price) != (
                proposal.symbol, proposal.side, proposal.quantity, proposal.limit_price,
            ):
                raise ValueError('unconfirmed Paper order differs from reserved SQL proposal')
