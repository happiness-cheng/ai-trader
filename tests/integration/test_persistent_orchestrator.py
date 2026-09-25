from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.state import EventType, InvalidTransition, RunState
from ai_trader.persistence.runs import SqlRunRepository


NOW = datetime(2026, 7, 11, 3, 0, tzinfo=UTC)


def make_orchestrator(tmp_path):
    ticks = count()
    ids = count(1)
    repository = SqlRunRepository(f"sqlite:///{tmp_path / 'runs.db'}")
    orchestrator = PersistentOrchestrator(
        repository,
        clock=lambda: NOW + timedelta(seconds=next(ticks)),
        id_factory=lambda prefix: f"{prefix}-{next(ids):03d}",
    )
    return orchestrator, repository


def test_create_start_and_pause_for_approval(tmp_path):
    orchestrator, repository = make_orchestrator(tmp_path)
    run = orchestrator.create_run("analyze fixture", run_id="run-001")
    planning = orchestrator.start(run.run_id)
    waiting = orchestrator.request_approval(planning.run_id, {"proposal_id": "p-1"})
    assert waiting.state is RunState.WAITING_APPROVAL
    assert [event.event_type for event in repository.events(run.run_id)] == [
        EventType.RUN_CREATED,
        EventType.STATE_CHANGED,
        EventType.APPROVAL_REQUESTED,
    ]
    repository.close()


def test_resume_persists_approval_and_execution_intent_first(tmp_path):
    orchestrator, repository = make_orchestrator(tmp_path)
    run = orchestrator.create_run("analyze fixture", run_id="run-001")
    orchestrator.start(run.run_id)
    orchestrator.request_approval(run.run_id, {"proposal_id": "p-1"})
    executing = orchestrator.resume_approved(run.run_id, "p-1", "reviewer-1")
    event_types = [event.event_type for event in repository.events(run.run_id)]
    assert executing.state is RunState.EXECUTING
    assert event_types[-3:] == [
        EventType.APPROVAL_RECEIVED,
        EventType.INTENT_RECORDED,
        EventType.STATE_CHANGED,
    ]
    repository.close()


def test_cancel_and_terminal_rejection(tmp_path):
    orchestrator, repository = make_orchestrator(tmp_path)
    run = orchestrator.create_run("analyze fixture", run_id="run-001")
    cancelled = orchestrator.cancel(run.run_id, "user request")
    assert cancelled.state is RunState.CANCELLED
    with pytest.raises(InvalidTransition):
        orchestrator.cancel(run.run_id, "again")
    repository.close()


def test_second_repository_instance_finds_recoverable_run(tmp_path):
    orchestrator, repository = make_orchestrator(tmp_path)
    run = orchestrator.create_run("analyze fixture", run_id="run-001")
    orchestrator.start(run.run_id)
    repository.close()

    second_repository = SqlRunRepository(f"sqlite:///{tmp_path / 'runs.db'}")
    recovered = PersistentOrchestrator(second_repository).recoverable_runs()
    assert [(item.run_id, item.state) for item in recovered] == [
        ("run-001", RunState.PLANNING)
    ]
    second_repository.close()
