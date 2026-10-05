from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_trader.domain.trading import OrderSide, TradeProposal


def make_proposal(**overrides):
    now = datetime.now(UTC)
    values = {
        "proposal_id": "proposal-001",
        "symbol": "600519",
        "side": OrderSide.BUY,
        "quantity": 100,
        "limit_price": Decimal("1450.00"),
        "stop_loss": Decimal("1377.50"),
        "take_profit": Decimal("1667.50"),
        "evidence_refs": ("tool-call-001",),
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
    }
    values.update(overrides)
    return TradeProposal(**values)


def test_valid_trade_proposal():
    proposal = make_proposal()
    assert proposal.quantity == 100
    assert proposal.symbol == "600519"


@pytest.mark.parametrize(
    ("field", "value"),
    [("quantity", 0), ("quantity", 50), ("limit_price", Decimal(0))],
)
def test_invalid_trade_proposal(field, value):
    with pytest.raises(ValidationError):
        make_proposal(**{field: value})


def test_expired_trade_proposal_is_observable():
    now = datetime.now(UTC)
    proposal = make_proposal(
        created_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    assert proposal.is_expired(datetime.now(UTC)) is True


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValidationError):
        make_proposal(created_at=datetime.now())  # noqa: DTZ005 - 本用例就是要验证 naive 时间被拒


def test_sell_quantity_may_be_an_odd_lot():
    proposal = make_proposal(side=OrderSide.SELL, quantity=50)
    assert proposal.quantity == 50
