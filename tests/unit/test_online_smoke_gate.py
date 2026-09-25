import pytest

from ai_trader.runtime import OnlineSmokeDenied, main, validate_online_smoke
from ai_trader.settings import Settings


def settings(**overrides):
    values = {
        "runtime_mode": "production",
        "anthropic_api_key": "fixture-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_online_smoke_requires_explicit_network_flag():
    with pytest.raises(OnlineSmokeDenied, match="allow-network"):
        validate_online_smoke(settings(), allow_network=False)


def test_online_smoke_requires_production_mode():
    with pytest.raises(OnlineSmokeDenied, match="production"):
        validate_online_smoke(settings(runtime_mode="legacy"), allow_network=True)


def test_online_smoke_requires_credential():
    with pytest.raises(OnlineSmokeDenied, match="credential"):
        validate_online_smoke(settings(anthropic_api_key=""), allow_network=True)


def test_cli_refusal_is_concise(capsys):
    assert main(["--smoke-online"]) == 2
    output = capsys.readouterr().out
    assert "online_smoke=DENIED" in output
    assert "allow-network" in output
