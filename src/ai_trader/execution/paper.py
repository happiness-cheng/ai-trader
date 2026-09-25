"""仅用于测试和演示的内存纸面交易适配器。"""

from collections.abc import Callable
from datetime import UTC, datetime
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


class PaperBrokerAdapter:
    """用 `proposal_id` 防止重复纸面委托。"""

    def __init__(
        self,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._id_factory = id_factory or (lambda: f"paper-{uuid4()}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._orders_by_proposal: dict[str, Order] = {}
        self._lock = Lock()

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
