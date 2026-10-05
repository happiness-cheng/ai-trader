"""交易提案、风控决策与委托的不可变领域模型。"""

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value


class Quote(DomainModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    price: Decimal = Field(gt=0)
    as_of: datetime

    _validate_as_of = field_validator("as_of")(_require_aware)


class Position(DomainModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    quantity: int = Field(gt=0)
    average_price: Decimal = Field(gt=0)
    current_price: Decimal = Field(gt=0)
    sector: str = "其他"

    @property
    def market_value(self) -> Decimal:
        return self.current_price * self.quantity


class AccountSnapshot(DomainModel):
    total_equity: Decimal = Field(gt=0)
    available_cash: Decimal = Field(ge=0)
    daily_pnl: Decimal = Decimal(0)


class TradeProposal(DomainModel):
    proposal_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(pattern=r"^\d{6}$")
    side: OrderSide
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("证据引用不能为空")
        return value

    @model_validator(mode="after")
    def validate_trade_rules(self) -> "TradeProposal":
        if self.side is OrderSide.BUY and self.quantity % 100:
            raise ValueError("A 股买入数量必须为 100 的整数倍")
        if self.expires_at <= self.created_at:
            raise ValueError("提案过期时间必须晚于创建时间")
        if self.side is OrderSide.BUY and not (
            self.stop_loss < self.limit_price < self.take_profit
        ):
            raise ValueError("买入提案必须满足止损价 < 限价 < 止盈价")
        return self

    def is_expired(self, now: datetime) -> bool:
        _require_aware(now)
        return now >= self.expires_at


class RiskDecision(DomainModel):
    proposal_id: str = Field(min_length=1, max_length=128)
    outcome: RiskOutcome
    codes: tuple[str, ...] = Field(min_length=1)
    reasons: tuple[str, ...] = Field(min_length=1)


class Order(DomainModel):
    order_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    side: OrderSide
    quantity: int = Field(gt=0)
    price: Decimal = Field(gt=0)
    status: OrderStatus
    created_at: datetime

    _validate_created_at = field_validator("created_at")(_require_aware)
