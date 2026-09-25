from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ai_trader.agents.state import (
    AgentRun,
    InvalidTransition,
    RunState,
    transition,
)


NOW = datetime(2026, 7, 11, 3, 0, tzinfo=UTC)


def make_run(state=RunState.CREATED, version=0):
    return AgentRun(
        run_id="run-001",
        goal="analyze fixture market",
        state=state,
        version=version,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunState.CREATED, RunState.PLANNING),
        (RunState.PLANNING, RunState.TOOL_RUNNING),
        (RunState.TOOL_RUNNING, RunState.WAITING_APPROVAL),
        (RunState.WAITING_APPROVAL, RunState.EXECUTING),
        (RunState.EXECUTING, RunState.VERIFYING),
        (RunState.VERIFYING, RunState.SUCCEEDED),
    ],
)
def test_legal_transition_increments_version(source, target):
    result = transition(make_run(source, version=4), target, NOW + timedelta(seconds=1))
    assert result.state is target
    assert result.version == 5


@pytest.mark.parametrize("terminal", [RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED])
def test_terminal_state_cannot_transition(terminal):
    with pytest.raises(InvalidTransition):
        transition(make_run(terminal), RunState.PLANNING, NOW + timedelta(seconds=1))


def test_skipped_transition_is_rejected():
    with pytest.raises(InvalidTransition):
        transition(make_run(), RunState.EXECUTING, NOW + timedelta(seconds=1))


def test_transition_rejects_time_moving_backwards():
    with pytest.raises(ValueError, match="earlier"):
        transition(make_run(), RunState.PLANNING, NOW - timedelta(seconds=1))


def test_run_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        AgentRun.model_validate(make_run().model_dump() | {"updated_at": datetime.now()})
