"""Agent 运行状态和追加式事件契约。"""

import json
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    TOOL_RUNNING = "tool_running"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(str, Enum):
    RUN_CREATED = "run_created"
    STATE_CHANGED = "state_changed"
    INTENT_RECORDED = "intent_recorded"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RECEIVED = "approval_received"
    CANCEL_REQUESTED = "cancel_requested"
    ERROR_RECORDED = "error_recorded"


class InvalidTransition(ValueError):
    """运行状态转移不符合显式状态机。"""


class StateModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value


class AgentRun(StateModel):
    run_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=4000)
    state: RunState
    version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    _validate_created_at = field_validator("created_at")(_aware)
    _validate_updated_at = field_validator("updated_at")(_aware)


class RunEvent(StateModel):
    event_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    event_type: EventType
    state_from: RunState | None = None
    state_to: RunState
    occurred_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)

    _validate_occurred_at = field_validator("occurred_at")(_aware)

    @field_validator("payload")
    @classmethod
    def bound_payload(cls, value: dict[str, object]) -> dict[str, object]:
        if len(json.dumps(value, ensure_ascii=False, default=str)) > 65_536:
            raise ValueError("事件 payload 超过 64 KiB")
        return value


_ALLOWED: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.PLANNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.PLANNING: frozenset(
        {
            RunState.TOOL_RUNNING,
            RunState.WAITING_APPROVAL,
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.TOOL_RUNNING: frozenset(
        {RunState.PLANNING, RunState.WAITING_APPROVAL, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.WAITING_APPROVAL: frozenset(
        {RunState.PLANNING, RunState.EXECUTING, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.EXECUTING: frozenset(
        {RunState.VERIFYING, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.VERIFYING: frozenset(
        {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def transition(run: AgentRun, target: RunState, now: datetime) -> AgentRun:
    _aware(now)
    if now < run.updated_at:
        raise ValueError("transition time cannot be earlier than current state")
    if target not in _ALLOWED[run.state]:
        raise InvalidTransition(f"{run.state.value} -> {target.value} is not allowed")
    return run.model_copy(
        update={"state": target, "version": run.version + 1, "updated_at": now}
    )
