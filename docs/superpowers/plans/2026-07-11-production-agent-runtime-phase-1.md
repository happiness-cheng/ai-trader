# Production Agent Runtime Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe, offline-testable domain core in which an Agent may create a trade proposal, deterministic rules decide whether it is allowed, and a paper broker executes an approved proposal idempotently.

**Architecture:** Add a focused `src/ai_trader` package beside the legacy modules and migrate behavior incrementally instead of rewriting the running application in place. Pydantic models define the contracts, a pure `RiskGate` owns hard policy, and a `PaperBrokerAdapter` is the only execution backend enabled by default.

**Tech Stack:** Python 3.12, Pydantic 2, pydantic-settings, pytest, pytest-cov, standard-library `Decimal`, `Enum`, and `datetime`.

---

## File map

### Created in this phase

- `pyproject.toml`: package metadata, locked-compatible dependency ranges, pytest and coverage configuration.
- `src/ai_trader/__init__.py`: package boundary and version.
- `src/ai_trader/settings.py`: environment-backed settings and fail-closed trading switches.
- `src/ai_trader/domain/trading.py`: proposal, risk decision, order, position, quote and account contracts.
- `src/ai_trader/risk/gate.py`: deterministic risk policy.
- `src/ai_trader/execution/base.py`: broker protocol and execution errors.
- `src/ai_trader/execution/paper.py`: in-memory paper broker with idempotency.
- `src/ai_trader/demo.py`: offline safety demonstration.
- `tests/unit/test_settings.py`: secret and safety-default tests.
- `tests/unit/test_trading_models.py`: domain validation tests.
- `tests/unit/test_risk_gate.py`: deterministic risk-rule tests.
- `tests/unit/test_paper_broker.py`: broker idempotency and state tests.
- `tests/integration/test_safe_trade_flow.py`: proposal-to-paper-order flow.

### Modified in this phase

- `config.py`: remove the hard-coded webhook and expose environment-backed compatibility values.
- `env.example`: document safe defaults without real credentials.
- `.gitignore`: exclude generated evaluation, coverage, database and model-training artifacts while retaining source fixtures.
- `requirements.txt`: keep legacy installation working and add the new runtime/test dependencies.
- `README.md`: document safe offline demo and correct the production-readiness claims.

## Task 1: Establish a reproducible test entry point

**Files:**
- Create: `pyproject.toml`
- Create: `src/ai_trader/__init__.py`

- [ ] **Step 1: Add the package and test configuration**

Create `src/ai_trader/__init__.py`:

```python
"""AI Trader production Agent runtime."""

__version__ = "0.1.0"
```

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=75,<82"]
build-backend = "setuptools.build_meta"

[project]
name = "ai-trader-runtime"
version = "0.1.0"
requires-python = ">=3.12,<3.14"
dependencies = [
  "pydantic>=2.10,<3",
  "pydantic-settings>=2.7,<3",
]

