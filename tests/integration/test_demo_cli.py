import pytest

from ai_trader.demo import main


@pytest.mark.parametrize(
    ("arguments", "marker"),
    [
        ([], "risk_outcome=require_human"),
        (["--agent"], "model_tool_call=get_quote"),
        (["--recovery"], "replayed_side_effects=0"),
    ],
)
def test_offline_demo_modes(arguments, marker, capsys):
    assert main(arguments) == 0
    assert marker in capsys.readouterr().out
