from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ai_trader.domain.trading import Order, OrderSide, OrderStatus, TradeProposal
from ai_trader.persistence.trading import (
    ApprovalRecord,
    DuplicateReservation,
    SqlTradingRepository,
)


NOW = datetime(2026, 7, 11, 4, 0, tzinfo=UTC)


def proposal():
    return TradeProposal(
        proposal_id="proposal-001", symbol="600519", side=OrderSide.BUY,
        quantity=100, limit_price=Decimal("100"), stop_loss=Decimal("95"),
        take_profit=Decimal("115"), evidence_refs=("tool-1",),
        created_at=NOW, expires_at=NOW + timedelta(minutes=5),
    )


@pytest.fixture
def repository(tmp_path):
    value = SqlTradingRepository(f"sqlite:///{tmp_path / 'trading.db'}")
    yield value
    value.close()


def test_proposal_round_trip(repository):
    repository.save_proposal(proposal())
    assert repository.get_proposal("proposal-001") == proposal()


def test_valid_approval_is_bound_and_metadata_redacted(repository):
    repository.save_proposal(proposal())
    approval = ApprovalRecord(
        approval_id="approval-001", proposal_id="proposal-001",
        reviewer_id="reviewer-1", approved=True, created_at=NOW,
        expires_at=NOW + timedelta(minutes=2), metadata={"api_key": "secret"},
    )
    repository.record_approval(approval)
    loaded = repository.get_valid_approval("proposal-001", NOW + timedelta(seconds=1))
    assert loaded is not None
    assert loaded.metadata == {"api_key": "[REDACTED]"}
    assert repository.get_valid_approval("proposal-001", approval.expires_at) is None


def test_order_reservation_is_idempotent(repository):
    repository.save_proposal(proposal())
    repository.reserve_order("proposal-001", "reservation-001", NOW)
    with pytest.raises(DuplicateReservation):
        repository.reserve_order("proposal-001", "reservation-002", NOW)


def test_order_round_trip_by_proposal(repository):
    repository.save_proposal(proposal())
    repository.reserve_order("proposal-001", "reservation-001", NOW)
    order = Order(
        order_id="paper-001", proposal_id="proposal-001", symbol="600519",
        side=OrderSide.BUY, quantity=100, price=Decimal("100"),
        status=OrderStatus.FILLED, created_at=NOW,
    )
    repository.save_order(order)
    assert repository.get_order_by_proposal("proposal-001") == order
