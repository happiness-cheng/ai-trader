"""风控、人工审批、幂等保留和 Broker 执行的协调器。"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ai_trader.domain.trading import Order, RiskDecision, RiskOutcome
from ai_trader.execution.base import BrokerAdapter
from ai_trader.persistence.trading import (
    ApprovalRecord,
    DuplicateReservation,
    SqlTradingRepository,
)
from ai_trader.risk.gate import RiskContext, RiskGate


class ExecutionUnknown(RuntimeError):
    pass


class ExecutionCoordinator:
    def __init__(
        self,
        repository: SqlTradingRepository,
        broker: BrokerAdapter,
        *,
        risk_gate: RiskGate | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._repository = repository
        self._broker = broker
        self._risk_gate = risk_gate or RiskGate()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4()}")

    def submit_proposal(self, context: RiskContext) -> RiskDecision:
        self._repository.save_proposal(context.proposal)
        return self._risk_gate.evaluate(context)

    def approve(self, proposal_id: str, reviewer_id: str) -> ApprovalRecord:
        proposal = self._repository.get_proposal(proposal_id)
        now = self._clock()
        if proposal.is_expired(now):
            raise ExecutionUnknown("proposal expired before approval")
        approval = ApprovalRecord(
            approval_id=self._id_factory("approval"),
            proposal_id=proposal_id,
            reviewer_id=reviewer_id,
            approved=True,
            created_at=now,
            expires_at=min(proposal.expires_at, now + timedelta(minutes=2)),
        )
        self._repository.record_approval(approval)
        return approval

    def reject(self, proposal_id: str, reviewer_id: str, reason: str) -> ApprovalRecord:
        now = self._clock()
        approval = ApprovalRecord(
            approval_id=self._id_factory("approval"),
            proposal_id=proposal_id,
            reviewer_id=reviewer_id,
            approved=False,
            created_at=now,
            expires_at=now + timedelta(minutes=2),
            metadata={"reason": reason},
        )
        self._repository.record_approval(approval)
        return approval

    def execute_approved(self, context: RiskContext) -> Order:
        proposal = context.proposal
        existing = self._repository.get_order_by_proposal(proposal.proposal_id)
        if existing is not None:
            return existing
        now = self._clock()
        if proposal.is_expired(now):
            raise ExecutionUnknown("proposal expired before execution")
        approval = self._repository.get_valid_approval(proposal.proposal_id, now)
        if approval is None:
            raise ExecutionUnknown("valid human approval is required")
        risk = self._risk_gate.evaluate(replace(context, now=now))
        if risk.outcome is RiskOutcome.REJECT:
            raise ExecutionUnknown(f"risk rejected: {','.join(risk.codes)}")

        try:
            self._repository.reserve_order(
                proposal.proposal_id, self._id_factory("reservation"), now
            )
        except DuplicateReservation as exc:
            existing = self._repository.get_order_by_proposal(proposal.proposal_id)
            if existing is not None:
                return existing
            raise ExecutionUnknown("execution is already reserved or unknown") from exc

        decision = RiskDecision(
            proposal_id=proposal.proposal_id,
            outcome=RiskOutcome.APPROVE,
            codes=("HUMAN_APPROVED",),
            reasons=(f"approved by {approval.reviewer_id}",),
        )
        try:
            order = self._broker.submit(proposal, decision)
            self._repository.save_order(order)
            return order
        except Exception:
            self._repository.mark_execution_unknown(proposal.proposal_id)
            raise

    def approve_and_execute(
        self, context: RiskContext, reviewer_id: str
    ) -> Order:
        self.submit_proposal(context)
        self.approve(context.proposal.proposal_id, reviewer_id)
        return self.execute_approved(context)

    def reconcile(self, proposal_id: str) -> Order | None:
        persisted = self._repository.get_order_by_proposal(proposal_id)
        if persisted is not None:
            return persisted
        broker_order = next(
            (
                order
                for order in self._broker.list_orders()
                if order.proposal_id == proposal_id
            ),
            None,
        )
        if broker_order is not None:
            self._repository.save_order(broker_order)
        return broker_order
