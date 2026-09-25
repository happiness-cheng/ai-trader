"""SQLite + SQLAlchemy 的 Agent 快照与追加式事件仓库。"""

from datetime import datetime

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, func, insert, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError

from ai_trader.agents.state import AgentRun, EventType, RunEvent, RunState, transition
from ai_trader.observability.redaction import redact_payload


class DuplicateRun(RuntimeError):
    pass


class RunNotFound(LookupError):
    pass


class VersionConflict(RuntimeError):
    pass


_metadata = MetaData()
_runs = Table(
    "agent_runs",
    _metadata,
    Column("run_id", String(128), primary_key=True),
    Column("goal", String(4000), nullable=False),
    Column("state", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)
_events = Table(
    "run_events",
    _metadata,
    Column("event_id", String(128), primary_key=True),
    Column("run_id", String(128), nullable=False, index=True),
    Column("sequence", Integer, nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("state_from", String(32), nullable=True),
    Column("state_to", String(32), nullable=False),
    Column("occurred_at", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _run_from_row(row: RowMapping) -> AgentRun:
    return AgentRun(
        run_id=row["run_id"],
        goal=row["goal"],
        state=RunState(row["state"]),
        version=row["version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _event_from_row(row: RowMapping) -> RunEvent:
    return RunEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        sequence=row["sequence"],
        event_type=EventType(row["event_type"]),
        state_from=RunState(row["state_from"]) if row["state_from"] else None,
        state_to=RunState(row["state_to"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        payload=row["payload"],
    )


class SqlRunRepository:
    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url)
        _metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def create(self, run: AgentRun) -> None:
        created_event = RunEvent(
            event_id=f"created:{run.run_id}",
            run_id=run.run_id,
            sequence=0,
            event_type=EventType.RUN_CREATED,
            state_to=run.state,
            occurred_at=run.created_at,
            payload={"goal": run.goal},
        )
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(_runs).values(
                        run_id=run.run_id,
                        goal=run.goal,
                        state=run.state.value,
                        version=run.version,
                        created_at=_iso(run.created_at),
                        updated_at=_iso(run.updated_at),
                    )
                )
                self._insert_event(connection, created_event)
        except IntegrityError as exc:
            raise DuplicateRun(run.run_id) from exc

    def get(self, run_id: str) -> AgentRun:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_runs).where(_runs.c.run_id == run_id)
            ).mappings().first()
        if row is None:
            raise RunNotFound(run_id)
        return _run_from_row(row)

    def append_transition(
        self,
        run_id: str,
        expected_version: int,
        target: RunState,
        event_type: EventType,
        payload: dict[str, object],
        now: datetime,
        event_id: str,
    ) -> AgentRun:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(_runs).where(_runs.c.run_id == run_id)
            ).mappings().first()
            if row is None:
                raise RunNotFound(run_id)
            current = _run_from_row(row)
            if current.version != expected_version:
                raise VersionConflict(
                    f"expected {expected_version}, found {current.version}"
                )
            updated = transition(current, target, now)
            result = connection.execute(
                update(_runs)
                .where(
                    (_runs.c.run_id == run_id)
                    & (_runs.c.version == expected_version)
                )
                .values(
                    state=updated.state.value,
                    version=updated.version,
                    updated_at=_iso(updated.updated_at),
                )
            )
            if result.rowcount != 1:
                raise VersionConflict(f"concurrent update for {run_id}")
            self._insert_event(
                connection,
                RunEvent(
                    event_id=event_id,
                    run_id=run_id,
                    sequence=self._next_sequence(connection, run_id),
                    event_type=event_type,
                    state_from=current.state,
                    state_to=updated.state,
                    occurred_at=now,
                    payload=payload,
                ),
            )
            return updated

    def append_event(
        self,
        run_id: str,
        expected_version: int,
        event_type: EventType,
        payload: dict[str, object],
        now: datetime,
        event_id: str,
    ) -> AgentRun:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(_runs).where(_runs.c.run_id == run_id)
            ).mappings().first()
            if row is None:
                raise RunNotFound(run_id)
            current = _run_from_row(row)
            if current.version != expected_version:
                raise VersionConflict(
                    f"expected {expected_version}, found {current.version}"
                )
            next_version = current.version + 1
            result = connection.execute(
                update(_runs)
                .where(
                    (_runs.c.run_id == run_id)
                    & (_runs.c.version == expected_version)
                )
                .values(version=next_version, updated_at=_iso(now))
            )
            if result.rowcount != 1:
                raise VersionConflict(f"concurrent update for {run_id}")
            self._insert_event(
                connection,
                RunEvent(
                    event_id=event_id,
                    run_id=run_id,
                    sequence=self._next_sequence(connection, run_id),
                    event_type=event_type,
                    state_from=current.state,
                    state_to=current.state,
                    occurred_at=now,
                    payload=payload,
                ),
            )
            return current.model_copy(
                update={"version": next_version, "updated_at": now}
            )

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(_events)
                .where(_events.c.run_id == run_id)
                .order_by(_events.c.sequence)
            ).mappings().all()
        return tuple(_event_from_row(row) for row in rows)

    def list_recoverable(self) -> tuple[AgentRun, ...]:
        terminal = {
            RunState.SUCCEEDED.value,
            RunState.FAILED.value,
            RunState.CANCELLED.value,
        }
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(_runs)
                .where(_runs.c.state.not_in(terminal))
                .order_by(_runs.c.updated_at)
            ).mappings().all()
        return tuple(_run_from_row(row) for row in rows)

    @staticmethod
    def _insert_event(connection: Connection, event: RunEvent) -> None:
        connection.execute(
            insert(_events).values(
                event_id=event.event_id,
                run_id=event.run_id,
                sequence=event.sequence,
                event_type=event.event_type.value,
                state_from=event.state_from.value if event.state_from else None,
                state_to=event.state_to.value,
                occurred_at=_iso(event.occurred_at),
                payload=redact_payload(event.payload),
            )
        )

    @staticmethod
    def _next_sequence(connection: Connection, run_id: str) -> int:
        value = connection.scalar(
            select(func.max(_events.c.sequence)).where(_events.c.run_id == run_id)
        )
        return 0 if value is None else int(value) + 1
