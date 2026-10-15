"""受控记忆只接服务器已确认成交，记忆失败不能伪装执行失败或再次下单。"""

from fastapi.testclient import TestClient

from ai_trader.domain.trading import OrderStatus
from ai_trader.paper_service import create_paper_app


def test_paper_emits_business_confirmation_only_after_completed_fill(tmp_path):
    received = []

    def sink(run_id, order):
        received.append((run_id, order))

    with TestClient(create_paper_app(state_directory=tmp_path, confirmed_event_sink=sink),
                    client=('127.0.0.1', 50000)) as client:
        run_id = client.post('/runs', json={'goal': '分析'}).json()['run_id']
        assert client.post(f'/runs/{run_id}/analyze').status_code == 200
        assert received == []
        response = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'local'})
        assert response.status_code == 200, response.text
        assert received[0][0] == run_id
        assert received[0][1].status is OrderStatus.FILLED
        assert client.get(f'/runs/{run_id}').json()['state'] == 'succeeded'


def test_memory_failure_is_audited_without_reexecuting_confirmed_trade(tmp_path):
    attempts = []

    def sink(run_id, order):
        attempts.append((run_id, order.order_id))
        raise TimeoutError('fixture memory unavailable')

    with TestClient(create_paper_app(state_directory=tmp_path, confirmed_event_sink=sink),
                    client=('127.0.0.1', 50000)) as client:
        run_id = client.post('/runs', json={'goal': '分析'}).json()['run_id']
        client.post(f'/runs/{run_id}/analyze')
        first = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'local'})
        assert first.status_code == 200, first.text
        detail = client.get(f'/runs/{run_id}').json()
        assert detail['state'] == 'succeeded'
        events = client.get(f'/runs/{run_id}/events').json()
        assert any(event['payload'].get('error') == 'memory_write_failed' for event in events)
        reconciled = client.post(f'/runs/{run_id}/reconcile', headers={'X-Reviewer-Id': 'local'})
        assert reconciled.status_code == 200
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert first.json()['order']['order_id'] == reconciled.json()['order']['order_id']


def test_confirmed_fill_is_persisted_once_and_isolated_on_later_task(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from datetime import UTC, datetime
    from pathlib import Path
    from types import SimpleNamespace

    import pytest

    from ai_trader.memory.business import make_paper_memory_sink
    from ai_trader.memory.lifecycle import ControlledMemory

    chromadb = pytest.importorskip('chromadb')

    class Embedding:
        def is_legacy(self):
            return True

        def __call__(self, input):
            return [[1., 0., 0.] for _ in input]

        def embed_query(self, input):
            return self(input)

        def name(self):
            return 'paper-memory-fixture'

    monkeypatch.setitem(sys.modules, 'config', SimpleNamespace(DATA_DIR=str(tmp_path)))
    source = Path(__file__).parents[2] / 'rag_store.py'
    spec = importlib.util.spec_from_file_location('paper_memory_rag', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = module.RagStore(
        client=chromadb.PersistentClient(path=str(tmp_path / 'chroma'),
            settings=chromadb.Settings(anonymized_telemetry=False)),
        embedding_function=Embedding(),
    )
    now = datetime(2026, 10, 14, tzinfo=UTC)
    writer = ControlledMemory(store, user_id='alice',
        permissions=frozenset({'memory:read', 'memory:write'}), clock=lambda: now)
    other = ControlledMemory(store, user_id='bob',
        permissions=frozenset({'memory:read'}), clock=lambda: now)
    sink = make_paper_memory_sink(writer, user_id='alice')
    with TestClient(create_paper_app(state_directory=tmp_path / 'paper', clock=lambda: now,
                                     confirmed_event_sink=sink),
                    client=('127.0.0.1', 50000)) as client:
        run_id = client.post('/runs', json={'goal': '不应进入事实记忆的模型目标'}).json()['run_id']
        client.post(f'/runs/{run_id}/analyze')
        first = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'local'})
        replay = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'local'})
        assert first.status_code == replay.status_code == 200
        assert first.json()['order']['order_id'] == replay.json()['order']['order_id']

    records = store.get_memory_records()
    assert len(records) == 1
    assert records[0]['metadata']['source_run'] == run_id
    assert records[0]['metadata']['outcome'] == 'paper_filled'
    assert '不应进入事实记忆' not in records[0]['document']
    assert len(writer.search('600519成交')) == 1
    assert other.search('600519成交') == []
