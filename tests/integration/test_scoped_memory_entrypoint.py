"""production装配的记忆身份由可信调用方绑定，无默认全局记忆权限。"""

from unittest.mock import patch

import pytest

from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelResponse, ModelToolCall, ModelUsage
from ai_trader.runtime import build_production_runner
from ai_trader.settings import Settings


def _settings(tmp_path):
    return Settings(
        _env_file=None, runtime_mode='production', anthropic_api_key='fixture-only',
        runtime_database_url=f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}",
    )


def test_reader_is_bound_by_server_and_runner_uses_memory_observation(tmp_path):
    calls = []

    def reader(query, top_k=3):
        calls.append((query, top_k))
        return [{'content': '历史Paper订单已确认，不能覆盖当前风控',
                 'authority': 'historical_context_only', 'user_id': 'owner-a'}]

    provider = FakeModelProvider([
        ModelResponse(
            provider='fake', model='fixture', tool_calls=(ModelToolCall(
                call_id='read-memory', name='rag_search', arguments={'query': '历史订单', 'top_k': 2},
            ),), usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0,
        ),
        ModelResponse(provider='fake', model='fixture', text='已读取历史观察',
                      usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0),
    ])
    with patch('ai_trader.runtime.AnthropicProvider.from_settings', return_value=provider):
        runner, repository = build_production_runner(
            _settings(tmp_path), memory_owner='owner-a', memory_reader=reader,
        )
    try:
        result = runner.run('分析历史证据')
        assert result.observation.final_outcome == 'succeeded'
        assert calls == [('历史订单', 2)]
        assert 'historical_context_only' in str(provider.requests[1].messages)
    finally:
        repository.close()


def test_no_owner_rejects_a_reader_and_does_not_grant_memory_permission(tmp_path):
    with pytest.raises(ValueError, match='owner'):
        build_production_runner(_settings(tmp_path), memory_reader=lambda *a, **kw: [])

    provider = FakeModelProvider([
        ModelResponse(provider='fake', model='fixture', text='ok',
                      usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0),
    ])
    with patch('ai_trader.runtime.AnthropicProvider.from_settings', return_value=provider):
        runner, repository = build_production_runner(_settings(tmp_path))
    try:
        runner.run('普通分析')
        assert 'rag_search' not in {schema['name'] for schema in provider.requests[0].tools}
    finally:
        repository.close()
