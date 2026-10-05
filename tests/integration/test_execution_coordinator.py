from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest

from ai_trader.domain.trading import AccountSnapshot, OrderSide, Quote, TradeProposal
from ai_trader.execution.coordinator import ExecutionCoordinator, ExecutionUnknown
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.persistence.trading import SqlTradingRepository
from ai_trader.risk.gate import RiskContext, RiskOutcome

NOW = datetime(2026, 7, 11, 4, 0, tzinfo=UTC)


def make_context():
    proposal = TradeProposal(
        proposal_id="proposal-001", symbol="600519", side=OrderSide.BUY,
        quantity=100, limit_price=Decimal(100), stop_loss=Decimal(95),
        take_profit=Decimal(115), evidence_refs=("tool-1",), created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    return RiskContext(
        proposal=proposal, quote=Quote(symbol="600519", price=Decimal(100), as_of=NOW),
        account=AccountSnapshot(total_equity=Decimal(100000), available_cash=Decimal(50000)),
        positions=(), open_order_symbols=frozenset(), trading_enabled=True, now=NOW,
    )


@pytest.fixture
def coordinator(tmp_path):
    repository = SqlTradingRepository(f"sqlite:///{tmp_path / 'trading.db'}")
    ids = count(1)
    broker = PaperBrokerAdapter(id_factory=lambda: "paper-001", clock=lambda: NOW)
    value = ExecutionCoordinator(
        repository, broker, clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}-{next(ids):03d}",
    )
    yield value
    repository.close()


def test_submit_requires_human_then_approved_execution_is_idempotent(coordinator):
    context = make_context()
    decision = coordinator.submit_proposal(context)
    assert decision.outcome is RiskOutcome.REQUIRE_HUMAN
    coordinator.approve(context.proposal.proposal_id, "reviewer-1")
    first = coordinator.execute_approved(context)
    second = coordinator.execute_approved(context)
    assert first.order_id == second.order_id == "paper-001"


def test_expired_proposal_cannot_be_approved(coordinator):
    context = make_context()
    coordinator.submit_proposal(context)
    coordinator._clock = lambda: context.proposal.expires_at
    with pytest.raises(ExecutionUnknown, match="expired"):
        coordinator.approve(context.proposal.proposal_id, "reviewer-1")
