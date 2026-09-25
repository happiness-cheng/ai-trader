import sys

from ai_trader.runtime import main
from ai_trader.runtime import build_production_runner
from ai_trader.settings import Settings


def test_dry_run_uses_new_runtime_without_network_or_legacy_trading(capsys):
    sys.modules.pop("ths_trader", None)
    assert main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "fixture answer: price 100" in output
    assert "network=false" in output
    assert "live_execution=false" in output
    assert "ths_trader" not in sys.modules


def test_default_entrypoint_preserves_legacy_mode(monkeypatch, capsys):
    monkeypatch.delenv("AI_TRADER_RUNTIME_MODE", raising=False)
    assert main([]) == 0
    assert "runtime_mode=legacy" in capsys.readouterr().out


def test_production_dependencies_build_without_calling_network(tmp_path):
    settings = Settings(
        _env_file=None,
        runtime_mode="production",
        runtime_database_url=f"sqlite:///{tmp_path / 'runtime.db'}",
        anthropic_api_key="fixture-key",
        anthropic_base_url="https://example.invalid",
        anthropic_model="fixture-model",
    )
    _runner, repository = build_production_runner(settings)
    repository.close()
