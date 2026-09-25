"""写入 Trace 前的递归脱敏与输出限长。"""

import re
from collections.abc import Mapping


_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|token|password|secret|webhook)", re.IGNORECASE
)
_WEBHOOK = re.compile(
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-f-]{20,}",
    re.IGNORECASE,
)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE)


def _redact_value(value: object, max_string_length: int) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _SENSITIVE_KEY.search(str(key))
                else _redact_value(item, max_string_length)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, max_string_length) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, max_string_length) for item in value)
    if isinstance(value, str):
        redacted = _WEBHOOK.sub("[REDACTED]", value)
        redacted = _BEARER.sub("[REDACTED]", redacted)
        return redacted[:max_string_length]
    return value


def redact_payload(
    payload: Mapping[str, object], *, max_string_length: int = 2000
) -> dict[str, object]:
    if max_string_length < 1:
        raise ValueError("max string length must be positive")
    result = _redact_value(payload, max_string_length)
    assert isinstance(result, dict)
    return result
