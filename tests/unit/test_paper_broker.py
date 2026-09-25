from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from time import sleep

import pytest

from ai_trader.domain.trading import (
    OrderSide,
    OrderStatus,
    RiskDecision,
    RiskOutcome,
    TradeProposal,
)
from ai_trader.execution.base import ExecutionDenied
from ai_trader.execution.paper import PaperBrokerAdapter


def make_proposal():
    now = datetime.now(UTC)
    return TradeProposal(
        proposal_id="proposal-001",
        symbol="600519",
        side=OrderSide.BUY,
        quantity=100,
        limit_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("115"),
        evidence_refs=("tool-call-001",),
        created_at=now,
        expires_at=now + timedelta(minutes=2),
    )


def approved_decision():
    return RiskDecision(
        proposal_id="proposal-001",
        outcome=RiskOutcome.APPROVE,
        codes=("HUMAN_APPROVED",),
        reasons=("测试审批通过",),
    )


def rejected_decision():
    return RiskDecision(
        proposal_id="proposal-001",
        outcome=RiskOutcome.REJECT,
        codes=("TRADING_DISABLED",),
        reasons=("交易开关已关闭",),
    )


def test_approved_proposal_creates_filled_order():
    broker = PaperBrokerAdapter(id_factory=lambda: "paper-001")
    proposal = make_proposal()
    order = broker.submit(proposal, approved_decision())
    assert order.status is OrderStatus.FILLED
    assert order.proposal_id == proposal.proposal_id


def test_same_proposal_is_idempotent():
    broker = PaperBrokerAdapter(id_factory=lambda: "paper-001")
    proposal = make_proposal()
    first = broker.submit(proposal, approved_decision())
    second = broker.submit(proposal, approved_decision())
    assert second.order_id == first.order_id
    assert len(broker.list_orders()) == 1


def test_unapproved_proposal_cannot_execute():
    broker = PaperBrokerAdapter(id_factory=lambda: "paper-001")
    with pytest.raises(ExecutionDenied, match="approval"):
        broker.submit(make_proposal(), rejected_decision())


def test_approval_must_match_proposal():
    broker = PaperBrokerAdapter(id_factory=lambda: "paper-001")
    wrong_approval = approved_decision().model_copy(update={"proposal_id": "proposal-002"})
    with pytest.raises(ExecutionDenied, match="proposal"):
        broker.submit(make_proposal(), wrong_approval)


def test_expired_proposal_cannot_execute():
    proposal = make_proposal()
    broker = PaperBrokerAdapter(
        id_factory=lambda: "paper-001",
        clock=lambda: proposal.expires_at,
    )
    with pytest.raises(ExecutionDenied, match="expired"):
        broker.submit(proposal, approved_decision())


def test_concurrent_duplicate_submission_returns_one_order():
    ids = count(1)

    def slow_id_factory():
        value = next(ids)
        sleep(0.01)
        return f"paper-{value:03d}"

    broker = PaperBrokerAdapter(id_factory=slow_id_factory)
    proposal = make_proposal()
    with ThreadPoolExecutor(max_workers=8) as pool:
        orders = list(
            pool.map(lambda _: broker.submit(proposal, approved_decision()), range(16))
        )

    assert {order.order_id for order in orders} == {"paper-001"}
    assert len(broker.list_orders()) == 1
