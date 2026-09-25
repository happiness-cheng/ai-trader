from fastapi.testclient import TestClient

from ai_trader.api.app import ApiDependencies, create_app
from ai_trader.demo import main


class _Unused:
    pass


def test_read_only_eval_summary_endpoint():
    summary = {"scenario_count": 32, "pass_rate": 1.0, "mode": "contract_baseline"}
    dependencies = ApiDependencies(_Unused(), _Unused(), _Unused(), lambda: summary)  # type: ignore[arg-type]
    response = TestClient(create_app(dependencies)).get("/evals/latest")
    assert response.status_code == 200
    assert response.json() == summary


def test_evals_demo_is_offline_contract_baseline(capsys):
    assert main(["--evals"]) == 0
    output = capsys.readouterr().out
    assert "scenarios=32" in output
    assert "mode=deterministic_contract_baseline" in output
