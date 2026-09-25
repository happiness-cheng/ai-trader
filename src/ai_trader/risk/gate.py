"""独立于 LLM 的确定性交易风控门。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ai_trader.domain.trading import (
    AccountSnapshot,
    OrderSide,
    Position,
    Quote,
    RiskDecision,
    RiskOutcome,
    TradeProposal,
)


@dataclass(frozen=True)
class RiskPolicy:
    max_quote_age_seconds: int = 30
    max_position_ratio: Decimal = Decimal("0.15")
    max_total_positions: int = 8
    max_daily_loss_ratio: Decimal = Decimal("0.03")
    require_human_approval: bool = True


@dataclass(frozen=True)
class RiskContext:
    proposal: TradeProposal
    quote: Quote
    account: AccountSnapshot
    positions: tuple[Position, ...]
    open_order_symbols: frozenset[str]
    trading_enabled: bool
    now: datetime


_REASONS = {
    "TRADING_DISABLED": "交易总开关已关闭",
    "PROPOSAL_EXPIRED": "交易提案已过期",
    "QUOTE_SYMBOL_MISMATCH": "行情标的与提案不一致",
    "STALE_QUOTE": "行情数据已过期",
    "POSITION_LIMIT_EXCEEDED": "单一标的仓位超过上限",
    "MAX_POSITIONS_EXCEEDED": "持仓标的数超过上限",
    "DAILY_LOSS_LIMIT_EXCEEDED": "当日亏损超过上限",
    "INSUFFICIENT_CASH": "可用资金不足",
    "DUPLICATE_ORDER": "同一标的已有未完成委托",
}


class RiskGate:
    """对交易提案执行可重现的硬规则检查。"""

    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def evaluate(self, context: RiskContext) -> RiskDecision:
        codes: list[str] = []
        proposal = context.proposal

        if not context.trading_enabled:
            codes.append("TRADING_DISABLED")
        if proposal.is_expired(context.now):
            codes.append("PROPOSAL_EXPIRED")
        if context.quote.symbol != proposal.symbol:
            codes.append("QUOTE_SYMBOL_MISMATCH")

        quote_age = (context.now - context.quote.as_of).total_seconds()
        if quote_age < 0 or quote_age > self.policy.max_quote_age_seconds:
            codes.append("STALE_QUOTE")

        if proposal.symbol in context.open_order_symbols:
            codes.append("DUPLICATE_ORDER")

        if context.account.daily_pnl < 0:
            loss_ratio = abs(context.account.daily_pnl) / context.account.total_equity
            if loss_ratio >= self.policy.max_daily_loss_ratio:
                codes.append("DAILY_LOSS_LIMIT_EXCEEDED")

        if proposal.side is OrderSide.BUY:
            order_value = proposal.quantity * context.quote.price
            if order_value > context.account.available_cash:
                codes.append("INSUFFICIENT_CASH")

            existing_value = sum(
                position.market_value
                for position in context.positions
                if position.symbol == proposal.symbol
            )
            target_ratio = (existing_value + order_value) / context.account.total_equity
            if target_ratio > self.policy.max_position_ratio:
                codes.append("POSITION_LIMIT_EXCEEDED")

            held_symbols = {position.symbol for position in context.positions}
            if (
                proposal.symbol not in held_symbols
                and len(held_symbols) >= self.policy.max_total_positions
            ):
                codes.append("MAX_POSITIONS_EXCEEDED")

        if codes:
            return RiskDecision(
                proposal_id=proposal.proposal_id,
                outcome=RiskOutcome.REJECT,
                codes=tuple(codes),
                reasons=tuple(_REASONS[code] for code in codes),
            )

        if self.policy.require_human_approval:
            return RiskDecision(
                proposal_id=proposal.proposal_id,
                outcome=RiskOutcome.REQUIRE_HUMAN,
                codes=("HUMAN_APPROVAL_REQUIRED",),
                reasons=("交易提案需要人工审批",),
            )

        return RiskDecision(
            proposal_id=proposal.proposal_id,
            outcome=RiskOutcome.APPROVE,
            codes=("POLICY_APPROVED",),
            reasons=("所有确定性风控规则已通过",),
        )
