import json

import pytest

from ai_trader.evals.loader import DatasetError, load_dataset


def test_core_dataset_has_30_unique_cross_category_scenarios():
    dataset = load_dataset("evals/scenarios/core.json")
    assert dataset.name == "core-safety-v1"
    assert len(dataset.scenarios) >= 30
    assert len({item.scenario_id for item in dataset.scenarios}) == len(dataset.scenarios)
    assert {"risk", "recovery", "prompt_injection", "approval", "security"} <= {
        item.category for item in dataset.scenarios
    }


def test_loader_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "bad.json"
    scenario = {
        "scenario_id": "same", "category": "x", "prompt": "x",
        "expected_outcome": "x"
    }
    path.write_text(json.dumps({"schema_version": 1, "dataset": "bad", "scenarios": [scenario, scenario]}))
    with pytest.raises(DatasetError, match="duplicate"):
        load_dataset(path)
