from datetime import UTC, datetime, timedelta

import pytest

from ai_trader.agents.state import AgentRun, EventType, RunState
from ai_trader.persistence.runs import DuplicateRun, SqlRunRepository, VersionConflict

NOW = datetime(2026, 7, 11, 3, 0, tzinfo=UTC)


def make_run():
    return AgentRun(
        run_id="run-001",
        goal="analyze fixture market",
        state=RunState.CREATED,
        version=0,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def repository(tmp_path):
    value = SqlRunRepository(f"sqlite:///{tmp_path / 'runs.db'}")
    yield value
    value.close()


def test_create_and_load_run_with_created_event(repository):
    repository.create(make_run())
    loaded = repository.get("run-001")
    events = repository.events("run-001")
    assert loaded == make_run()
    assert [event.event_type for event in events] == [EventType.RUN_CREATED]


def test_transition_updates_snapshot_and_appends_event_atomically(repository):
    repository.create(make_run())
    updated = repository.append_transition(
        run_id="run-001",
        expected_version=0,
        target=RunState.PLANNING,
        event_type=EventType.STATE_CHANGED,
        payload={"source": "test"},
        now=NOW + timedelta(seconds=1),
        event_id="event-001",
    )
    assert updated.version == 1
    assert repository.get("run-001") == updated
    assert [event.sequence for event in repository.events("run-001")] == [0, 1]


def test_duplicate_run_is_rejected(repository):
    repository.create(make_run())
    with pytest.raises(DuplicateRun):
        repository.create(make_run())


def test_stale_version_writes_no_event(repository):
    repository.create(make_run())
    repository.append_transition(
        "run-001", 0, RunState.PLANNING, EventType.STATE_CHANGED, {},
        NOW + timedelta(seconds=1), "event-001",
    )
    with pytest.raises(VersionConflict):
        repository.append_transition(
            "run-001", 0, RunState.CANCELLED, EventType.CANCEL_REQUESTED, {},
            NOW + timedelta(seconds=2), "event-stale",
        )
    assert len(repository.events("run-001")) == 2


def test_second_repository_instance_recovers_state(tmp_path):
    url = f"sqlite:///{tmp_path / 'runs.db'}"
    first = SqlRunRepository(url)
    first.create(make_run())
    first.append_transition(
        "run-001", 0, RunState.PLANNING, EventType.STATE_CHANGED, {},
        NOW + timedelta(seconds=1), "event-001",
    )
    second = SqlRunRepository(url)
    assert second.get("run-001").state is RunState.PLANNING
    assert len(second.events("run-001")) == 2
    first.close()
    second.close()


def test_event_payload_is_redacted_at_repository_boundary(repository):
    repository.create(make_run())
    repository.append_transition(
        "run-001", 0, RunState.PLANNING, EventType.STATE_CHANGED,
        {"api_key": "must-not-persist", "safe": "visible"},
        NOW + timedelta(seconds=1), "event-001",
    )
    payload = repository.events("run-001")[-1].payload
    assert payload == {"api_key": "[REDACTED]", "safe": "visible"}
