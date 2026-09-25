"""交易执行边界。"""

from typing import Protocol

from ai_trader.domain.trading import Order, RiskDecision, TradeProposal


class ExecutionDenied(RuntimeError):
    """交易未获得执行权限。"""


class BrokerAdapter(Protocol):
    def submit(self, proposal: TradeProposal, decision: RiskDecision) -> Order: ...

    def get_order(self, order_id: str) -> Order | None: ...

    def list_orders(self) -> tuple[Order, ...]: ...
