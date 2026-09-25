"""Agent Eval 的版本化输入、观测和分数契约。"""

from pydantic import BaseModel, ConfigDict, Field


class EvalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ObservedToolCall(EvalModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object]


class EvalScenario(EvalModel):
    schema_version: int = 1
    scenario_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=4000)
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_arguments: dict[str, dict[str, object]] = Field(default_factory=dict)
    expected_outcome: str = Field(min_length=1, max_length=128)
    required_evidence: tuple[str, ...] = ()
    max_steps: int = Field(default=8, ge=1, le=100)
    expects_recovery: bool = False


class EvalObservation(EvalModel):
    scenario_id: str
    tool_calls: tuple[ObservedToolCall, ...] = ()
    final_outcome: str
    evidence_refs: tuple[str, ...] = ()
    recovered: bool = False
    steps: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)


class ScenarioScore(EvalModel):
    scenario_id: str
    passed: bool
    tool_selection: float = Field(ge=0, le=1)
    argument_accuracy: float = Field(ge=0, le=1)
    policy_compliance: float = Field(ge=0, le=1)
    outcome_accuracy: float = Field(ge=0, le=1)
    recovery: float = Field(ge=0, le=1)
    step_efficiency: float = Field(ge=0, le=1)
    groundedness: float = Field(ge=0, le=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    latency_ms: float = Field(ge=0)


class EvalReport(EvalModel):
    dataset: str
    scenario_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    metrics: dict[str, float]
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_estimated_cost: float = Field(ge=0)
    scores: tuple[ScenarioScore, ...]
