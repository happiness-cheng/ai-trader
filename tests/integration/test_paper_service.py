"""完整服务装配的离线 HTTP 验收，不访问模型或行情网络。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.models.fake import FakeModelProvider
from ai_trader.models.gateway import ModelResponse, ModelToolCall, ModelUsage
from ai_trader.paper_service import create_paper_app


def test_fixture_http_flow_requires_approval_and_has_one_order(tmp_path):
    app = create_paper_app(state_directory=tmp_path / 'service')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        assert client.get('/health').json() == {'status': 'ok', 'live_execution': False}
        created = client.post('/runs', json={'goal': '根据离线行情提出 Paper 建议'})
        assert created.status_code == 201
        run_id = created.json()['run_id']
        analyzed = client.post(f'/runs/{run_id}/analyze')
        assert analyzed.status_code == 200
        assert analyzed.json()['run']['state'] == 'waiting_approval'
        assert analyzed.json()['proposal']['evidence_refs'] == ['get_quote']
        assert not (tmp_path / 'service' / 'paper-ledger.json').exists()
        assert client.post(f'/runs/{run_id}/approve').status_code == 401
        approved = client.post(
            f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'test-reviewer'},
        )
        assert approved.status_code == 200
        assert approved.json()['run']['state'] == 'succeeded'
        assert approved.json()['order']['status'] == 'filled'
        events = client.get(f'/runs/{run_id}/events').json()
        assert any(event['payload'].get('tool') == 'get_quote' for event in events)


def _analyze(client):
    created = client.post('/runs', json={'goal': 'fixture Paper 分析'})
    assert created.status_code == 201
    run_id = created.json()['run_id']
    response = client.post(f'/runs/{run_id}/analyze')
    assert response.status_code == 200
    return run_id, response.json()


def test_repeated_approval_returns_same_order_and_rejection_is_terminal(tmp_path):
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        run_id, _ = _analyze(client)
        headers = {'X-Reviewer-Id': 'test-reviewer'}
        first = client.post(f'/runs/{run_id}/approve', headers=headers)
        repeated = client.post(f'/runs/{run_id}/approve', headers=headers)
        assert repeated.status_code == 200
        assert repeated.json()['order'] == first.json()['order']
        # 新目录保证拒绝测试不受已成交持仓影响。
    with TestClient(create_paper_app(state_directory=tmp_path / 'rejection'),
                    client=('127.0.0.1', 50000)) as client:
        run_id, _ = _analyze(client)
        rejected = client.post(f'/runs/{run_id}/reject', headers=headers, json={'reason': '不执行'})
        assert rejected.status_code == 200
        assert rejected.json()['state'] == 'cancelled'
        assert client.post(f'/runs/{run_id}/approve', headers=headers).status_code == 409
        assert not (tmp_path / 'rejection' / 'paper-ledger.json').exists()
    assert len(json.loads((tmp_path / 'paper-ledger.json').read_text(encoding='utf-8'))) == 1


def test_approval_rejects_caller_risk_fields(tmp_path):
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        run_id, _ = _analyze(client)
        response = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'test-reviewer'},
            json={'trading_enabled': True, 'account': {'available_cash': '999999999'}})
        assert response.status_code == 422
        assert client.get(f'/runs/{run_id}').json()['state'] == 'waiting_approval'
        assert not (tmp_path / 'paper-ledger.json').exists()


def test_server_risk_uses_held_paper_positions(tmp_path):
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        run_id, _ = _analyze(client)
        assert client.post(f'/runs/{run_id}/approve',
                           headers={'X-Reviewer-Id': 'test-reviewer'}).status_code == 200
        _, analyzed = _analyze(client)
        assert analyzed['run']['state'] == 'failed'
        assert 'POSITION_LIMIT_EXCEEDED' in analyzed['risk']['codes']
        assert len(json.loads((tmp_path / 'paper-ledger.json').read_text(encoding='utf-8'))) == 1


def test_service_rejects_non_loopback_clients(tmp_path):
    with TestClient(create_paper_app(state_directory=tmp_path), client=('192.0.2.10', 50000)) as client:
        assert client.get('/health').status_code == 403


def test_state_directory_has_one_writer_and_lifecycle_releases_it(tmp_path):
    with (
        TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)),
        pytest.raises(RuntimeError, match='writer'),
        TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)),
    ):
        pass
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        assert client.get('/health').status_code == 200


def test_factory_rejects_implicit_directory_and_partial_provider_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='absolute'):
        create_paper_app(state_directory=Path('.'))
    with pytest.raises(ValueError, match='provider.*model'):
        create_paper_app(state_directory=tmp_path / 'not-created', model='external')
    assert not (tmp_path / 'not-created').exists()


def test_response_loss_restarts_and_reconciles_without_a_second_order(tmp_path, monkeypatch):
    original_submit = PaperBrokerAdapter.submit

    def lose_response(broker, proposal, decision):
        original_submit(broker, proposal, decision)
        raise TimeoutError('test: Paper persisted order but HTTP response was lost')

    headers = {'X-Reviewer-Id': 'test-reviewer'}
    monkeypatch.setattr(PaperBrokerAdapter, 'submit', lose_response)
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        run_id, _ = _analyze(client)
        response = client.post(f'/runs/{run_id}/approve', headers=headers)
        assert response.status_code == 409
        assert 'unknown' in response.json()['detail']
        assert client.get(f'/runs/{run_id}').json()['state'] == 'executing'
        assert client.post(f'/runs/{run_id}/approve', headers=headers).status_code == 409
    monkeypatch.setattr(PaperBrokerAdapter, 'submit', original_submit)
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        recovered = client.post(f'/runs/{run_id}/reconcile', headers=headers)
        assert recovered.status_code == 200
        assert recovered.json()['run']['state'] == 'succeeded'
        order = recovered.json()['order']
        assert client.post(f'/runs/{run_id}/approve', headers=headers).json()['order'] == order
        assert client.post(f'/runs/{run_id}/reconcile', headers=headers).json()['order'] == order
    assert len(json.loads((tmp_path / 'paper-ledger.json').read_text(encoding='utf-8'))) == 1


def test_waiting_approval_survives_service_restart(tmp_path):
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        run_id, _ = _analyze(client)
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        approved = client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'test-reviewer'})
        assert approved.status_code == 200
        assert approved.json()['run']['state'] == 'succeeded'


def test_cli_requires_explicit_state_directory_and_loopback_host(tmp_path, capsys):
    from ai_trader.paper_service import main

    with pytest.raises(SystemExit) as missing:
        main([])
    assert missing.value.code == 2
    assert '--state-directory' in capsys.readouterr().err
    with pytest.raises(SystemExit) as unsafe:
        main(['--state-directory', str(tmp_path / 'not-created'), '--host', '0.0.0.0'])
    assert unsafe.value.code == 2
    assert 'loopback' in capsys.readouterr().err
    assert not (tmp_path / 'not-created').exists()


def test_cli_starts_one_loopback_server_without_proxy_trust(tmp_path, monkeypatch):
    import uvicorn

    from ai_trader.paper_service import main

    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    assert main(['--state-directory', str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0][1] == {'host': '127.0.0.1', 'port': 8000, 'workers': 1, 'proxy_headers': False}


def test_injected_provider_uses_the_same_runner_and_only_fixture_read_tools(tmp_path):
    provider = FakeModelProvider([ModelResponse(
        provider='test-script', model='test-model',
        text=json.dumps({'action': 'no_trade', 'reason': 'fixture evidence is insufficient'}),
        usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0,
    )])
    app = create_paper_app(state_directory=tmp_path, provider=provider, model='test-model')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        _, analyzed = _analyze(client)
        assert analyzed['run']['state'] == 'succeeded'
        assert analyzed['proposal'] is None
    assert provider.requests[0].model == 'test-model'
    assert [tool['name'] for tool in provider.requests[0].tools] == ['get_quote']
    assert provider.requests[0].tools[0]['effect'] == 'read'
    assert not (tmp_path / 'paper-ledger.json').exists()


def test_concurrent_approval_rechecks_account_after_prior_paper_fill(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        first, _ = _analyze(client)
        second, _ = _analyze(client)

        def approve(run_id):
            return client.post(f'/runs/{run_id}/approve', headers={'X-Reviewer-Id': 'test-reviewer'})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(approve, [first, second]))
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert sorted(client.get(f'/runs/{run_id}').json()['state'] for run_id in [first, second]) == [
            'failed', 'succeeded',
        ]
    assert len(json.loads((tmp_path / 'paper-ledger.json').read_text(encoding='utf-8'))) == 1


def test_model_input_uses_server_account_snapshot_scoped_to_current_run(tmp_path):
    provider = FakeModelProvider([
        ModelResponse(provider='fake', model='fixture', tool_calls=(ModelToolCall(
            call_id='quote', name='get_quote', arguments={'symbol': '600519'},
        ),), usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0),
        ModelResponse(provider='fake', model='fixture', text=json.dumps({'action': 'propose', 'trade': {
            'symbol': '600519', 'side': 'buy', 'quantity': 100, 'limit_price': '100',
            'stop_loss': '95', 'take_profit': '115', 'evidence_refs': ['get_quote'],
        }}), usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0),
        ModelResponse(provider='fake', model='fixture',
            text=json.dumps({'action': 'no_trade', 'reason': 'fixture second analysis'}),
            usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0),
    ])
    with TestClient(create_paper_app(state_directory=tmp_path, provider=provider, model='fixture'),
                    client=('127.0.0.1', 50000)) as client:
        first_run_id, _ = _analyze(client)
        assert client.post(f'/runs/{first_run_id}/approve',
                           headers={'X-Reviewer-Id': 'test-reviewer'}).status_code == 200
        second_run_id, _ = _analyze(client)

    def snapshot(request):
        content = request.system
        assert '[business_instructions]' in content
        assert '最终只输出 JSON' in content
        assert '[server_constraints]' in content
        return json.loads(content.split('[server_constraints]\n', 1)[1].split('\n[context_status]', 1)[0])

    first = snapshot(provider.requests[0])
    second = snapshot(provider.requests[2])
    assert first['run_id'] == first_run_id and second['run_id'] == second_run_id
    assert first['account']['available_cash'] == '50000'
    assert second['account']['available_cash'] == '40000'
    assert second['positions'][0]['quantity'] == 100
    assert first['execution_unknown'] is False
    assert first['current_proposal'] is None and first['risk_status'] == 'not_evaluated'
    assert first_run_id not in json.dumps(second)


def test_server_snapshot_unavailable_fails_before_model_call(tmp_path, monkeypatch):
    provider = FakeModelProvider([ModelResponse(
        provider='fake', model='fixture',
        text=json.dumps({'action': 'no_trade', 'reason': 'must not be called'}),
        usage=ModelUsage(input_tokens=1, output_tokens=1), attempts=1, elapsed_ms=0,
    )])

    def unavailable(_broker):
        raise TimeoutError('test fixture ledger source unavailable')

    with TestClient(create_paper_app(state_directory=tmp_path, provider=provider, model='fixture'),
                    client=('127.0.0.1', 50000)) as client:
        created = client.post('/runs', json={'goal': 'fixture analysis'}).json()
        before = client.get(f"/runs/{created['run_id']}").json()['state']
        monkeypatch.setattr(PaperBrokerAdapter, 'list_orders', unavailable)
        response = client.post(f"/runs/{created['run_id']}/analyze")
        assert response.status_code == 503
        assert 'state unavailable' in response.json()['detail']
        assert client.get(f"/runs/{created['run_id']}").json()['state'] == before
    assert provider.requests == []
