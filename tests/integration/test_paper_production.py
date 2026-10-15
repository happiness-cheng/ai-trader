
import pytest
from fastapi.testclient import TestClient

from ai_trader.paper_service import create_paper_app

TOKEN = 'synthetic-test-token-0123456789abcdef'


def test_production_requires_explicit_token_without_creating_state(tmp_path):
    state = tmp_path / 'state'
    with pytest.raises(ValueError, match='token'):
        create_paper_app(state_directory=state, production=True)
    assert not state.exists()


def test_production_authentication_and_full_paper_flow(tmp_path):
    app = create_paper_app(state_directory=tmp_path, production=True, operator_token=TOKEN)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/ready').status_code == 200
        assert client.post('/runs', json={'goal': 'fixture'}).status_code == 401
        assert client.get('/runs/unknown').status_code == 401
        assert client.post('/runs', json={'goal': 'fixture'}, headers={
            'X-Reviewer-Id': 'test', 'Authorization': 'Bearer wrong',
        }).status_code == 401
        client.headers['Authorization'] = f'Bearer {TOKEN}'
        run_id = client.post('/runs', json={'goal': 'fixture'}).json()['run_id']
        assert client.post(f'/runs/{run_id}/analyze').status_code == 200
        approved = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'operator'})
        assert approved.status_code == 200
        assert client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'operator'}).json()['order'] == approved.json()['order']
        assert client.get('/ready').status_code == 200
        (tmp_path / 'paper-ledger.json').write_text('private corrupted ledger', encoding='utf-8')
        failed = client.get('/ready')
        assert failed.status_code == 503
        assert 'private corrupted' not in failed.text
        assert client.get('/health').status_code == 200
    # 故障注入只作用于临时测试状态。


def test_readiness_before_start_is_not_ready(tmp_path):
    client = TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000))
    assert client.get('/ready').status_code == 503
    assert not (tmp_path / 'workflow.db').exists()


def test_sql_failure_is_not_ready(tmp_path):
    import sqlite3

    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        with sqlite3.connect(tmp_path / 'workflow.db') as db:
            db.execute('DROP TABLE run_events')
        assert client.get('/ready').status_code == 503
