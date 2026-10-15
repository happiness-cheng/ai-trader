"""工具返回超时后仍运行的handler必须继续占用全进程容量。"""
from threading import Event, Lock
from time import monotonic, sleep

from pydantic import BaseModel

from ai_trader.tools.runtime import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRuntime,
)


class Input(BaseModel):
    pass


class Output(BaseModel):
    value: int


def test_timeout_retains_capacity_across_runtime_instances():
    release, drained = Event(), Event()
    lock = Lock()
    active = 0
    started = 0

    def handler(_data):
        nonlocal active, started
        with lock:
            active += 1
            started += 1
        try:
            release.wait(2)
            return Output(value=1)
        finally:
            with lock:
                active -= 1
                if active == 0:
                    drained.set()

    def runtime():
        result = ToolRuntime()
        result.register(ToolDefinition(name='blocked', description='fixture', input_model=Input,
                                      output_model=Output, handler=handler, permission='read', timeout_seconds=.02))
        return result

    context = ToolExecutionContext(permissions=frozenset({'read'}))
    first, second = runtime(), runtime()
    try:
        results = [first.execute(ToolCall(call_id=f'call-{i}', name='blocked', arguments={}), context) for i in range(4)]
        assert all(result.error_code.value == 'timeout' for result in results)
        before = monotonic()
        denied = second.execute(ToolCall(call_id='fifth', name='blocked', arguments={}), context)
        assert denied.error_code.value == 'capacity_unavailable'
        assert monotonic() - before < .2
        assert started == 4
    finally:
        release.set()
        assert drained.wait(2)

    # handler退出与future完成回调之间可有极短窗口；只在公共调用返回容量拒绝时重试。
    deadline = monotonic()+2
    while True:
        recovered = second.execute(ToolCall(call_id='after-finish', name='blocked', arguments={}), context)
        if recovered.ok or monotonic()>=deadline:
            break
        assert recovered.error_code.value == 'capacity_unavailable'
        sleep(.005)
    assert recovered.ok
    assert recovered.output == {'value': 1}


def test_pool_start_failure_returns_capacity_without_exposing_exception(monkeypatch):
    runtime = ToolRuntime()
    runtime.register(ToolDefinition(name='ready', description='fixture', input_model=Input,
                                   output_model=Output, handler=lambda _data: Output(value=1), permission='read'))
    context = ToolExecutionContext(permissions=frozenset({'read'}))

    def unavailable(*_args, **_kwargs):
        raise RuntimeError('PRIVATE_POOL_ERROR')

    with monkeypatch.context() as patch:
        patch.setattr('ai_trader.tools.runtime.ThreadPoolExecutor', unavailable)
        for i in range(8):
            result = runtime.execute(ToolCall(call_id=f'fail-{i}', name='ready', arguments={}), context)
            assert result.error_code.value == 'handler_error'
            assert 'PRIVATE_POOL_ERROR' not in result.error_message
    recovered = runtime.execute(ToolCall(call_id='ready', name='ready', arguments={}), context)
    assert recovered.ok
