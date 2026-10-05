from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_trader.domain.trading import (
    AccountSnapshot,
    OrderSide,
    Position,
    Quote,
    RiskOutcome,
    TradeProposal,
)
from ai_trader.risk.gate import RiskContext, RiskGate

NOW = datetime(2026, 7, 11, 2, 0, tzinfo=UTC)


def make_context(**overrides):
    proposal = TradeProposal(
        proposal_id="proposal-001",
        symbol="600519",
        side=OrderSide.BUY,
        quantity=100,
        limit_price=Decimal(100),
        stop_loss=Decimal(95),
        take_profit=Decimal(115),
        evidence_refs=("tool-call-001",),
        created_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(minutes=2),
    )
    values = {
        "proposal": proposal,
        "quote": Quote(symbol="600519", price=Decimal(100), as_of=NOW),
        "account": AccountSnapshot(
            total_equity=Decimal(100000),
            available_cash=Decimal(50000),
            daily_pnl=Decimal(0),
        ),
        "positions": (),
        "open_order_symbols": frozenset(),
        "trading_enabled": True,
        "now": NOW,
    }
    values.update(overrides)
    return RiskContext(**values)


def test_kill_switch_rejects():
    decision = RiskGate().evaluate(make_context(trading_enabled=False))
    assert decision.outcome is RiskOutcome.REJECT
    assert "TRADING_DISABLED" in decision.codes


def test_expired_quote_rejects():
    quote = Quote(
        symbol="600519",
        price=Decimal(100),
        as_of=NOW - timedelta(seconds=31),
    )
    decision = RiskGate().evaluate(make_context(quote=quote))
    assert "STALE_QUOTE" in decision.codes


def test_position_limit_rejects():
    position = Position(
        symbol="600519",
        quantity=100,
        average_price=Decimal(100),
        current_price=Decimal(100),
    )
    proposal = make_context().proposal.model_copy(update={"quantity": 100})
    account = AccountSnapshot(
        total_equity=Decimal(20000),
        available_cash=Decimal(20000),
        daily_pnl=Decimal(0),
    )
    decision = RiskGate().evaluate(
        make_context(proposal=proposal, account=account, positions=(position,))
    )
    assert "POSITION_LIMIT_EXCEEDED" in decision.codes


def test_duplicate_open_order_rejects():
    decision = RiskGate().evaluate(
        make_context(open_order_symbols=frozenset({"600519"}))
    )
    assert "DUPLICATE_ORDER" in decision.codes


def test_safe_paper_trade_requires_human():
    decision = RiskGate().evaluate(make_context())
    assert decision.outcome is RiskOutcome.REQUIRE_HUMAN
    assert decision.codes == ("HUMAN_APPROVAL_REQUIRED",)
