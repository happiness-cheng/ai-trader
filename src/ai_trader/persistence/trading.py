"""交易提案、审批和订单的事务持久化。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import IntegrityError

from ai_trader.domain.trading import Order, TradeProposal
from ai_trader.observability.redaction import redact_payload


class DuplicateReservation(RuntimeError):
    pass


class TradingRecordNotFound(LookupError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(min_length=1, max_length=128)
    proposal_id: str = Field(min_length=1, max_length=128)
    reviewer_id: str = Field(min_length=1, max_length=128)
    approved: bool
    created_at: datetime
    expires_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)

    _created_aware = field_validator("created_at")(_aware)
    _expires_aware = field_validator("expires_at")(_aware)


_metadata = MetaData()
_proposals = Table(
    "trade_proposals", _metadata,
    Column("proposal_id", String(128), primary_key=True),
    Column("payload", JSON, nullable=False),
)
_approvals = Table(
    "approvals", _metadata,
    Column("approval_id", String(128), primary_key=True),
    Column("proposal_id", String(128), nullable=False, index=True),
    Column("reviewer_id", String(128), nullable=False),
    Column("approved", String(8), nullable=False),
    Column("created_at", String(64), nullable=False),
    Column("expires_at", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
)
_reservations = Table(
    "order_reservations", _metadata,
    Column("proposal_id", String(128), primary_key=True),
    Column("reservation_id", String(128), unique=True, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", String(64), nullable=False),
)
_orders = Table(
    "orders", _metadata,
    Column("order_id", String(128), primary_key=True),
    Column("proposal_id", String(128), unique=True, nullable=False),
    Column("payload", JSON, nullable=False),
)


class SqlTradingRepository:
    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url)
        _metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def save_proposal(self, proposal: TradeProposal) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(_proposals).values(
                        proposal_id=proposal.proposal_id,
                        payload=proposal.model_dump(mode="json"),
                    )
                )
        except IntegrityError as exc:
            existing = self.get_proposal(proposal.proposal_id)
            if existing != proposal:
                raise DuplicateReservation("proposal id already has different data") from exc

    def get_proposal(self, proposal_id: str) -> TradeProposal:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_proposals).where(_proposals.c.proposal_id == proposal_id)
            ).mappings().first()
        if row is None:
            raise TradingRecordNotFound(proposal_id)
        return TradeProposal.model_validate(row["payload"])

    def record_approval(self, approval: ApprovalRecord) -> None:
        self.get_proposal(approval.proposal_id)
        safe = approval.model_copy(update={"metadata": redact_payload(approval.metadata)})
        with self._engine.begin() as connection:
            connection.execute(
                insert(_approvals).values(
                    approval_id=safe.approval_id,
                    proposal_id=safe.proposal_id,
                    reviewer_id=safe.reviewer_id,
                    approved="true" if safe.approved else "false",
                    created_at=safe.created_at.isoformat(),
                    expires_at=safe.expires_at.isoformat(),
                    metadata=safe.metadata,
                )
            )

    def get_valid_approval(
        self, proposal_id: str, now: datetime
    ) -> ApprovalRecord | None:
        _aware(now)
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(_approvals)
                .where(_approvals.c.proposal_id == proposal_id)
                .order_by(_approvals.c.created_at.desc())
            ).mappings().all()
        for row in rows:
            approval = self._approval_from_row(row)
            if approval.approved and now < approval.expires_at:
                return approval
            if not approval.approved:
                return None
        return None

    def reserve_order(self, proposal_id: str, reservation_id: str, now: datetime) -> None:
        self.get_proposal(proposal_id)
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(_reservations).values(
                        proposal_id=proposal_id,
                        reservation_id=reservation_id,
                        status="reserved",
                        created_at=now.isoformat(),
                    )
                )
        except IntegrityError as exc:
            raise DuplicateReservation(proposal_id) from exc

    def save_order(self, order: Order) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(_orders).values(
                    order_id=order.order_id,
                    proposal_id=order.proposal_id,
                    payload=order.model_dump(mode="json"),
                )
            )
            connection.execute(
                update(_reservations)
                .where(_reservations.c.proposal_id == order.proposal_id)
                .values(status="completed")
            )

    def mark_execution_unknown(self, proposal_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(_reservations)
                .where(_reservations.c.proposal_id == proposal_id)
                .values(status="execution_unknown")
            )

    def get_order_by_proposal(self, proposal_id: str) -> Order | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_orders).where(_orders.c.proposal_id == proposal_id)
            ).mappings().first()
        return None if row is None else Order.model_validate(row["payload"])

    @staticmethod
    def _approval_from_row(row: RowMapping) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row["approval_id"], proposal_id=row["proposal_id"],
            reviewer_id=row["reviewer_id"], approved=row["approved"] == "true",
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            metadata=row["metadata"],
        )
