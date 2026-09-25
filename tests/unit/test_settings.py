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
