from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ai_trader.agents.orchestrator import PersistentOrchestrator
from ai_trader.api.app import ApiDependencies, create_app
from ai_trader.execution.coordinator import ExecutionCoordinator
from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.persistence.runs import SqlRunRepository
from ai_trader.persistence.trading import SqlTradingRepository

NOW = datetime(2026, 7, 11, 4, 0, tzinfo=UTC)


@pytest.fixture
def api(tmp_path):
    url = f"sqlite:///{tmp_path / 'api.db'}"
    run_repo = SqlRunRepository(url)
    trading_repo = SqlTradingRepository(url)
    orchestrator = PersistentOrchestrator(run_repo, clock=lambda: NOW)
    coordinator = ExecutionCoordinator(
        trading_repo, PaperBrokerAdapter(clock=lambda: NOW), clock=lambda: NOW
    )
    client = TestClient(create_app(ApiDependencies(orchestrator, run_repo, coordinator)))
    yield client
    run_repo.close()
    trading_repo.close()


def test_health_and_run_lifecycle(api):
    assert api.get("/health").json() == {"status": "ok", "live_execution": False}
    created = api.post("/runs", json={"goal": "analyze fixture"})
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    assert api.get(f"/runs/{run_id}").json()["state"] == "created"
    cancelled = api.post(f"/runs/{run_id}/cancel", json={"reason": "user request"})
    assert cancelled.json()["state"] == "cancelled"
    assert len(api.get(f"/runs/{run_id}/events").json()) == 2


def test_submit_and_approve_proposal_requires_reviewer_header(api):
    proposal = {
        "proposal_id": "proposal-001", "symbol": "600519", "side": "buy",
        "quantity": 100, "limit_price": "100", "stop_loss": "95",
        "take_profit": "115", "evidence_refs": ["tool-1"],
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
    }
    body = {
        "proposal": proposal,
        "quote": {"symbol": "600519", "price": "100", "as_of": NOW.isoformat()},
        "account": {"total_equity": "100000", "available_cash": "50000", "daily_pnl": "0"},
        "trading_enabled": True,
        "now": NOW.isoformat(),
    }
    submitted = api.post("/proposals", json=body)
    assert submitted.status_code == 201
    assert submitted.json()["outcome"] == "require_human"
    assert api.post("/proposals/proposal-001/approve").status_code == 401
    approved = api.post(
        "/proposals/proposal-001/approve", headers={"X-Reviewer-Id": "reviewer-1"}
    )
    assert approved.status_code == 200
    assert approved.json()["proposal_id"] == "proposal-001"
