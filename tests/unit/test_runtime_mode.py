import pytest

from ai_trader.runtime import select_runtime_mode


@pytest.mark.parametrize("mode", ["pipeline", "agent", "production"])
def test_supported_runtime_modes(mode):
    assert select_runtime_mode(mode) == mode


def test_unknown_runtime_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        select_runtime_mode("typo")
