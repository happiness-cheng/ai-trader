"""离线测试用的确定性队列模型提供方。"""

from collections import deque

from ai_trader.models.gateway import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
)


class FakeModelProvider:
    name = "fake"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise ProviderError(
                "fake response queue is empty",
                retryable=False,
                code=ProviderErrorCode.INVALID_RESPONSE,
            )
        return self._responses.popleft()
