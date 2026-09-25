from ai_trader.demo import build_demo_context
from ai_trader.domain.trading import RiskDecision, RiskOutcome
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.risk.gate import RiskGate


def test_proposal_requires_approval_then_executes_once():
    context = build_demo_context()
    pending = RiskGate().evaluate(context)
    assert pending.outcome is RiskOutcome.REQUIRE_HUMAN

    approved = RiskDecision(
        proposal_id=context.proposal.proposal_id,
        outcome=RiskOutcome.APPROVE,
        codes=("HUMAN_APPROVED",),
        reasons=("离线演示审批",),
    )
    broker = PaperBrokerAdapter(
        id_factory=lambda: "paper-demo-001",
        clock=lambda: context.now,
    )
    first = broker.submit(context.proposal, approved)
    second = broker.submit(context.proposal, approved)
    assert first.order_id == second.order_id
    assert len(broker.list_orders()) == 1
