"""仅负责状态、意图和事件持久化的 Agent 编排器。"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from ai_trader.agents.state import AgentRun, EventType, RunState
from ai_trader.persistence.runs import SqlRunRepository


class PersistentOrchestrator:
    def __init__(
        self,
        repository: SqlRunRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4()}")

    def create_run(self, goal: str, *, run_id: str | None = None) -> AgentRun:
        now = self._clock()
        run = AgentRun(
            run_id=run_id or self._id_factory("run"),
            goal=goal,
            state=RunState.CREATED,
            version=0,
            created_at=now,
            updated_at=now,
        )
        self._repository.create(run)
        return run

    def start(self, run_id: str) -> AgentRun:
        return self.transition(run_id, RunState.PLANNING)

    def transition(
        self,
        run_id: str,
        target: RunState,
        payload: dict[str, object] | None = None,
    ) -> AgentRun:
        current = self._repository.get(run_id)
        event_payload = payload or {}
        if target in {RunState.TOOL_RUNNING, RunState.EXECUTING}:
            current = self._repository.append_event(
                run_id=run_id,
                expected_version=current.version,
                event_type=EventType.INTENT_RECORDED,
                payload={"target": target.value, **event_payload},
                now=self._clock(),
                event_id=self._id_factory("event"),
            )
        return self._repository.append_transition(
            run_id=run_id,
            expected_version=current.version,
            target=target,
            event_type=EventType.STATE_CHANGED,
            payload=event_payload,
            now=self._clock(),
            event_id=self._id_factory("event"),
        )

    def request_approval(
        self, run_id: str, proposal_summary: dict[str, object]
    ) -> AgentRun:
        current = self._repository.get(run_id)
        return self._repository.append_transition(
            run_id=run_id,
            expected_version=current.version,
            target=RunState.WAITING_APPROVAL,
            event_type=EventType.APPROVAL_REQUESTED,
            payload=proposal_summary,
            now=self._clock(),
            event_id=self._id_factory("event"),
        )

    def resume_approved(
        self, run_id: str, proposal_id: str, reviewer_id: str
    ) -> AgentRun:
        current = self._repository.get(run_id)
        current = self._repository.append_event(
            run_id=run_id,
            expected_version=current.version,
            event_type=EventType.APPROVAL_RECEIVED,
            payload={"proposal_id": proposal_id, "reviewer_id": reviewer_id},
            now=self._clock(),
            event_id=self._id_factory("event"),
        )
        return self.transition(
            current.run_id,
            RunState.EXECUTING,
            {"proposal_id": proposal_id},
        )

    def cancel(self, run_id: str, reason: str) -> AgentRun:
        current = self._repository.get(run_id)
        return self._repository.append_transition(
            run_id=run_id,
            expected_version=current.version,
            target=RunState.CANCELLED,
            event_type=EventType.CANCEL_REQUESTED,
            payload={"reason": reason},
            now=self._clock(),
            event_id=self._id_factory("event"),
        )

    def recoverable_runs(self) -> tuple[AgentRun, ...]:
        return self._repository.list_recoverable()
