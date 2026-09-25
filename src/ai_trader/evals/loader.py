"""版本化 Eval JSON 数据集加载与完整性校验。"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_trader.evals.contracts import EvalScenario


class DatasetError(ValueError):
    pass


class EvalDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    name: str
    scenarios: tuple[EvalScenario, ...] = Field(min_length=1)


def load_dataset(path: str | Path) -> EvalDataset:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"cannot load dataset: {exc}") from exc
    if raw.get("schema_version") != 1:
        raise DatasetError("unsupported schema version")
    try:
        scenarios = tuple(EvalScenario.model_validate(item) for item in raw["scenarios"])
        dataset = EvalDataset(
            schema_version=1,
            name=raw["dataset"],
            scenarios=scenarios,
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise DatasetError(f"invalid dataset: {exc}") from exc
    ids = [scenario.scenario_id for scenario in dataset.scenarios]
    if len(ids) != len(set(ids)):
        raise DatasetError("duplicate scenario id")
    return dataset
