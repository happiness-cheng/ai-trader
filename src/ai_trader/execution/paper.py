"""仅用于测试和演示的内存纸面交易适配器。"""

from collections.abc import Callable
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from ai_trader.domain.trading import (
    Order,
    OrderStatus,
    RiskDecision,
    RiskOutcome,
    TradeProposal,
)
from ai_trader.execution.base import ExecutionDenied
from ai_trader.state_health import read_ledger_file


class PaperBrokerAdapter:
    """用 `proposal_id` 防止重复纸面委托。"""

    def __init__(
        self,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        ledger_path: Path | None = None,
    ):
        self._id_factory = id_factory or (lambda: f"paper-{uuid4()}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._orders_by_proposal: dict[str, Order] = {}
        self._lock = Lock()
        self._ledger_path = ledger_path
        if ledger_path is not None and ledger_path.exists():
            for order in read_ledger_file(ledger_path):
                self._orders_by_proposal[order.proposal_id] = order

    def submit(self, proposal: TradeProposal, decision: RiskDecision) -> Order:
        if decision.outcome is not RiskOutcome.APPROVE:
            raise ExecutionDenied("explicit approval is required before execution")
        if decision.proposal_id != proposal.proposal_id:
            raise ExecutionDenied("approval does not match proposal")
        if proposal.is_expired(self._clock()):
            raise ExecutionDenied("proposal has expired")

        with self._lock:
            existing = self._orders_by_proposal.get(proposal.proposal_id)
            if existing is not None:
                return existing

            order = Order(
                order_id=self._id_factory(),
                proposal_id=proposal.proposal_id,
                symbol=proposal.symbol,
                side=proposal.side,
                quantity=proposal.quantity,
                price=proposal.limit_price,
                status=OrderStatus.FILLED,
                created_at=self._clock(),
            )
            if self._ledger_path is not None:
                # 单 writer 的模拟 Broker：先持久化自己的订单，再返回响应。
                # 这份 ledger 独立于应用数据库，用于验证响应丢失后的重启对账。
                self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self._ledger_path.with_name(f'{self._ledger_path.name}.{uuid4()}.tmp')
                data = [item.model_dump(mode='json') for item in self._orders_by_proposal.values()]
                data.append(order.model_dump(mode='json'))
                with temp_path.open('w', encoding='utf-8') as stream:
                    json.dump(data, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self._ledger_path)
            self._orders_by_proposal[proposal.proposal_id] = order
            return order

    def get_order(self, order_id: str) -> Order | None:
        with self._lock:
            return next(
                (
                    order
                    for order in self._orders_by_proposal.values()
                    if order.order_id == order_id
                ),
                None,
            )

    def list_orders(self) -> tuple[Order, ...]:
        with self._lock:
            return tuple(self._orders_by_proposal.values())
