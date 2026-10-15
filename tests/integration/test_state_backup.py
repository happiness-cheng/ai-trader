import json

import pytest
from fastapi.testclient import TestClient

from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.paper_service import create_paper_app
from ai_trader.state_backup import backup_state, restore_state


def test_backup_restores_paper_order_without_duplicate_fill(tmp_path):
    source, backup, target = (tmp_path / name for name in ('source', 'backup', 'restored'))
    headers = {'X-Reviewer-Id': 'test'}
    with TestClient(create_paper_app(state_directory=source), client=('127.0.0.1', 50000)) as client:
        run_id = client.post('/runs', json={'goal': 'fixture'}).json()['run_id']
        assert client.post(f'/runs/{run_id}/analyze').status_code == 200
        approved = client.post(f'/runs/{run_id}/approve', headers=headers).json()
        with pytest.raises(RuntimeError, match='writer'):
            backup_state(source, backup)
        assert not backup.exists()
    backup_state(source, backup)
    assert set(json.loads((backup / 'manifest.json').read_text())['files']) == {
        'workflow.db', 'paper-ledger.json',
    }
    restore_state(backup, target)
    with TestClient(create_paper_app(state_directory=target), client=('127.0.0.1', 50000)) as client:
        assert client.get('/ready').status_code == 200
        assert client.post(f'/runs/{run_id}/reconcile', headers=headers).json()['order'] == approved['order']
        assert client.post(f'/runs/{run_id}/approve', headers=headers).json()['order'] == approved['order']
    assert len(json.loads((target / 'paper-ledger.json').read_text())) == 1


def _empty_backup(source, backup):
    with TestClient(create_paper_app(state_directory=source), client=('127.0.0.1', 50000)):
        pass
    backup_state(source, backup)


def test_corrupt_backup_is_rejected_before_target_creation(tmp_path):
    backup, target = tmp_path / 'backup', tmp_path / 'target'
    _empty_backup(tmp_path / 'source', backup)
    (backup / 'workflow.db').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash'):
        restore_state(backup, target)
    assert not target.exists()


def test_manifest_path_escape_is_rejected(tmp_path):
    backup, target = tmp_path / 'backup', tmp_path / 'target'
    _empty_backup(tmp_path / 'source', backup)
    manifest = backup / 'manifest.json'
    data = json.loads(manifest.read_text())
    data['files']['../private'] = '0' * 64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='manifest'):
        restore_state(backup, target)
    assert not target.exists()


def test_restore_never_overwrites_existing_state(tmp_path):
    backup, target = tmp_path / 'backup', tmp_path / 'target'
    _empty_backup(tmp_path / 'source', backup)
    target.mkdir()
    original = target / 'workflow.db'
    original.write_bytes(b'USER_DATA')
    with pytest.raises(ValueError, match='empty'):
        restore_state(backup, target)
    assert original.read_bytes() == b'USER_DATA'


def test_restore_unknown_execution_reconciles_existing_broker_order(tmp_path, monkeypatch):
    source, backup, target = (tmp_path / name for name in ('source', 'backup', 'target'))
    original_submit = PaperBrokerAdapter.submit
    def lose_response(broker, proposal, decision):
        original_submit(broker, proposal, decision)
        raise TimeoutError('fixture response loss')
    monkeypatch.setattr(PaperBrokerAdapter, 'submit', lose_response)
    headers = {'X-Reviewer-Id': 'test'}
    with TestClient(create_paper_app(state_directory=source), client=('127.0.0.1', 50000)) as client:
        run_id = client.post('/runs', json={'goal': 'fixture'}).json()['run_id']
        client.post(f'/runs/{run_id}/analyze')
        assert client.post(f'/runs/{run_id}/approve', headers=headers).status_code == 409
        assert client.get(f'/runs/{run_id}').json()['state'] == 'executing'
    monkeypatch.setattr(PaperBrokerAdapter, 'submit', original_submit)
    backup_state(source, backup)
    restore_state(backup, target)
    with TestClient(create_paper_app(state_directory=target), client=('127.0.0.1', 50000)) as client:
        assert client.post(f'/runs/{run_id}/approve', headers=headers).status_code == 409
        assert client.post(f'/runs/{run_id}/reconcile', headers=headers).json()['run']['state'] == 'succeeded'
    assert len(json.loads((target / 'paper-ledger.json').read_text())) == 1


def test_backup_destination_created_during_source_lock_is_never_overwritten(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    from ai_trader import state_backup

    source, target = tmp_path / 'source', tmp_path / 'destination'
    with TestClient(create_paper_app(state_directory=source), client=('127.0.0.1', 50000)):
        pass
    @contextmanager
    def race(_directory):
        target.mkdir()
        with sqlite3.connect(target / 'workflow.db') as db:
            db.execute('CREATE TABLE user_owned (value TEXT)')
            db.execute("INSERT INTO user_owned VALUES ('USER_DATA')")
        yield
    monkeypatch.setattr(state_backup, '_single_writer', race)
    with pytest.raises(FileExistsError):
        backup_state(source, target)
    with sqlite3.connect(target / 'workflow.db') as db:
        assert db.execute('SELECT value FROM user_owned').fetchone() == ('USER_DATA',)


def test_restore_detects_source_mutation_during_copy(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from ai_trader import state_backup

    backup, target = tmp_path / 'backup', tmp_path / 'target'
    _empty_backup(tmp_path / 'source', backup)
    @contextmanager
    def race(directory):
        directory.mkdir()
        (backup / 'workflow.db').write_bytes(b'changed after validation')
        yield
    monkeypatch.setattr(state_backup, '_single_writer', race)
    with pytest.raises(ValueError, match='hash'):
        restore_state(backup, target)
