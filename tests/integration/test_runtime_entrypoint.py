import sys

from ai_trader.runtime import build_production_runner, main
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


_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


def test_production_dependencies_build_without_calling_network(tmp_path, monkeypatch):
    # httpx 默认 trust_env=True，构造 Client 时会解析环境里的代理变量。
    # 若 NO_PROXY 含"已带方括号的 IPv6"（如 "[::1]"），httpx 会生成畸形模式
    # "all://*[::1]" 并抛 httpx.InvalidURL。本用例只验证"依赖能装配、不联网"，
    # 与宿主代理配置无关，故先清空代理环境变量，保证在任何机器上可复现。
    for name in _PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
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
