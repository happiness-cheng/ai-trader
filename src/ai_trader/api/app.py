"""不直接访问交易 GUI 的 FastAPI 控制面。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.agents.state import InvalidTransition
from ai_trader.domain.trading import AccountSnapshot, Position, Quote, TradeProposal
from ai_trader.execution.coordinator import ExecutionCoordinator, ExecutionUnknown
from ai_trader.persistence.runs import RunNotFound, SqlRunRepository, VersionConflict
from ai_trader.persistence.trading import TradingRecordNotFound
from ai_trader.risk.gate import RiskContext


@dataclass(frozen=True)
class ApiDependencies:
    orchestrator: PersistentOrchestrator
    run_repository: SqlRunRepository
    execution_coordinator: ExecutionCoordinator
    eval_summary: Callable[[], dict[str, object]] | None = None


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRunRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=4000)


class CancelRunRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


class RejectRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


class ProposalSubmission(ApiModel):
    proposal: TradeProposal
    quote: Quote
    account: AccountSnapshot
    positions: tuple[Position, ...] = ()
    open_order_symbols: frozenset[str] = frozenset()
    trading_enabled: bool
    now: datetime

    def to_context(self) -> RiskContext:
        return RiskContext(
            proposal=self.proposal,
            quote=self.quote,
            account=self.account,
            positions=self.positions,
            open_order_symbols=self.open_order_symbols,
            trading_enabled=self.trading_enabled,
            now=self.now,
        )


def _reviewer(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="reviewer required")
    return value.strip()


def create_app(dependencies: ApiDependencies) -> FastAPI:
    app = FastAPI(title="AI Trader Agent Runtime", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "live_execution": False}

    @app.get("/evals/latest")
    def latest_evals() -> dict[str, object]:
        if dependencies.eval_summary is None:
            raise HTTPException(status_code=404, detail="eval report not configured")
        return dependencies.eval_summary()

    @app.post("/runs", status_code=status.HTTP_201_CREATED)
    def create_run(request: CreateRunRequest) -> dict[str, object]:
        return dependencies.orchestrator.create_run(request.goal).model_dump(mode="json")

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            return dependencies.run_repository.get(run_id).model_dump(mode="json")
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.get("/runs/{run_id}/events")
    def get_events(run_id: str) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in dependencies.run_repository.events(run_id)
        ]

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: CancelRunRequest) -> dict[str, object]:
        try:
            return dependencies.orchestrator.cancel(run_id, request.reason).model_dump(
                mode="json"
            )
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except (InvalidTransition, VersionConflict) as exc:
            raise HTTPException(status_code=409, detail="run state conflict") from exc

    @app.post("/proposals", status_code=status.HTTP_201_CREATED)
    def submit_proposal(request: ProposalSubmission) -> dict[str, object]:
        decision = dependencies.execution_coordinator.submit_proposal(
            request.to_context()
        )
        return decision.model_dump(mode="json")

    @app.post("/proposals/{proposal_id}/approve")
    def approve_proposal(
        proposal_id: str,
        x_reviewer_id: str | None = Header(default=None, alias="X-Reviewer-Id"),
    ) -> dict[str, object]:
        try:
            approval = dependencies.execution_coordinator.approve(
                proposal_id, _reviewer(x_reviewer_id)
            )
            return approval.model_dump(mode="json")
        except TradingRecordNotFound as exc:
            raise HTTPException(status_code=404, detail="proposal not found") from exc
        except ExecutionUnknown as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/proposals/{proposal_id}/reject")
    def reject_proposal(
        proposal_id: str,
        request: RejectRequest,
        x_reviewer_id: str | None = Header(default=None, alias="X-Reviewer-Id"),
    ) -> dict[str, object]:
        try:
            approval = dependencies.execution_coordinator.reject(
                proposal_id, _reviewer(x_reviewer_id), request.reason
            )
            return approval.model_dump(mode="json")
        except TradingRecordNotFound as exc:
            raise HTTPException(status_code=404, detail="proposal not found") from exc

    return app
