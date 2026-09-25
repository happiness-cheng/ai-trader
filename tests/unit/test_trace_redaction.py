from copy import deepcopy

from ai_trader.observability.redaction import redact_payload


def test_redacts_nested_sensitive_keys_without_mutating_input():
    payload = {
        "api_key": "secret-value",
        "nested": {"Authorization": "Bearer abcdef123456", "safe": "visible"},
        "items": [{"password": "p"}],
    }
    original = deepcopy(payload)
    result = redact_payload(payload)
    assert result["api_key"] == "[REDACTED]"
    assert result["nested"]["Authorization"] == "[REDACTED]"
    assert result["nested"]["safe"] == "visible"
    assert result["items"][0]["password"] == "[REDACTED]"
    assert payload == original


def test_redacts_credentials_embedded_in_strings():
    payload = {
        "message": (
            "Bearer abcdef1234567890 "
            "https://open.feishu.cn/open-apis/bot/v2/hook/"
            + "11111111-2222-3333-4444-555555555555"
        )
    }
    result = redact_payload(payload)
    assert "abcdef" not in result["message"]
    assert "11111111" not in result["message"]
    assert result["message"].count("[REDACTED]") == 2


def test_bounds_untrusted_strings():
    result = redact_payload({"tool_output": "x" * 5000}, max_string_length=100)
    assert len(result["tool_output"]) == 100
