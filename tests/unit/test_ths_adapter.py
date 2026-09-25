import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ai_trader.domain.trading import OrderSide, OrderStatus, RiskDecision, RiskOutcome, TradeProposal
from ai_trader.execution.ths import LiveExecutionDisabled, ThsBrokerAdapter
from ai_trader.settings import Settings


def proposal():
    now = datetime.now(UTC)
    return TradeProposal(
        proposal_id="proposal-001", symbol="600519", side=OrderSide.BUY,
        quantity=100, limit_price=Decimal("100"), stop_loss=Decimal("95"),
        take_profit=Decimal("115"), evidence_refs=("tool-1",), created_at=now,
        expires_at=now + timedelta(minutes=2),
    )


def approval():
    return RiskDecision(
        proposal_id="proposal-001", outcome=RiskOutcome.APPROVE,
        codes=("HUMAN_APPROVED",), reasons=("approved",),
    )


def test_disabled_adapter_does_not_import_legacy_trader():
    sys.modules.pop("ths_trader", None)
    with pytest.raises(LiveExecutionDisabled):
        ThsBrokerAdapter(Settings(_env_file=None))
    assert "ths_trader" not in sys.modules


def test_enabled_fake_submission_is_only_acknowledged():
    class FakeTrader:
        def buy(self, symbol, quantity):
            return True

        def sell(self, symbol, quantity):
            return True

    settings = Settings(
        _env_file=None, trading_enabled=True, paper_trading_only=False
    )
    adapter = ThsBrokerAdapter(settings, trader_factory=FakeTrader)
    order = adapter.submit(proposal(), approval())
    assert order.status is OrderStatus.ACKNOWLEDGED