[project.optional-dependencies]
test = [
  "pytest>=8.3,<9",
  "pytest-cov>=6,<7",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
pythonpath = ["src", "."]

[tool.coverage.run]
source = ["ai_trader"]
branch = true
```

- [ ] **Step 2: Install only the Phase 1 package and test dependencies**

Run: `python -m pip install -e ".[test]"`

Expected: exit code 0 and installation of `ai-trader-runtime`, Pydantic and pytest without installing Torch or GUI automation dependencies.

- [ ] **Step 3: Verify the package is importable**

Run: `python -c "import ai_trader; print(ai_trader.__version__)"`

Expected: `0.1.0`

- [ ] **Step 4: Create an authorized checkpoint commit**

Run only after the user explicitly authorizes Git commits:

```powershell
git add -- pyproject.toml src/ai_trader/__init__.py
git commit -m "build: add production runtime package"
```

Expected: one commit containing only the two listed files. Without authorization, record the checkpoint in the plan and continue without committing.

## Task 2: Add fail-closed settings and remove the leaked webhook

**Files:**
- Create: `src/ai_trader/settings.py`
- Create: `tests/unit/test_settings.py`
- Modify: `config.py`

- [ ] **Step 1: Write failing safety-default and secret tests**

Create `tests/unit/test_settings.py`:

```python
from ai_trader.settings import Settings


def test_trading_is_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("TRADING_ENABLED", raising=False)
    monkeypatch.delenv("PAPER_TRADING_ONLY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.trading_enabled is False
    assert settings.paper_trading_only is True


def test_webhook_comes_from_environment(monkeypatch):
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://example.invalid/test-hook")
    settings = Settings(_env_file=None)
    assert settings.feishu_webhook == "https://example.invalid/test-hook"


def test_live_execution_requires_both_switches(monkeypatch):
    monkeypatch.setenv("TRADING_ENABLED", "true")
    monkeypatch.setenv("PAPER_TRADING_ONLY", "false")
    settings = Settings(_env_file=None)
    assert settings.live_execution_allowed is True
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `python -m pytest tests/unit/test_settings.py -v`

Expected: FAIL because `ai_trader.settings` does not exist.

- [ ] **Step 3: Implement the settings contract**

Create `src/ai_trader/settings.py`:

```python
from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    trading_enabled: bool = False
    paper_trading_only: bool = True
    feishu_webhook: str = ""
    anthropic_base_url: str = "https://token-plan-cn.xiaomimimo.com/anthropic"
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_auth_token: str = ""
    anthropic_model: str = "mimo-v2-pro"

    @cached_property
    def live_execution_allowed(self) -> bool:
        return self.trading_enabled and not self.paper_trading_only


settings = Settings()
```

Modify the webhook compatibility line in `config.py` to:

```python
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
```

- [ ] **Step 4: Run the tests and observe GREEN**

Run: `python -m pytest tests/unit/test_settings.py -v`

Expected: 3 passed.

- [ ] **Step 5: Scan tracked and untracked text for the exposed webhook ID**

Run:

```powershell
rg -n --hidden -g '!.git/**' 'open-apis/bot/v2/hook/[0-9a-f-]{20,}' .
```

Expected: no matches. Separately instruct the user to revoke the previously exposed Feishu webhook because source removal cannot revoke a credential.

## Task 3: Define validated trading-domain contracts

**Files:**
- Create: `src/ai_trader/domain/__init__.py`
- Create: `src/ai_trader/domain/trading.py`
- Create: `tests/unit/test_trading_models.py`

- [ ] **Step 1: Write failing contract tests**

Create `tests/unit/test_trading_models.py` with tests that construct a valid proposal and reject invalid quantity, invalid price and expired proposal:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_trader.domain.trading import OrderSide, TradeProposal


def make_proposal(**overrides):
    now = datetime.now(UTC)
    values = {
        "proposal_id": "proposal-001",
        "symbol": "600519",
        "side": OrderSide.BUY,
        "quantity": 100,
        "limit_price": Decimal("1450.00"),
        "stop_loss": Decimal("1377.50"),
        "take_profit": Decimal("1667.50"),
        "evidence_refs": ["tool-call-001"],
        "created_at": now,
        "expires_at": now + timedelta(minutes=2),
    }
    values.update(overrides)
    return TradeProposal(**values)


def test_valid_trade_proposal():
    proposal = make_proposal()
    assert proposal.quantity == 100
    assert proposal.symbol == "600519"


@pytest.mark.parametrize(
    ("field", "value"),
    [("quantity", 0), ("quantity", 50), ("limit_price", Decimal("0"))],
)
def test_invalid_trade_proposal(field, value):
    with pytest.raises(ValidationError):
        make_proposal(**{field: value})


def test_expired_trade_proposal_is_observable():
    proposal = make_proposal(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert proposal.is_expired(datetime.now(UTC)) is True
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `python -m pytest tests/unit/test_trading_models.py -v`

Expected: FAIL because `ai_trader.domain.trading` does not exist.

- [ ] **Step 3: Implement the contracts**

Create immutable Pydantic models in `src/ai_trader/domain/trading.py` for:

```python
class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class RiskOutcome(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUIRE_HUMAN = "require_human"


class OrderStatus(str, Enum):
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
```

Implement `Quote`, `Position`, `AccountSnapshot`, `TradeProposal`, `RiskDecision` and `Order` with `ConfigDict(frozen=True)`. Require six-digit numeric symbols, positive prices, buy quantity in lots of 100, timezone-aware timestamps, non-empty evidence references and an `is_expired(now)` method. `TradeProposal` must not contain broker credentials or an execution method.

- [ ] **Step 4: Run the tests and observe GREEN**

Run: `python -m pytest tests/unit/test_trading_models.py -v`

Expected: 5 passed.

## Task 4: Implement the deterministic RiskGate

**Files:**
- Create: `src/ai_trader/risk/__init__.py`
- Create: `src/ai_trader/risk/gate.py`
- Create: `tests/unit/test_risk_gate.py`

- [ ] **Step 1: Write failing policy tests**

Create table-driven tests covering these exact observable outcomes:

```python
def test_kill_switch_rejects():
    assert decision.outcome is RiskOutcome.REJECT
    assert "TRADING_DISABLED" in decision.codes


def test_expired_quote_rejects():
    assert "STALE_QUOTE" in decision.codes


def test_position_limit_rejects():
    assert "POSITION_LIMIT_EXCEEDED" in decision.codes


def test_duplicate_open_order_rejects():
    assert "DUPLICATE_ORDER" in decision.codes


def test_safe_paper_trade_requires_human():
    assert decision.outcome is RiskOutcome.REQUIRE_HUMAN
    assert decision.codes == ("HUMAN_APPROVAL_REQUIRED",)
```

Use fixed UTC datetimes, `Decimal` values, and the public `RiskGate.evaluate(context)` API. Define fixtures locally in the test so no live market data is used.

- [ ] **Step 2: Run the tests and observe RED**

Run: `python -m pytest tests/unit/test_risk_gate.py -v`

Expected: FAIL because `ai_trader.risk.gate` does not exist.

- [ ] **Step 3: Implement the minimal policy**

Implement:

```python
@dataclass(frozen=True)
class RiskPolicy:
    max_quote_age_seconds: int = 30
    max_position_ratio: Decimal = Decimal("0.15")
    max_total_positions: int = 8
    max_daily_loss_ratio: Decimal = Decimal("0.03")
    require_human_approval: bool = True


@dataclass(frozen=True)
class RiskContext:
    proposal: TradeProposal
    quote: Quote
    account: AccountSnapshot
    positions: tuple[Position, ...]
    open_order_symbols: frozenset[str]
    trading_enabled: bool
    now: datetime
```

`RiskGate.evaluate` must accumulate all hard-rule reason codes, return `REJECT` when any exists, otherwise return `REQUIRE_HUMAN` under the default policy. It must not call an LLM, network API, clock singleton or mutable global configuration.

- [ ] **Step 4: Run the tests and observe GREEN**

Run: `python -m pytest tests/unit/test_risk_gate.py -v`

Expected: 5 passed.

## Task 5: Add an idempotent Paper Broker

**Files:**
- Create: `src/ai_trader/execution/base.py`
- Create: `src/ai_trader/execution/paper.py`
- Create: `tests/unit/test_paper_broker.py`

- [ ] **Step 1: Write failing broker tests**

Create tests for the public broker interface:

```python
def test_approved_proposal_creates_filled_order():
    order = broker.submit(proposal, approved_decision)
    assert order.status is OrderStatus.FILLED
    assert order.proposal_id == proposal.proposal_id


def test_same_proposal_is_idempotent():
    first = broker.submit(proposal, approved_decision)
    second = broker.submit(proposal, approved_decision)
    assert second.order_id == first.order_id
    assert len(broker.list_orders()) == 1


def test_unapproved_proposal_cannot_execute():
    with pytest.raises(ExecutionDenied, match="approval"):
        broker.submit(proposal, rejected_decision)
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `python -m pytest tests/unit/test_paper_broker.py -v`

Expected: FAIL because the execution package does not exist.

- [ ] **Step 3: Implement the broker protocol and paper adapter**

`src/ai_trader/execution/base.py` must define `ExecutionDenied` and a `BrokerAdapter` protocol with `submit`, `get_order`, and `list_orders`.

`PaperBrokerAdapter` must keep orders keyed by `proposal_id`, return the existing order on duplicate submission, reject any decision other than `APPROVE`, and generate deterministic testable order IDs using an injected `id_factory`. The default factory may use `uuid4`.

- [ ] **Step 4: Run the tests and observe GREEN**

Run: `python -m pytest tests/unit/test_paper_broker.py -v`

Expected: 3 passed.

## Task 6: Prove the safe offline flow end to end

**Files:**
- Create: `tests/integration/test_safe_trade_flow.py`
- Create: `src/ai_trader/demo.py`

- [ ] **Step 1: Write the failing integration test**

The test must build a proposal, quote and account fixture; evaluate once with `require_human_approval=True`; create an explicit approved `RiskDecision` representing a human action; and submit twice to the paper broker:

```python
def test_proposal_requires_approval_then_executes_once():
    pending = gate.evaluate(context)
    assert pending.outcome is RiskOutcome.REQUIRE_HUMAN

    approved = RiskDecision(
        outcome=RiskOutcome.APPROVE,
        codes=("HUMAN_APPROVED",),
        reasons=("offline demo approval",),
    )
    first = broker.submit(proposal, approved)
    second = broker.submit(proposal, approved)
    assert first.order_id == second.order_id
    assert len(broker.list_orders()) == 1
```

- [ ] **Step 2: Run the integration test and observe RED**

Run: `python -m pytest tests/integration/test_safe_trade_flow.py -v`

Expected: FAIL because `ai_trader.demo` or its fixture builder does not exist.

- [ ] **Step 3: Add an offline demo entry point**

`python -m ai_trader.demo` must use fixed fixtures, run only `PaperBrokerAdapter`, print the proposal ID, initial `require_human` outcome, approved paper order ID and final `filled` state, and exit 0. It must not import `ths_trader`, `market_data`, `requests` or any model client.

- [ ] **Step 4: Run integration and user-path verification**

Run:

```powershell
python -m pytest tests/integration/test_safe_trade_flow.py -v
python -m ai_trader.demo
```

Expected: test passes; demo output contains `require_human`, `paper-`, and `filled`.

## Task 7: Align environment files and documentation

**Files:**
- Modify: `env.example`
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Document safe environment defaults**

Add to `env.example`:

```dotenv
TRADING_ENABLED=false
PAPER_TRADING_ONLY=true
ANTHROPIC_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic
ANTHROPIC_MODEL=mimo-v2-pro
```

Keep credentials as non-working placeholders.

- [ ] **Step 2: Exclude generated artifacts without deleting user files**

Add patterns for `.coverage`, `htmlcov/`, `.pytest_cache/`, `*.db`, `evals/reports/`, `qwen-stock-lora-*/checkpoint-*/`, `colab_upload*.zip`, and generated backtest JSON/PNG. Do not remove files or change existing tracked-file history.

- [ ] **Step 3: Correct README claims and add the demo**

Describe the project as a constrained Agent prototype under production hardening. Replace “decision quality does not decrease”, “accuracy continuously improves”, “microservices”, and “automated end-to-end order test” claims with measured or qualified language. Add:

```powershell
python -m pip install -e ".[test]"
python -m pytest
python -m ai_trader.demo
```

State explicitly that live trading is disabled by default and the Phase 1 demo uses an in-memory paper broker.

- [ ] **Step 4: Verify documentation commands**

Run every command in the new README quick-start section from the repository root.

Expected: installation exits 0, tests exit 0, demo exits 0.

## Task 8: Phase 1 full verification

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Preserve the legacy installer**

Add compatible entries for `pydantic>=2.10,<3`, `pydantic-settings>=2.7,<3`, `pytest>=8.3,<9`, and `pytest-cov>=6,<7`. Do not pin or alter the existing Torch/model stack during this phase.

- [ ] **Step 2: Run build and import verification**

Run:

```powershell
python -m compileall -q src tests config.py
python -c "from ai_trader.settings import Settings; from ai_trader.risk.gate import RiskGate; from ai_trader.execution.paper import PaperBrokerAdapter; print('imports: PASS')"
```

Expected: both commands exit 0 and print `imports: PASS`.

- [ ] **Step 3: Run the complete Phase 1 test suite with coverage**

Run: `python -m pytest --cov=ai_trader --cov-report=term-missing`

Expected: 0 failures and at least 85% branch coverage for the new `src/ai_trader` package.

- [ ] **Step 4: Run security and diff checks**

Run:

```powershell
rg -n --hidden -g '!.git/**' '(open-apis/bot/v2/hook/[0-9a-f-]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})' .
git diff --check
git status --short
```

Expected: no secret matches, no whitespace errors, and only planned files plus pre-existing user changes appear in status.

- [ ] **Step 5: Perform a scoped review**

Review only Phase 1 files for boundary validation, hard-coded credentials, accidental live execution, mutable financial values, timezone-naive timestamps and idempotency gaps. Any issue found must receive a regression test before its fix.

- [ ] **Step 6: Create an authorized phase checkpoint**

Only when the user explicitly requests a commit, stage the exact Phase 1 files after rechecking `git diff --cached` and commit:

```powershell
git commit -m "feat: add safe production agent trading core"
```

Without explicit authorization, leave all changes unstaged and report the verified working-tree state.

## Follow-on plans

After Phase 1 passes, create separate implementation plans for these independently testable subsystems:

1. Phase 2: structured Tool Runtime and provider-neutral Model Gateway.
2. Phase 3: SQLite event store, persistent Agent state machine, cancellation and recovery.
3. Phase 4: approval API, idempotent execution workflow and disabled-by-default THS adapter.
4. Phase 5: Agent Eval runner, 30+ fixtures, metrics, Trace UI and final interview demo.
