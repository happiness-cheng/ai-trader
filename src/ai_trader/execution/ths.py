"""默认禁用、延迟导入旧同花顺 GUI 驱动的 Broker 边界。"""

from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from typing import Protocol, cast
from uuid import uuid4

from ai_trader.domain.trading import (
    Order,
    OrderSide,
    OrderStatus,
    RiskDecision,
    RiskOutcome,
    TradeProposal,
)
from ai_trader.execution.base import ExecutionDenied
from ai_trader.settings import Settings


class LiveExecutionDisabled(ExecutionDenied):
    pass


class _Trader(Protocol):
    def buy(self, stock_code: str, quantity: int) -> bool: ...
    def sell(self, stock_code: str, quantity: int) -> bool: ...


def _legacy_factory() -> _Trader:
    module = import_module("ths_trader")
    constructor = cast(Callable[[], object], getattr(module, "THSTrader"))
    return cast(_Trader, constructor())


class ThsBrokerAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        trader_factory: Callable[[], _Trader] | None = None,
    ) -> None:
        if not settings.live_execution_allowed:
            raise LiveExecutionDisabled("live execution switches are not enabled")
        self._trader = (trader_factory or _legacy_factory)()
        self._orders: dict[str, Order] = {}

    def submit(self, proposal: TradeProposal, decision: RiskDecision) -> Order:
        if decision.outcome is not RiskOutcome.APPROVE:
            raise ExecutionDenied("explicit approval is required")
        if decision.proposal_id != proposal.proposal_id:
            raise ExecutionDenied("approval does not match proposal")
        existing = next(
            (item for item in self._orders.values() if item.proposal_id == proposal.proposal_id),
            None,
        )
        if existing is not None:
            return existing

        submitted = (
            self._trader.buy(proposal.symbol, proposal.quantity)
            if proposal.side is OrderSide.BUY
            else self._trader.sell(proposal.symbol, proposal.quantity)
        )
        order = Order(
            order_id=f"ths-{uuid4()}",
            proposal_id=proposal.proposal_id,
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=proposal.quantity,
            price=proposal.limit_price,
            status=OrderStatus.ACKNOWLEDGED if submitted else OrderStatus.REJECTED,
            created_at=datetime.now(UTC),
        )
        self._orders[order.order_id] = order
        return order

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def list_orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())
